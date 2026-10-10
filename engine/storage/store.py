"""Encrypted local store for PAi Health.

Layout of a vault directory:
    vault.key    passphrase-wrapped data key (keys.py)
    health.db    SQLite; every payload AES-256-GCM encrypted
    audit.log    HMAC-SHA256 append-only chain (audit.py, from PAi Legal)

What is readable in health.db without the passphrase: row sequence numbers,
record kind (DOCUMENT, USER_LOG, ...), and opaque keyed tags. Nothing else —
no names, values, dates, file names or document hashes.

Rows are bound to their place: the GCM associated data is
vault_id|id|kind|ref|supersedes, so a ciphertext copied to another row, another
kind or another vault fails to decrypt.

Records are append-only. A correction is a new record that supersedes the old
one; the old one stays readable (§ copy-forward: history is never rewritten).
The only destructive operation is purge(), for the user's right to remove
their own data; it is audited, and SQLite secure_delete zeroes freed pages.

The audit log holds ids, kinds and ciphertext hashes only — never content.
verify() checks the chain and cross-checks every row against it, so a row
inserted, altered or removed outside this module is reported.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import struct
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from . import keys as vault_keys
from .audit import AuditChain

SCHEMA_VERSION = 1
KEY_FILE, DB_FILE, AUDIT_FILE = "vault.key", "health.db", "audit.log"
MAX_PAYLOAD = 256 * 1024 * 1024

KINDS = frozenset({
    "DOCUMENT",        # original bytes of an imported record (PDF, FHIR, ...)
    "DOCUMENT_TEXT",   # canonical text + page map produced by ingest
    "DEVICE_BATCH",    # normalized samples from one device import
    "USER_LOG",        # dose taken, reading, meal, symptom — first-person entries
    "QUESTION",        # a question the user or a clinician opened (§9)
    "SETTING",         # §29 user settings
    "ENGINE_OUTPUT",   # cached engine output keyed by its input signature
    "AI_CONFIG",       # external provider, model, API key and what it may see (§17)
    "INGEST_CHECK",    # double-check report for one document (review/check.py)
    "INGEST_RESOLUTION",  # a person's decision on one check finding
    "INVESTIGATION",   # an AI investigation: every step, proposals (MODEL_PROPOSAL), checked answer
    "DEVICE_SAMPLES",  # compressed sample rows of one measure from a device export (tracker/device_store.py)
    "DEVICE_IMPORT",   # the report of one device export import
    "DEVICE_PAIRING",  # a paired phone: its key, last sequence number, last sync (companion/service.py)
    "BILLING_PROFILE",  # a person's confirmation of which billing layout a document is (billing/)
})

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS records (
    seq        INTEGER PRIMARY KEY AUTOINCREMENT,
    id         TEXT NOT NULL UNIQUE,
    kind       TEXT NOT NULL,
    ref        TEXT,
    supersedes TEXT,
    nonce      BLOB NOT NULL,
    ciphertext BLOB NOT NULL
);
CREATE INDEX IF NOT EXISTS records_kind_ref ON records(kind, ref);
CREATE INDEX IF NOT EXISTS records_supersedes ON records(supersedes);
"""


class StoreError(Exception):
    pass


class IntegrityError(StoreError):
    """A row failed authentication (altered, moved, or from another vault)."""


