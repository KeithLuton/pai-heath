"""Device exports into the vault, and back out by measure.

Storage: one DEVICE_SAMPLES record per measure per ≤50,000 samples, zlib-compressed
JSON rows, encrypted like every other record. The lookup tag is per measure, so
the tracker reads only what it needs (a full Apple export can hold millions of
rows). A DEVICE_IMPORT record keeps each import's report.

Row: [start, end, value, unit, source_name, source_app, device_class, method, category,
      conversion, raw_type, time_basis]

Duplicates: a sample already in the vault (same measure, start, end, value,
category, source) is skipped on re-import — overlapping exports are normal.

The export is written to a temporary file only while the importer reads it
(the importers stream from a path) and is deleted immediately after.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import tempfile
import zipfile
import zlib
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Tuple

from devices import apple_health, fitbit, garmin_activities, generic_csv

CHUNK = 50_000
FORMAT = "device-rows-v1"
IMPORTERS = {"apple": apple_health, "fitbit": fitbit, "garmin": garmin_activities, "csv": generic_csv}
LABEL = {"apple": "Apple Health export", "fitbit": "Fitbit / Google Takeout export",
         "garmin": "Garmin Connect activities (CSV)", "csv": "CSV with your column mapping"}


class DeviceImportError(ValueError):
    pass


def detect(name: str, data: bytes) -> Optional[str]:
    """Which importer fits this file, by content. None = not a device export."""
    if data[:4] == b"PK\x03\x04":
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                names = z.namelist()
        except zipfile.BadZipFile:
            return None
        if any(n.endswith("/export.xml") or n == "export.xml" for n in names):
            return "apple"
        if any(fitbit.FILE_RE.search(n) for n in names):
            return "fitbit"
        return None
    head = data[:4096]
    if b"<HealthData" in head:
        return "apple"
    first_line = head.split(b"\n", 1)[0].decode("utf-8", "replace")
    if all(col in first_line for col in garmin_activities.REQUIRED):
        return "garmin"
    return None


def needs(kind: str) -> List[str]:
    return {"apple": [], "fitbit": ["timezone"], "garmin": ["units", "timezone"], "csv": ["mapping"]}[kind]


def _row(s) -> list:
    return [s.start.isoformat(), s.end.isoformat(), s.value, s.unit, s.source_name, s.source_app,
            s.device_class, s.method, s.category, s.conversion, s.provenance.raw_type,
            (s.metadata or {}).get("time_basis")]


def _key(measure: str, r: list) -> bytes:
    return hashlib.blake2b(json.dumps([measure, r[0], r[1], r[2], r[8], r[4]]).encode(), digest_size=12).digest()


def _pack(rows: List[list]) -> bytes:
    return zlib.compress(json.dumps(rows, separators=(",", ":")).encode(), 6)


def _unpack(blob: bytes) -> List[list]:
    return json.loads(zlib.decompress(blob))


def rows_for(vault, measure: str) -> List[list]:
    """All stored rows of one measure: imported chunks plus Bluetooth captures."""
    out: List[list] = []
    for r in vault.iter("DEVICE_SAMPLES", ref=vault.tag("device-measure", measure)):
        out.extend(_unpack(r.body))
    for r in vault.iter("DEVICE_BATCH"):
        for s in r.body.get("samples", []):
            if s["measure"] == measure:
                out.append([s["start"], s["end"], s["value"], s["unit"], s["source_name"], s["source_app"],
                            s["device_class"], s.get("method"), s.get("category"), s.get("conversion"),
                            s["provenance"]["raw_type"], (s.get("metadata") or {}).get("time_basis")])
    out.sort(key=lambda r: r[0])
    return out


def import_bytes(vault, name: str, data: bytes, kind: str, options: dict,
                 progress: Optional[Callable[[str, int, int], None]] = None) -> dict:
    if kind not in IMPORTERS:
        raise DeviceImportError("Unknown export type")
    missing = [k for k in needs(kind) if not options.get(k)]
    if missing:
        raise DeviceImportError(f"{LABEL[kind]} needs: {', '.join(missing)}")
    mod = IMPORTERS[kind]
    workdir = tempfile.mkdtemp(prefix="pai-health-import-")
    path = Path(workdir) / Path(name).name
    mapping_path = Path(workdir) / "mapping.json"
    try:
        path.write_bytes(data)
        args: Tuple = ()
        if kind == "fitbit":
            args = (options["timezone"],)
        elif kind == "garmin":
            args = (options["units"], options["timezone"])
        elif kind == "csv":
            mapping_path.write_text(json.dumps(options["mapping"]) if not isinstance(options["mapping"], str)
                                    else options["mapping"], encoding="utf-8")
            args = (mapping_path,)
        try:
            report = mod.new_report(path)
            stream = mod.iter_export(path, report, *args)
            by_measure: Dict[str, List[list]] = {}
            n = 0
            for s in stream:
                by_measure.setdefault(s.measure, []).append(_row(s))
                n += 1
                if progress and n % 50_000 == 0:
                    progress(f"Read {n:,} samples…", n, 0)
        except ValueError as exc:
            raise DeviceImportError(str(exc)) from exc
    finally:
        for p in (path, mapping_path):
            try:
                p.unlink()
            except OSError:
                pass
        try:
            os.rmdir(workdir)
        except OSError:
            pass

    import_id = hashlib.sha256(f"{kind}|{report.file_sha256}".encode()).hexdigest()[:16]
    kept, skipped = store_rows(vault, by_measure, import_id, progress)
    summary = {"import_id": import_id, "kind": kind, "label": LABEL[kind], "file_name": name,
               "importer_status": report.importer_status, "samples_read": sum(len(r) for r in by_measure.values()),
               "saved": kept, "already_present": skipped, "rejected": len(report.rejected),
               "rejected_examples": [list(x) for x in report.rejected[:10]], "unmapped_types": report.unmapped_types,
               "notes": report.notes, "first": report.first, "last": report.last,
               "imported_at": datetime.now().astimezone().isoformat(timespec="seconds")}
    vault.put("DEVICE_IMPORT", summary, meta={"import_id": import_id})
    return summary


def store_rows(vault, by_measure: Dict[str, List[list]], import_id: str,
               progress: Optional[Callable[[str, int, int], None]] = None) -> Tuple[Dict[str, int], Dict[str, int]]:
    """Save rows per measure, skipping any already in the vault. Returns (saved, already_present) per measure."""
    kept, skipped = {}, {}
    for measure, rows in by_measure.items():
        if progress:
            progress(f"Saving {measure}…", 0, 0)
        existing = {_key(measure, r) for r in rows_for(vault, measure)}
        fresh = []
        for r in sorted(rows, key=lambda r: r[0]):
            k = _key(measure, r)
            if k in existing:
                continue
            existing.add(k)
            fresh.append(r)
        kept[measure], skipped[measure] = len(fresh), len(rows) - len(fresh)
        ref = vault.tag("device-measure", measure)
        for i in range(0, len(fresh), CHUNK):
            part = fresh[i:i + CHUNK]
            vault.put("DEVICE_SAMPLES", _pack(part), ref=ref,
                      meta={"format": FORMAT, "measure": measure, "rows": len(part), "import_id": import_id,
                            "first": part[0][0], "last": part[-1][0]})
    return kept, skipped


def imports(vault) -> List[dict]:
    return [r.body for r in vault.iter("DEVICE_IMPORT")][::-1]
