"""Garmin Connect activities list (Activities.csv) importer.

Status: UNVERIFIED_AGAINST_REAL_EXPORT.

Scope: one workout summary per row. This file does NOT contain daily heart
rate, resting HR, sleep, stress or steps — those come from the full account
export (DI_CONNECT JSON), which needs its own importer.

Rules:
* Units are not in the file; the user declares "metric" or "statute". Nothing
  is assumed.
* Average/max heart rate are workout summaries kept in metadata. They never
  become heart-rate samples.
* A date without a time is a calendar day; no start time is invented.
* "--" means missing. Quoted numbers may carry thousands commas.
"""
from __future__ import annotations

import csv
import hashlib
import io
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator, List, Optional, Tuple
from zoneinfo import ZoneInfo

from .schema import DeviceSample, ImportReport, Provenance

IMPORTER = "garmin_activities"
STATUS = "UNVERIFIED_AGAINST_REAL_EXPORT"
SOURCE_NAME = "Garmin Connect"
REQUIRED = {"Activity Type", "Date", "Time"}
UNIT_SYSTEMS = {"metric": ("km", "m"), "statute": ("mi", "ft")}


class GarminError(ValueError):
    pass


def _num(v: Optional[str]) -> Optional[float]:
    v = (v or "").strip()
    if v in ("", "--"):
        return None
    try:
        return float(v.replace(",", ""))
    except ValueError:
        raise ValueError(f"non-numeric value {v!r}")


def _duration_s(v: str) -> float:
    v = (v or "").strip()
    m = re.fullmatch(r"(?:(\d+):)?(\d{1,2}):(\d{2}(?:\.\d+)?)", v)
    if not m:
        raise ValueError(f"unexpected duration {v!r}")
    h, mi, s = m.groups()
    return int(h or 0) * 3600 + int(mi) * 60 + float(s)


def _when(v: str, tz: ZoneInfo) -> Tuple[datetime, Optional[datetime], str]:
    """Return (start, end_or_None, timing). Date-only rows span the calendar day."""
    v = (v or "").strip()
    for fmt, timing in (("%Y-%m-%d %H:%M:%S", "START_TIME"), ("%Y-%m-%d %H:%M", "START_TIME"), ("%Y-%m-%d", "DAY_ONLY")):
        try:
            t = datetime.strptime(v, fmt).replace(tzinfo=tz)
        except ValueError:
            continue
        return (t, t + timedelta(days=1), timing) if timing == "DAY_ONLY" else (t, None, timing)
    raise ValueError(f"unexpected date {v!r}")


def _read(path: Path) -> Tuple[bytes, str]:
    data = path.read_bytes()
    return data, data.decode("utf-8-sig")


def new_report(path: str | Path) -> ImportReport:
    path = Path(path)
    data, text = _read(path)
    header = next(csv.reader(io.StringIO(text)), [])
    missing = REQUIRED - {h.strip() for h in header}
    if missing:
        raise GarminError(f"not a Garmin activities CSV (missing columns: {', '.join(sorted(missing))})")
    return ImportReport(importer=IMPORTER, importer_status=STATUS, file_name=path.name,
                        file_sha256=hashlib.sha256(data).hexdigest())


def import_export(path: str | Path, units: str, local_tz: str) -> Tuple[List[DeviceSample], ImportReport]:
    report = new_report(path)
    return list(iter_export(path, report, units, local_tz)), report


def iter_export(path: str | Path, report: ImportReport, units: str, local_tz: str) -> Iterator[DeviceSample]:
    if units not in UNIT_SYSTEMS:
        raise GarminError("units must be 'metric' or 'statute' (as set in the user's Garmin Connect settings)")
    try:
        tz = ZoneInfo(local_tz)
    except Exception:
        raise GarminError(f"unknown timezone {local_tz!r}; use an IANA name like America/New_York")
    dist_unit, elev_unit = UNIT_SYSTEMS[units]
    report.notes.append(
        f"Units declared by user: {units} (distance {dist_unit}, elevation {elev_unit}). Times in {local_tz}.")
    path = Path(path)
    _, text = _read(path)
    first = last = None
    for i, row in enumerate(csv.DictReader(io.StringIO(text))):
        row = {(k or "").strip(): v for k, v in row.items()}
        prov = Provenance(path.name, report.file_sha256, i, "Activities.csv row")
        try:
            date_raw = (row.get("Date") or "").strip()
            time_raw = (row.get("Time") or "").strip()
            if not date_raw:
                raise ValueError("missing Date")
            if not time_raw or time_raw == "--":
                raise ValueError("missing Time (duration)")
            start, day_end, timing = _when(date_raw, tz)
            duration = _duration_s(time_raw)
            end = day_end or start + timedelta(seconds=duration)
            distance = _num(row.get("Distance"))
            totals = {
                "distance_km": None if distance is None else (distance if units == "metric" else distance * 1.609344),
                "distance_as_exported": None if distance is None else f"{row.get('Distance').strip()} {dist_unit}",
                "calories": _num(row.get("Calories")),
                "avg_hr_summary": _num(row.get("Avg HR")),
                "max_hr_summary": _num(row.get("Max HR")),
                "elev_gain_as_exported": None if _num(row.get("Elev Gain")) is None else f"{row.get('Elev Gain').strip()} {elev_unit}",
                "elev_loss_as_exported": None if _num(row.get("Elev Loss")) is None else f"{row.get('Elev Loss').strip()} {elev_unit}",
                "avg_pace_as_exported": (row.get("Avg Pace") or "").strip() or None,
                "best_pace_as_exported": (row.get("Best Pace") or "").strip() or None,
            }
            known = {"Activity Type", "Date", "Favorite", "Title", "Distance", "Calories", "Time", "Avg HR",
                     "Max HR", "Avg Pace", "Best Pace", "Elev Gain", "Elev Loss"}
            extra = {k: v for k, v in row.items() if k and k not in known and (v or "").strip() not in ("", "--")}
            sample = DeviceSample(
                measure="workout", value=duration, unit="s", start=start, end=end,
                source_name=SOURCE_NAME, source_app=IMPORTER, device_model=None, device_class="WATCH_OR_TRACKER",
                method=None, category=(row.get("Activity Type") or "unknown").strip().lower(),
                provenance=prov, conversion="mi to km (x1.609344)" if units == "statute" and distance is not None else None,
                metadata={"timing": timing, "title": (row.get("Title") or "").strip(),
                          "totals": totals, "other_columns_as_exported": extra,
                          "note": "Average/max HR are workout summaries, not heart-rate samples."},
            )
        except (ValueError, TypeError) as exc:
            report.rejected.append((i, str(exc)))
            continue
        report.samples += 1
        report.by_measure["workout"] = report.by_measure.get("workout", 0) + 1
        report.by_source[SOURCE_NAME] = report.by_source.get(SOURCE_NAME, 0) + 1
        first = start if first is None or start < first else first
        last = end if last is None or end > last else last
        yield sample
    report.first = first.astimezone(timezone.utc).isoformat() if first else None
    report.last = last.astimezone(timezone.utc).isoformat() if last else None