@dataclass(frozen=True)
class Record:
    id: str
    kind: str
    created_at: str
    meta: dict
    body: object          # dict/list for JSON records, bytes for binary
    supersedes: Optional[str] = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _pack(header: dict, body: bytes) -> bytes:
    h = json.dumps(header, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return struct.pack(">I", len(h)) + h + body


def _unpack(blob: bytes) -> tuple[dict, bytes]:
    (n,) = struct.unpack(">I", blob[:4])
    return json.loads(blob[4:4 + n]), blob[4 + n:]


class Vault:
    """Open with Vault.create(dir, passphrase) or Vault.open(dir, passphrase)."""

    def __init__(self, directory: Path, k: vault_keys.VaultKeys):
        self.dir = Path(directory)
        self._k = k
        self._aead = AESGCM(k.record)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(self.dir / DB_FILE, check_same_thread=False, isolation_level=None)
        try:
            self._init_db(k)
        except BaseException:
            self._db.close()        # Windows can't delete or replace a file that is still open
            raise
        self.audit = AuditChain(self.dir / AUDIT_FILE, k.audit)

    def _init_db(self, k: vault_keys.VaultKeys) -> None:
        self._db.execute("PRAGMA secure_delete=ON")
        self._db.execute("PRAGMA foreign_keys=ON")
        self._db.executescript(_SCHEMA)
        row = self._db.execute("SELECT v FROM meta WHERE k='vault_id'").fetchone()
        if row is None:
            self._db.execute("INSERT INTO meta VALUES ('vault_id', ?), ('schema_version', ?)",
                             (k.vault_id, str(SCHEMA_VERSION)))
        elif row[0] != k.vault_id:
            raise IntegrityError("This database belongs to a different vault key")

    # ---- lifecycle -------------------------------------------------------
    @classmethod
    def create(cls, directory: Path, passphrase: str) -> "Vault":
        directory = Path(directory)
        if (directory / KEY_FILE).exists() or (directory / DB_FILE).exists():
            raise FileExistsError(f"A vault already exists in {directory}")
        directory.mkdir(parents=True, exist_ok=True)
        v = cls(directory, vault_keys.create(directory / KEY_FILE, passphrase))
        v.audit.append({"event": "VAULT_CREATED", "vault_id": v.vault_id, "at": _now(),
                        "schema_version": SCHEMA_VERSION})
        return v

    @classmethod
    def open(cls, directory: Path, passphrase: str) -> "Vault":
        directory = Path(directory)
        if not (directory / KEY_FILE).is_file():
            raise FileNotFoundError(f"No vault in {directory}")
        v = cls(directory, vault_keys.unlock(directory / KEY_FILE, passphrase))
        state = v.audit.verify()
        if not state["valid"]:
            v.close()
            raise IntegrityError("Audit chain is invalid: " + "; ".join(state["errors"][:3]))
        return v

    def change_passphrase(self, old: str, new: str) -> None:
        with self._lock:
            vault_keys.change_passphrase(self.dir / KEY_FILE, old, new)
            self.audit.append({"event": "PASSPHRASE_CHANGED", "at": _now()})

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    @property
    def vault_id(self) -> str:
        return self._k.vault_id

    # ---- keyed tags ------------------------------------------------------
    def tag(self, namespace: str, value: str | bytes) -> str:
        """Opaque, vault-specific lookup tag. Not reversible, not comparable across vaults."""
        data = value if isinstance(value, bytes) else value.encode("utf-8")
        return hmac.new(self._k.index, namespace.encode() + b"\x00" + data, hashlib.sha256).hexdigest()

    # ---- core write/read -------------------------------------------------
    def _aad(self, rid: str, kind: str, ref: Optional[str], supersedes: Optional[str]) -> bytes:
        return "|".join([self.vault_id, rid, kind, ref or "", supersedes or ""]).encode()

    def put(self, kind: str, body, *, ref: Optional[str] = None, meta: Optional[dict] = None,
            supersedes: Optional[str] = None) -> str:
        if kind not in KINDS:
            raise StoreError(f"Unknown record kind {kind!r}")
        if isinstance(body, (bytes, bytearray)):
            header, raw = {"enc": "bytes"}, bytes(body)
        else:
            header, raw = {"enc": "json"}, json.dumps(body, sort_keys=True, ensure_ascii=False).encode()
        if len(raw) > MAX_PAYLOAD:
            raise StoreError("Record exceeds the 256 MB limit; split it")
        header.update({"created_at": _now(), "meta": meta or {}})
        rid = f"{kind[:3]}-{secrets.token_hex(10)}"
        with self._lock:
            if supersedes is not None:
                prior = self._db.execute("SELECT kind FROM records WHERE id=?", (supersedes,)).fetchone()
                if prior is None or prior[0] != kind:
                    raise StoreError("supersedes must name an existing record of the same kind")
            nonce = os.urandom(12)
            ct = self._aead.encrypt(nonce, _pack(header, raw), self._aad(rid, kind, ref, supersedes))
            self._db.execute("BEGIN IMMEDIATE")
            try:
                cur = self._db.execute(
                    "INSERT INTO records(id, kind, ref, supersedes, nonce, ciphertext) VALUES (?,?,?,?,?,?)",
                    (rid, kind, ref, supersedes, nonce, ct))
                self.audit.append({"event": "WRITE", "id": rid, "kind": kind, "seq": cur.lastrowid,
                                   "supersedes": supersedes, "ct_sha256": hashlib.sha256(ct).hexdigest(),
                                   "at": header["created_at"]})
                self._db.execute("COMMIT")
            except BaseException:
                self._db.execute("ROLLBACK")
                raise
        return rid

    def _decode(self, row) -> Record:
        rid, kind, ref, supersedes, nonce, ct = row
        try:
            header, raw = _unpack(self._aead.decrypt(nonce, ct, self._aad(rid, kind, ref, supersedes)))
        except InvalidTag as exc:
            raise IntegrityError(f"Record {rid} failed authentication") from exc
        body = raw if header["enc"] == "bytes" else json.loads(raw)
        return Record(rid, kind, header["created_at"], header.get("meta", {}), body, supersedes)

    _COLS = "id, kind, ref, supersedes, nonce, ciphertext"

    def get(self, rid: str) -> Record:
        with self._lock:
            row = self._db.execute(f"SELECT {self._COLS} FROM records WHERE id=?", (rid,)).fetchone()
        if row is None:
            raise KeyError(rid)
        return self._decode(row)

    def iter(self, kind: str, *, ref: Optional[str] = None, current_only: bool = True) -> Iterator[Record]:
        """Records of a kind in write order. current_only hides superseded versions."""
        sql = f"SELECT {self._COLS} FROM records WHERE kind=?"
        args: list = [kind]
        if ref is not None:
            sql += " AND ref=?"
            args.append(ref)
        if current_only:
            sql += " AND id NOT IN (SELECT supersedes FROM records WHERE supersedes IS NOT NULL)"
        with self._lock:
            rows = self._db.execute(sql + " ORDER BY seq", args).fetchall()
        for row in rows:
            yield self._decode(row)

    def history(self, rid: str) -> list[Record]:
        """The record and every version it superseded, oldest first."""
        chain, cur = [], rid
        while cur:
            r = self.get(cur)
            chain.append(r)
            cur = r.supersedes
        return chain[::-1]

    def purge(self, rid: str) -> None:
        """Permanently delete one record (and the versions it superseded). Audited."""
        with self._lock:
            ids = [r.id for r in self.history(rid)]
            if self._db.execute("SELECT 1 FROM records WHERE supersedes=?", (rid,)).fetchone():
                raise StoreError("Purge the newest version; this record has been superseded")
            self._db.execute("BEGIN IMMEDIATE")
            try:
                for i in ids:
                    self._db.execute("DELETE FROM records WHERE id=?", (i,))
                    self.audit.append({"event": "PURGE", "id": i, "at": _now()})
                self._db.execute("COMMIT")
            except BaseException:
                self._db.execute("ROLLBACK")
                raise

    # ---- typed helpers ---------------------------------------------------
    def add_document(self, file_name: str, data: bytes, metadata: Optional[dict] = None) -> tuple[str, bool]:
        """Store original bytes. Same bytes twice -> (existing id, False)."""
        ref = self.tag("document-content", data)
        with self._lock:
            existing = next(self.iter("DOCUMENT", ref=ref), None)
            if existing:
                return existing.id, False
            meta = {"file_name": file_name, "size": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(), **(metadata or {})}
            return self.put("DOCUMENT", data, ref=ref, meta=meta), True

    def put_document_text(self, document_id: str, ingest_result: dict) -> str:
        self.get(document_id)  # must exist
        ref = self.tag("document-text", document_id)
        prior = next(self.iter("DOCUMENT_TEXT", ref=ref), None)
        return self.put("DOCUMENT_TEXT", ingest_result, ref=ref, meta={"document_id": document_id},
                        supersedes=prior.id if prior else None)

    def log(self, entry: dict, supersedes: Optional[str] = None) -> str:
        """A first-person user entry. Corrections supersede; nothing is overwritten."""
        if "type" not in entry:
            raise StoreError("User log entries need a 'type'")
        return self.put("USER_LOG", entry, supersedes=supersedes)

    def set_setting(self, key: str, value, allowed: dict) -> str:
        if key not in allowed or value not in allowed[key]:
            raise ValueError(f"Setting {key}={value!r} is not allowed")
        ref = self.tag("setting", key)
        prior = next(self.iter("SETTING", ref=ref), None)
        if prior and prior.body == {"key": key, "value": value}:
            return prior.id
        return self.put("SETTING", {"key": key, "value": value}, ref=ref,
                        supersedes=prior.id if prior else None)

    def settings(self) -> dict:
        return {r.body["key"]: r.body["value"] for r in self.iter("SETTING")}

    def cache_output(self, signature: str, output: dict) -> str:
        ref = self.tag("engine-output", signature)
        prior = next(self.iter("ENGINE_OUTPUT", ref=ref), None)
        return self.put("ENGINE_OUTPUT", output, ref=ref, supersedes=prior.id if prior else None)

    def cached_output(self, signature: str) -> Optional[dict]:
        r = next(self.iter("ENGINE_OUTPUT", ref=self.tag("engine-output", signature)), None)
        return r.body if r else None

    # ---- integrity -------------------------------------------------------
    def verify(self) -> dict:
        """Audit chain + row-by-row cross-check + decrypt every row."""
        with self._lock:
            chain = self.audit.verify()
            events = self.audit._read() if chain["valid"] else []
            rows = self._db.execute(f"SELECT {self._COLS} FROM records ORDER BY seq").fetchall()
        written = {e["id"]: e for e in events if e.get("event") == "WRITE"}
        purged = {e["id"] for e in events if e.get("event") == "PURGE"}
        problems: list[str] = []
        present = set()
        for row in rows:
            rid, ct = row[0], row[5]
            present.add(rid)
            ev = written.get(rid)
            if ev is None:
                problems.append(f"{rid}: row has no audit record")
            elif ev["ct_sha256"] != hashlib.sha256(ct).hexdigest() or ev["kind"] != row[1]:
                problems.append(f"{rid}: row differs from what was written")
            if rid in purged:
                problems.append(f"{rid}: row was purged but is present")
            try:
                self._decode(row)
            except IntegrityError as exc:
                problems.append(str(exc))
        for rid in written:
            if rid not in present and rid not in purged:
                problems.append(f"{rid}: audited row is missing")
        return {"valid": chain["valid"] and not problems, "audit": chain,
                "records": len(rows), "problems": problems}

    def backup_snapshot(self, destination: Path) -> None:
        """Consistent copy of the database (SQLite online backup API)."""
        with self._lock:
            dst = sqlite3.connect(destination)
            try:
                self._db.backup(dst)
            finally:
                dst.close()
