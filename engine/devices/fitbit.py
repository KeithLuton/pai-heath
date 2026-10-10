"""Fitbit data export importer (Fitbit export or Google Takeout, folder or zip).

Status: UNVERIFIED_AGAINST_REAL_EXPORT.

Layout handled: one JSON array per type per day, e.g.
    heart_rate-2024-01-15.json         [{"dateTime": "01/15/24 08:32:15", "value": {"bpm": 72, "confidence": 2}}]
    steps-2024-01-15.json              [{"dateTime": "01/15/24 08:00:00", "value": "1234"}]
    calories-2024-01-15.json           [{"dateTime": "01/15/24 08:00:00", "value": "1.2"}]  # energy_unspecified
    distance-2024-01-15.json           [{"dateTime": "01/15/24 08:00:00", "value": "0.02"}] # km as exported
    resting_heart_rate-2024-01-15.json [{"dateTime": "01/15/24 00:00:00", "value": {"date": "01/15/24", "value": 58.4, "error": 6.1}}]
    sleep-2024-01-15.json              [{dateOfSleep, startTime, endTime, duration, type, levels: {summary, data}}]

Timebase — the main trap. Timestamps carry no offset. Heart rate and steps
are treated as UTC and sleep times as local, which matches the export's
known convention but is an ASSUMPTION until a real export confirms it; every
sample records which timebase was applied. Local times need the user's
timezone, supplied explicitly — it is never guessed.

Sleep vocabularies stay separate: "stages" (deep/light/rem/wake) and
"classic" (asleep/restless/awake) are different algorithms, recorded with
different methods, and neither is ever merged with Apple's stages.
"""
from __future__ import annotations

import hashlib
import json
import re
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator, List, Optional, Tuple
from zoneinfo import ZoneInfo

from .schema import DeviceSample, ImportReport, Provenance

IMPORTER = "fitbit"
STATUS = "UNVERIFIED_AGAINST_REAL_EXPORT"
SOURCE_NAME = "Fitbit"

FILE_RE = re.compile(r"(?:^|/)(heart_rate|steps|resting_heart_rate|sleep|calories|distance)-(\d{4}-\d{2}-\d{2})\.json$")
DATA_FILE_RE = re.compile(r"(?:^|/)[a-z_]+-\d{4}-\d{2}(?:-\d{2})?\.(json|csv)$")

UTC_NOTE = "UTC (assumed from Fitbit export convention; unverified)"
LOCAL_NOTE = "local time in the user-supplied timezone"

STAGES = {"deep": "DEEP", "light": "LIGHT", "rem": "REM", "wake": "AWAKE"}
CLASSIC = {"asleep": "ASLEEP_UNSPECIFIED", "restless": "RESTLESS", "awake": "AWAKE"}
CONFIDENCE_WORDS = {"LOW": 1, "MEDIUM": 2, "HIGH": 3}


class FitbitError(ValueError):
    pass


def _mdy(s: str) -> datetime:
    """'01/15/24 08:32:15' -> naive datetime. Two-digit years are 20YY."""
    m = re.fullmatch(r"(\d{2})/(\d{2})/(\d{2}) (\d{2}):(\d{2}):(\d{2})", s or "")
    if not m:
        raise ValueError(f"unexpected date format {s!r}")
    mo, d, y, hh, mi, ss = map(int, m.groups())
    try:
        return datetime(2000 + y, mo, d, hh, mi, ss)
    except ValueError:
        raise ValueError(f"invalid date {s!r} (expected MM/DD/YY)")


def _iso_local(s: str, tz: ZoneInfo) -> datetime:
    """'2024-01-14T23:00:00.000' (no offset) -> aware, in the user's zone."""
    try:
        naive = datetime.fromisoformat(s)
    except (TypeError, ValueError):
        raise ValueError(f"unexpected date format {s!r}")
    if naive.tzinfo is not None:
        return naive
    return naive.replace(tzinfo=tz)


class _Files:
    """Uniform access to a folder or zip; yields (relative path, bytes)."""

    def __init__(self, path: Path):
        self.path = path

    def __iter__(self) -> Iterator[Tuple[str, bytes]]:
        if zipfile.is_zipfile(self.path):
            with zipfile.ZipFile(self.path) as zf:
                for name in sorted(zf.namelist()):
                    if not name.endswith("/"):
                        yield name, zf.read(name)
        elif self.path.is_dir():
            for p in sorted(self.path.rglob("*")):
                if p.is_file():
                    yield p.relative_to(self.path).as_posix(), p.read_bytes()
        else:
            raise FitbitError("expected a Fitbit export folder or zip")


