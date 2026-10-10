"""Append-only, keyed integrity chain for case audit events."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import threading
from pathlib import Path


def _canonical(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


class AuditChain:
    def __init__(self, path: Path, key: bytes):
        self.path = Path(path)
        self.key = bytes(key)
        if len(self.key) < 32:
            raise ValueError("Audit integrity keys must contain at least 256 bits")
        self._lock = threading.Lock()

    def _read(self) -> list[dict]:
        if not self.path.is_file():
            return []
        records = []
        for line in self.path.read_text(encoding="utf-8", errors="strict").splitlines():
            records.append(json.loads(line))
        return records

    def append(self, record: dict) -> dict:
        with self._lock:
            try:
                existing = self._read()
            except (OSError, ValueError) as exc:
                raise RuntimeError("Audit log integrity is invalid; refusing to append") from exc
            if existing and all(not item.get("integrity") for item in existing):
                existing = self._seal_legacy(existing)
            elif any(not item.get("integrity") for item in existing):
                raise RuntimeError("Audit log mixes sealed and unsealed records; refusing to append")
            errors = self._verify_records(existing)
            if errors:
                raise RuntimeError("Audit log integrity is invalid; refusing to append: " + errors[0])
            previous = existing[-1] if existing else {}
            previous_integrity = str(previous.get("integrity") or "")
            value = dict(record)
            value["sequence"] = int(previous.get("sequence") or len(existing)) + 1
            value["previous_integrity"] = previous_integrity
            value["integrity_algorithm"] = "HMAC-SHA256"
            value["integrity"] = hmac.new(self.key, _canonical(value), hashlib.sha256).hexdigest()
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            return value

    def _seal_legacy(self, records: list[dict]) -> list[dict]:
        """Put pre-v0.9.6 records under the new chain without rewriting content.

        This proves their state from migration forward; it cannot prove what
        happened before the first keyed seal, so each migrated record is marked.
        """
        sealed: list[dict] = []
        previous = ""
        for sequence, original in enumerate(records, 1):
            value = {key: item for key, item in original.items()
                     if key not in {"sequence", "previous_integrity", "integrity_algorithm", "integrity"}}
            value["legacy_unsealed_origin"] = True
            value["sequence"] = sequence
            value["previous_integrity"] = previous
            value["integrity_algorithm"] = "HMAC-SHA256"
            value["integrity"] = hmac.new(self.key, _canonical(value), hashlib.sha256).hexdigest()
            previous = value["integrity"]
            sealed.append(value)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            for value in sealed:
                handle.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n")
            handle.flush(); os.fsync(handle.fileno())
        temporary.replace(self.path)
        return sealed

    def verify(self) -> dict:
        try:
            records = self._read()
        except (OSError, ValueError) as exc:
            return {"valid": False, "events": 0, "errors": [str(exc)], "head": ""}
        errors = self._verify_records(records)
        previous = str(records[-1].get("integrity") or "") if records else ""
        return {"valid": not errors, "events": len(records), "errors": errors,
                "head": previous, "algorithm": "HMAC-SHA256",
                "legacy_sealed_events": sum(bool(item.get("legacy_unsealed_origin")) for item in records)}

    def anchor_status(self, sequence: int, integrity: str) -> str:
        """Return current, extended, or mismatch for an externally stored head."""
        try:
            records = self._read()
        except (OSError, ValueError):
            return "mismatch"
        if self._verify_records(records):
            return "mismatch"
        if sequence == 0 and not integrity:
            return "extended" if records else "current"
        if sequence < 1 or len(records) < sequence:
            return "mismatch"
        if str(records[sequence - 1].get("integrity") or "") != str(integrity):
            return "mismatch"
        return "current" if len(records) == sequence else "extended"

    def _verify_records(self, records: list[dict]) -> list[str]:
        errors: list[str] = []
        previous = ""
        expected_sequence = 1
        for line_number, record in enumerate(records, 1):
            integrity = str(record.get("integrity") or "")
            supplied = dict(record)
            supplied.pop("integrity", None)
            expected = hmac.new(self.key, _canonical(supplied), hashlib.sha256).hexdigest()
            if int(record.get("sequence") or 0) != expected_sequence:
                errors.append(f"line {line_number}: sequence discontinuity")
            if str(record.get("previous_integrity") or "") != previous:
                errors.append(f"line {line_number}: previous hash mismatch")
            if not hmac.compare_digest(integrity, expected):
                errors.append(f"line {line_number}: integrity mismatch")
            previous = integrity
            expected_sequence += 1
        return errors
