"""Generic CSV importer driven by a user-declared mapping.

For any app that exports plain columns. The mapping says what each column
is; the importer applies it and guesses nothing:

* timezone, timestamp format, units and sample interval are declared;
* a column is imported only if mapped, otherwise counted as unmapped;
* ambiguous quantities stay ambiguous: "calories" with no declaration of
  active vs total maps to `energy_unspecified`, never to active energy.

The mapping's own hash is recorded in every sample, so a result can always
be traced to the exact interpretation that produced it.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator, List, Optional, Tuple
from zoneinfo import ZoneInfo

from .schema import DeviceSample, ImportReport, Provenance

IMPORTER = "generic_csv"
STATUS = "USER_MAPPED"

# measure -> {declared unit: (normalized unit, factor, conversion note)}
UNITS = {
    "steps": {"count": ("count", 1.0, None)},
    "distance": {"km": ("km", 1.0, None), "mi": ("km", 1.609344, "mi to km (x1.609344)"), "m": ("km", 0.001, "m to km (/1000)")},
    "active_energy": {"kcal": ("kcal", 1.0, None), "kJ": ("kcal", 1 / 4.184, "kJ to kcal (/4.184)")},
    "energy_unspecified": {"kcal": ("kcal", 1.0, None), "kJ": ("kcal", 1 / 4.184, "kJ to kcal (/4.184)")},
    "heart_rate": {"bpm": ("bpm", 1.0, None)},
    "resting_heart_rate": {"bpm": ("bpm", 1.0, None)},
    "spo2": {"%": ("%", 1.0, None)},
    "body_mass": {"kg": ("kg", 1.0, None), "lb": ("kg", 0.45359237, "lb to kg (x0.45359237)")},
    "bp_systolic": {"mmHg": ("mmHg", 1.0, None)},
    "bp_diastolic": {"mmHg": ("mmHg", 1.0, None)},
    "glucose": {"mg/dL": ("mg/dL", 1.0, None), "mmol/L": ("mg/dL", 18.0156, "mmol/L to mg/dL (x18.0156)")},
}
CUMULATIVE_OK = {"steps", "distance", "active_energy", "energy_unspecified"}


class MappingError(ValueError):
    pass


def load_mapping(path: str | Path) -> Tuple[dict, str]:
    raw = Path(path).read_bytes()
    m = json.loads(raw.decode("utf-8"))
    sha = hashlib.sha256(raw).hexdigest()
    for key in ("source_name", "timestamp", "interval", "columns"):
        if key not in m:
            raise MappingError(f"mapping is missing {key!r}")
    try:
        ZoneInfo(m["timestamp"]["timezone"])
    except Exception:
        raise MappingError("timestamp.timezone must be an IANA name like America/New_York")
    kind = m["interval"].get("kind")
    if kind not in ("fixed", "instant"):
        raise MappingError("interval.kind must be 'fixed' (with minutes) or 'instant'")
    if kind == "fixed" and not (isinstance(m["interval"].get("minutes"), (int, float)) and m["interval"]["minutes"] > 0):
        raise MappingError("interval.minutes must be a positive number for a fixed interval")
    for col, spec in m["columns"].items():
        measure, unit = spec.get("measure"), spec.get("unit")
        if measure not in UNITS:
            raise MappingError(f"column {col!r}: unsupported measure {measure!r}")
        if unit not in UNITS[measure]:
            raise MappingError(f"column {col!r}: unit {unit!r} not supported for {measure}; use one of {sorted(UNITS[measure])}")
        if kind == "instant" and measure in CUMULATIVE_OK:
            raise MappingError(f"column {col!r}: {measure} is a total over a period; declare a fixed interval")
    return m, sha


def new_report(path: str | Path) -> ImportReport:
    path = Path(path)
    data = path.read_bytes()
    return ImportReport(importer=IMPORTER, importer_status=STATUS, file_name=path.name,
                        file_sha256=hashlib.sha256(data).hexdigest())


def import_export(path: str | Path, mapping_path: str | Path) -> Tuple[List[DeviceSample], ImportReport]:
    report = new_report(path)
    return list(iter_export(path, report, mapping_path)), report


def iter_export(path: str | Path, report: ImportReport, mapping_path: str | Path) -> Iterator[DeviceSample]:
    m, mapping_sha = load_mapping(mapping_path)
    ts = m["timestamp"]
    tz = ZoneInfo(ts["timezone"])
    fmt = ts.get("format", "%Y-%m-%d %H:%M:%S")
    minutes = m["interval"].get("minutes") if m["interval"]["kind"] == "fixed" else 0
    notes = report.notes
    notes.append(f"Mapping {Path(mapping_path).name} sha256 {mapping_sha[:16]}…; timezone {ts['timezone']}; "
                 f"interval {'instant' if not minutes else f'{minutes} min'}.")

    path = Path(path)
    text = path.read_bytes().decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    header = [h.strip() for h in reader.fieldnames or []]
    ts_cols = {c for c in (ts.get("column"), ts.get("date_column"), ts.get("time_column")) if c}
    missing = (set(m["columns"]) | ts_cols) - set(header)
    if missing:
        raise MappingError(f"mapped columns not in the file: {', '.join(sorted(missing))}")
    for col in header:
        if col not in m["columns"] and col not in ts_cols:
            report.unmapped_types[col] = report.unmapped_types.get(col, 0) + 1

    first = last = None
    for i, row in enumerate(reader):
        row = {(k or "").strip(): (v or "").strip() for k, v in row.items()}
        try:
            stamp = row[ts["column"]] if ts.get("column") else f"{row[ts['date_column']]} {row[ts['time_column']]}"
            try:
                start = datetime.strptime(stamp, fmt).replace(tzinfo=tz)
            except ValueError:
                raise ValueError(f"timestamp {stamp!r} doesn't match declared format {fmt!r}")
            end = start + timedelta(minutes=minutes)
        except (KeyError, ValueError) as exc:
            report.rejected.append((i, str(exc)))
            continue
        for col, spec in m["columns"].items():
            raw = row.get(col, "")
            if raw in ("", "--", "NA", "null"):
                continue
            try:
                value = float(raw.replace(",", ""))
            except ValueError:
                report.rejected.append((i, f"{col}: non-numeric value {raw!r}"))
                continue
            unit, factor, note = UNITS[spec["measure"]][spec["unit"]]
            s = DeviceSample(
                measure=spec["measure"], value=value * factor, unit=unit, start=start, end=end,
                source_name=m["source_name"], source_app=IMPORTER, device_model=None,
                device_class=m.get("device_class", "OTHER_SOURCE"), method=None, category=None,
                provenance=Provenance(path.name, report.file_sha256, i, f"column:{col}"),
                conversion=note, metadata={"mapping_sha256": mapping_sha, "column": col},
            )
            report.samples += 1
            report.by_measure[s.measure] = report.by_measure.get(s.measure, 0) + 1
            report.by_source[s.source_name] = report.by_source.get(s.source_name, 0) + 1
            first = start if first is None or start < first else first
            last = end if last is None or end > last else last
            yield s
    report.first = first.astimezone(timezone.utc).isoformat() if first else None
    report.last = last.astimezone(timezone.utc).isoformat() if last else None