def new_report(path: str | Path) -> ImportReport:
    path = Path(path)
    digest = hashlib.sha256()
    found = False
    for name, data in _Files(path):
        digest.update(name.encode("utf-8") + b"\0" + data)
        found = found or bool(FILE_RE.search(name))
    if not found:
        raise FitbitError("no Fitbit heart_rate/steps/resting_heart_rate/sleep files found")
    return ImportReport(importer=IMPORTER, importer_status=STATUS, file_name=path.name,
                        file_sha256=digest.hexdigest())


def import_export(path: str | Path, local_tz: str) -> Tuple[List[DeviceSample], ImportReport]:
    report = new_report(path)
    return list(iter_export(path, report, local_tz)), report


def iter_export(path: str | Path, report: ImportReport, local_tz: str) -> Iterator[DeviceSample]:
    try:
        tz = ZoneInfo(local_tz)
    except Exception:
        raise FitbitError(f"unknown timezone {local_tz!r}; use an IANA name like America/New_York")
    report_meta = report.notes
    report_meta.append(f"Heart rate and steps: {UTC_NOTE}. Sleep: {LOCAL_NOTE} ({local_tz}).")
    zero_steps = 0
    first: Optional[datetime] = None
    last: Optional[datetime] = None
    for name, data in _Files(Path(path)):
        m = FILE_RE.search(name)
        if not m:
            if DATA_FILE_RE.search(name):
                kind = re.sub(r"-\d{4}-\d{2}(?:-\d{2})?\.(json|csv)$", "", name.split("/")[-1])
                report.unmapped_types[kind] = report.unmapped_types.get(kind, 0) + 1
            continue
        kind = m.group(1)
        file_sha = hashlib.sha256(data).hexdigest()
        try:
            entries = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            report.rejected.append((-1, f"{name}: unreadable JSON ({exc})"))
            continue
        if not isinstance(entries, list):
            report.rejected.append((-1, f"{name}: expected a JSON array"))
            continue
        for i, entry in enumerate(entries):
            prov = Provenance(name, file_sha, i, kind)
            try:
                if kind in ("steps", "calories", "distance") and str((entry or {}).get("value", "")).strip() in ("0", "0.0"):
                    if kind == "steps":
                        zero_steps += 1
                    continue
                for s in _convert(kind, entry, prov, tz):
                    report.samples += 1
                    report.by_measure[s.measure] = report.by_measure.get(s.measure, 0) + 1
                    report.by_source[s.source_name] = report.by_source.get(s.source_name, 0) + 1
                    first = s.start if first is None or s.start < first else first
                    last = s.end if last is None or s.end > last else last
                    yield s
            except (ValueError, TypeError, KeyError, AttributeError) as exc:
                report.rejected.append((i, f"{name}: {exc}"))
    report_meta.append(f"Zero-step minutes skipped: {zero_steps}")
    report.first = first.astimezone(timezone.utc).isoformat() if first else None
    report.last = last.astimezone(timezone.utc).isoformat() if last else None


def _base(prov: Provenance, start: datetime, end: datetime, timebase: str) -> dict:
    return dict(start=start, end=end, source_name=SOURCE_NAME, source_app=IMPORTER,
                device_model=None, device_class="WATCH_OR_TRACKER", provenance=prov,
                metadata={"timebase": timebase})


def _convert(kind: str, e: dict, prov: Provenance, tz: ZoneInfo) -> Iterator[DeviceSample]:
    if kind == "heart_rate":
        t = _mdy(e["dateTime"]).replace(tzinfo=timezone.utc)
        v = e["value"]
        bpm = float(v["bpm"])
        conf = v.get("confidence")
        if isinstance(conf, str):
            conf = CONFIDENCE_WORDS.get(conf.upper(), conf)
        base = _base(prov, t, t, UTC_NOTE)
        base["metadata"]["confidence"] = conf
        yield DeviceSample(measure="heart_rate", value=bpm, unit="bpm", method=None, category=None, **base)
    elif kind == "steps":
        t = _mdy(e["dateTime"]).replace(tzinfo=timezone.utc)
        steps = float(str(e["value"]))
        # Fitbit step entries are per-minute buckets.
        yield DeviceSample(measure="steps", value=steps, unit="count", method=None, category=None,
                           **_base(prov, t, t + timedelta(minutes=1), UTC_NOTE + "; 1-minute bucket"))
    elif kind == "resting_heart_rate":
        v = e["value"]
        rhr = float(v.get("value") or 0)
        if rhr <= 0:
            raise ValueError("no resting heart rate computed for this day (value 0)")
        day = datetime.strptime(v["date"], "%m/%d/%y").replace(tzinfo=tz)
        base = _base(prov, day, day + timedelta(days=1), "calendar day in the user-supplied timezone")
        base["metadata"]["error"] = v.get("error")
        yield DeviceSample(measure="resting_heart_rate", value=rhr, unit="bpm", method="FITBIT_DAILY",
                           category=None, **base)
    elif kind == "calories":
        # Fitbit minute-level calories; value is a string or number. Active vs total
        # is not distinguished in the per-minute file — keep as energy_unspecified.
        t = _mdy(e["dateTime"]).replace(tzinfo=timezone.utc)
        cal = float(str(e["value"]))
        yield DeviceSample(measure="energy_unspecified", value=cal, unit="kcal", method=None, category=None,
                           **_base(prov, t, t + timedelta(minutes=1), UTC_NOTE + "; 1-minute bucket"))
    elif kind == "distance":
        # Fitbit minute-level distance is in kilometers in the JSON export.
        t = _mdy(e["dateTime"]).replace(tzinfo=timezone.utc)
        dist = float(str(e["value"]))
        yield DeviceSample(measure="distance", value=dist, unit="km", method=None, category=None,
                           **_base(prov, t, t + timedelta(minutes=1), UTC_NOTE + "; 1-minute bucket; km as exported"))
    elif kind == "sleep":
        yield from _sleep(e, prov, tz)


def _sleep(e: dict, prov: Provenance, tz: ZoneInfo) -> Iterator[DeviceSample]:
    start, end = _iso_local(e["startTime"], tz), _iso_local(e["endTime"], tz)
    if end <= start:
        raise ValueError("sleep endTime not after startTime")
    levels = e.get("levels") or {}
    kind = (e.get("type") or "").lower()
    summary = levels.get("summary") or {}
    vocab_keys = set(summary) | {d.get("level") for d in levels.get("data") or []}
    if kind not in ("stages", "classic"):
        kind = "stages" if vocab_keys & {"deep", "light", "rem"} else "classic"
    vocab, method = (STAGES, "FITBIT_STAGES") if kind == "stages" else (CLASSIC, "FITBIT_CLASSIC")
    common_meta = {"timebase": LOCAL_NOTE, "efficiency": e.get("efficiency"), "dateOfSleep": e.get("dateOfSleep")}

    data = levels.get("data") or []
    if data:
        for seg in data:
            stage = vocab.get(seg.get("level"))
            if stage is None:
                raise ValueError(f"unknown sleep level {seg.get('level')!r} for {kind}")
            s = _iso_local(seg["dateTime"], tz)
            yield DeviceSample(measure="sleep_stage", value=None, unit="interval", method=method, category=stage,
                               start=s, end=s + timedelta(seconds=float(seg["seconds"])),
                               source_name=SOURCE_NAME, source_app=IMPORTER, device_model=None,
                               device_class="WATCH_OR_TRACKER", provenance=prov, metadata=dict(common_meta))
        return
    # Summary only: minutes per stage for the whole session. No intervals are invented.
    if not summary:
        raise ValueError("sleep log has neither level data nor a summary")
    for level, info in summary.items():
        stage = vocab.get(level)
        if stage is None:
            raise ValueError(f"unknown sleep level {level!r} for {kind}")
        meta = dict(common_meta)
        meta["shape"] = "session total; stage timing not in export"
        yield DeviceSample(measure="sleep_stage_minutes", value=float(info["minutes"]), unit="min",
                           method=method, category=stage, start=start, end=end,
                           source_name=SOURCE_NAME, source_app=IMPORTER, device_model=None,
                           device_class="WATCH_OR_TRACKER", provenance=prov, metadata=meta)
