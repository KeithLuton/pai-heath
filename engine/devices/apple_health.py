"""Apple Health export importer (export.zip or export.xml).

Status: UNVERIFIED_AGAINST_REAL_EXPORT. Built against the published structure
and synthetic fixtures. It must pass at least one real export before release.

Design:
* Streaming (iterparse + element clearing): real exports run to gigabytes.
* Refuses any document declaring XML entities (the real export's internal
  DTD declares elements and attributes only), so entity-expansion attacks
  never reach the parser.
* Never strengthens the export: no device model unless the record's `device`
  field states one; source names are kept verbatim (users rename devices);
  vendor-specific metrics keep their method.
* Every conversion (unit, fraction->percent) is logged on the sample.
* Unknown record types are counted in the report, never silently dropped.
* The <Me> profile element (date of birth, sex, blood type) is not imported.
"""
from __future__ import annotations

import hashlib
import io
import re
import zipfile
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import IO, Iterator, Optional, Tuple
from xml.etree.ElementTree import iterparse

from .schema import DeviceSample, ImportReport, Provenance

IMPORTER = "apple_health"
STATUS = "UNVERIFIED_AGAINST_REAL_EXPORT"
HEAD_SCAN_BYTES = 256 * 1024

Q = "HKQuantityTypeIdentifier"
C = "HKCategoryTypeIdentifier"

# raw type -> (measure, normalized unit, method)
QUANTITY = {
    f"{Q}HeartRate": ("heart_rate", "bpm", None),
    f"{Q}RestingHeartRate": ("resting_heart_rate", "bpm", None),
    f"{Q}WalkingHeartRateAverage": ("walking_heart_rate", "bpm", None),
    f"{Q}HeartRateVariabilitySDNN": ("hrv", "ms", "SDNN"),
    f"{Q}StepCount": ("steps", "count", None),
    f"{Q}DistanceWalkingRunning": ("distance", "km", None),
    f"{Q}DistanceCycling": ("distance_cycling", "km", None),
    f"{Q}ActiveEnergyBurned": ("active_energy", "kcal", None),
    f"{Q}BasalEnergyBurned": ("basal_energy", "kcal", None),
    f"{Q}FlightsClimbed": ("flights_climbed", "count", None),
    f"{Q}OxygenSaturation": ("spo2", "%", None),
    f"{Q}RespiratoryRate": ("respiratory_rate", "breaths/min", None),
    f"{Q}BloodPressureSystolic": ("bp_systolic", "mmHg", None),
    f"{Q}BloodPressureDiastolic": ("bp_diastolic", "mmHg", None),
    f"{Q}BloodGlucose": ("glucose", "mg/dL", None),
    f"{Q}BodyMass": ("body_mass", "kg", None),
    f"{Q}BodyTemperature": ("body_temperature", "degC", None),
    f"{Q}AppleSleepingWristTemperature": ("wrist_temperature", "degC", "APPLE_SLEEPING_WRIST"),
    f"{Q}AppleExerciseTime": ("exercise_time", "min", "APPLE_EXERCISE_RING"),
    f"{Q}VO2Max": ("vo2_max", "mL/min·kg", "APPLE_VO2MAX"),
    f"{Q}WalkingSpeed": ("walking_speed", "km/h", None),
    f"{Q}RunningSpeed": ("running_speed", "km/h", None),
}

SLEEP_VALUES = {
    "HKCategoryValueSleepAnalysisInBed": "IN_BED",
    "HKCategoryValueSleepAnalysisAsleep": "ASLEEP_UNSPECIFIED",          # pre-iOS 16
    "HKCategoryValueSleepAnalysisAsleepUnspecified": "ASLEEP_UNSPECIFIED",
    "HKCategoryValueSleepAnalysisAsleepCore": "CORE",
    "HKCategoryValueSleepAnalysisAsleepDeep": "DEEP",
    "HKCategoryValueSleepAnalysisAsleepREM": "REM",
    "HKCategoryValueSleepAnalysisAwake": "AWAKE",
}

# Values can contain commas (hardware:Watch7,2), so a field ends only where the next
# "key:" begins or the closing ">" is reached.
_DEVICE_FIELD = re.compile(r"(\w+):(.*?)(?=,\s*\w+:|>\s*$)")
_GLUCOSE_MMOL = re.compile(r"^mmol(?:/[Ll]|<[\d.]+>/[Ll])$")


class AppleHealthError(ValueError):
    pass


# ---------- helpers ----------

@lru_cache(maxsize=4096)
def _tz(offset: str) -> timezone:
    sign = -1 if offset[0] == "-" else 1
    return timezone(sign * timedelta(hours=int(offset[1:3]), minutes=int(offset[3:5])))


@lru_cache(maxsize=65536)
def _parse_date(s: Optional[str]) -> datetime:
    """'YYYY-MM-DD HH:MM:SS +HHMM' — sliced directly; strptime is the bottleneck
    on multi-gigabyte exports. Anything not in exactly this shape is rejected."""
    if not s or len(s) != 25 or s[4] != "-" or s[10] != " " or s[19] != " " or s[20] not in "+-":
        raise ValueError(f"unexpected date format {s!r}")
    try:
        return datetime(int(s[0:4]), int(s[5:7]), int(s[8:10]), int(s[11:13]), int(s[14:16]),
                        int(s[17:19]), tzinfo=_tz(s[20:25]))
    except ValueError as exc:
        raise ValueError(f"invalid date {s!r}") from exc


def _parse_device(raw: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """Return (device_model, device_name) only if the export states them."""
    if not raw:
        return None, None
    body = re.sub(r"^<<HKDevice:[^>]*>,?\s*", "", raw.strip())
    fields = {k.strip(): v.strip() for k, v in _DEVICE_FIELD.findall(body)}
    model = fields.get("hardware") or fields.get("model")
    return model, fields.get("name")


def _device_class(device_model: Optional[str], device_name: Optional[str]) -> str:
    text = f"{device_model or ''} {device_name or ''}"
    if "Watch" in text:
        return "WATCH"
    if "iPhone" in text:
        return "PHONE"
    return "OTHER_SOURCE"


def _convert(measure: str, value: float, unit: str) -> Tuple[float, Optional[str]]:
    """Normalize units. Every conversion returns a log string."""
    if measure == "spo2" and unit == "%" and value <= 1.0:
        return value * 100.0, "fraction to percent (x100)"
    if measure in ("distance", "distance_cycling"):
        if unit == "km":
            return value, None
        if unit == "mi":
            return value * 1.609344, "mi to km (x1.609344)"
        if unit == "m":
            return value / 1000.0, "m to km (/1000)"
    if measure in ("active_energy", "basal_energy"):
        if unit in ("kcal", "Cal"):
            return value, None
        if unit == "kJ":
            return value / 4.184, "kJ to kcal (/4.184)"
    if measure == "glucose":
        if unit in ("mg/dL", "mg/dl"):
            return value, None
        if _GLUCOSE_MMOL.match(unit) or unit.lower() == "mmol/l":
            return value * 18.0156, "mmol/L to mg/dL (x18.0156, glucose 180.156 g/mol)"
    if measure == "body_mass":
        if unit == "kg":
            return value, None
        if unit == "lb":
            return value * 0.45359237, "lb to kg (x0.45359237)"
    if measure in ("body_temperature", "wrist_temperature"):
        if unit == "degC":
            return value, None
        if unit == "degF":
            return (value - 32.0) * 5.0 / 9.0, "degF to degC"
    if measure in ("walking_speed", "running_speed"):
        if unit in ("km/h", "km/hr"):
            return value, None
        if unit in ("m/s",):
            return value * 3.6, "m/s to km/h (x3.6)"
        if unit in ("mi/h", "mph"):
            return value * 1.609344, "mi/h to km/h (x1.609344)"
    if measure == "exercise_time":
        if unit in ("min", "s"):
            if unit == "s":
                return value / 60.0, "s to min (/60)"
            return value, None
    if measure == "vo2_max":
        # Apple exports mL/min·kg under several spellings
        if unit in ("mL/min·kg", "mL/min/kg", "ml/min·kg", "ml/min/kg"):
            return value, None
    expected = {"heart_rate": "count/min", "resting_heart_rate": "count/min", "walking_heart_rate": "count/min",
                "hrv": "ms", "steps": "count", "flights_climbed": "count", "respiratory_rate": "count/min",
                "bp_systolic": "mmHg", "bp_diastolic": "mmHg"}
    if expected.get(measure) == unit:
        return value, None
    raise ValueError(f"unsupported unit {unit!r} for {measure}")


def _find_export_member(zf: zipfile.ZipFile) -> str:
    names = [n for n in zf.namelist() if n.endswith("/export.xml") or n == "export.xml"]
    if not names:
        raise AppleHealthError("export.xml not found in the zip")
    return names[0]


def _sha256_and_guard(path: Path) -> Tuple[str, str]:
    """Hash the export and refuse entity-bearing or non-HealthData documents.

    Opens the zip (or file) only for this pass; the ZipFile is closed before return
    so multi-gigabyte imports do not leak handles across the subsequent parse pass.
    """
    digest = hashlib.sha256()
    head = b""
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as zf:
            member = _find_export_member(zf)
            with zf.open(member) as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    if len(head) < HEAD_SCAN_BYTES:
                        head += block[: HEAD_SCAN_BYTES - len(head)]
                    digest.update(block)
            name = f"{path.name}:{member}" if member != path.name else path.name
    else:
        member = path.name
        name = path.name
        with open(path, "rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                if len(head) < HEAD_SCAN_BYTES:
                    head += block[: HEAD_SCAN_BYTES - len(head)]
                digest.update(block)
    if b"<!ENTITY" in head:
        raise AppleHealthError("export declares XML entities; refusing to parse")
    if b"<HealthData" not in head:
        raise AppleHealthError("not an Apple Health export (no <HealthData> element)")
    return name, digest.hexdigest()


# ---------- import ----------

def import_export(path: str | Path) -> Tuple[list[DeviceSample], ImportReport]:
    """Convenience wrapper for tests and small files. Large exports should use
    iter_export() and write samples to storage as they stream."""
    report = new_report(path)
    samples = list(iter_export(path, report))
    return samples, report


def new_report(path: str | Path) -> ImportReport:
    path = Path(path)
    name, sha = _sha256_and_guard(path)
    return ImportReport(importer=IMPORTER, importer_status=STATUS,
                        file_name=name, file_sha256=sha)


def _parse_stream(stream: IO[bytes], report: ImportReport) -> Iterator[DeviceSample]:
    """Shared streaming parse; caller owns the stream lifetime."""
    index = -1
    context = iterparse(stream, events=("start", "end"))
    _, root = next(context)
    for event, elem in context:
        if event != "end":
            continue
        tag = elem.tag
        if tag in ("Record", "Workout", "ActivitySummary", "ClinicalRecord", "Correlation"):
            index += 1
            try:
                for s in _convert_element(elem, index, report):
                    report.samples += 1
                    report.by_measure[s.measure] = report.by_measure.get(s.measure, 0) + 1
                    report.by_source[s.source_name] = report.by_source.get(s.source_name, 0) + 1
                    a, b = s.start.isoformat(), s.end.isoformat()
                    report.first = a if report.first is None or s.start < _parse_iso(report.first) else report.first
                    report.last = b if report.last is None or s.end > _parse_iso(report.last) else report.last
                    yield s
            except ValueError as exc:
                report.rejected.append((index, f"{elem.get('type') or tag}: {exc}"))
            root.clear()
        elif tag == "ExportDate":
            report.export_date = elem.get("value")
        elif tag == "Me":
            root.clear()   # profile is not imported


def iter_export(path: str | Path, report: ImportReport) -> Iterator[DeviceSample]:
    """Stream samples; the report's counts are complete when the iterator ends."""
    path = Path(path)
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as zf:
            member = _find_export_member(zf)
            with zf.open(member) as stream:
                yield from _parse_stream(stream, report)
    else:
        with open(path, "rb") as stream:
            yield from _parse_stream(stream, report)


@lru_cache(maxsize=8)
def _parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s)


def _convert_element(elem, index: int, report: ImportReport) -> Iterator[DeviceSample]:
    tag = elem.tag
    if tag == "Workout":
        yield _workout(elem, index, report)
        return
    if tag != "Record":
        report.unmapped_types[tag] = report.unmapped_types.get(tag, 0) + 1
        return

    raw_type = elem.get("type") or ""
    start, end = _parse_date(elem.get("startDate")), _parse_date(elem.get("endDate"))
    if end < start:
        raise ValueError("endDate before startDate")
    model, dev_name = _parse_device(elem.get("device"))
    source_name = elem.get("sourceName") or "Unknown source"
    common = dict(
        start=start, end=end, source_name=source_name, source_app=IMPORTER,
        device_model=model, device_class=_device_class(model, dev_name),
        provenance=Provenance(report.file_name, report.file_sha256, index, raw_type),
        metadata=_metadata(elem),
    )

    if raw_type == f"{C}SleepAnalysis":
        stage = SLEEP_VALUES.get(elem.get("value") or "")
        if stage is None:
            raise ValueError(f"unknown sleep value {elem.get('value')!r}")
        # Sleep staging is the source's own algorithm: method names the source.
        yield DeviceSample(measure="sleep_stage", value=None, unit="interval", method=f"SOURCE:{source_name}",
                           category=stage, **common)
        return

    spec = QUANTITY.get(raw_type)
    if spec is None:
        report.unmapped_types[raw_type] = report.unmapped_types.get(raw_type, 0) + 1
        return
    measure, unit, method = spec
    try:
        raw_value = float(elem.get("value") or "")
    except ValueError:
        raise ValueError(f"non-numeric value {elem.get('value')!r}")
    value, conversion = _convert(measure, raw_value, elem.get("unit") or "")
    yield DeviceSample(measure=measure, value=value, unit=unit, method=method, category=None,
                       conversion=conversion, **common)


def _metadata(elem) -> dict:
    meta = {m.get("key"): m.get("value") for m in elem.findall("MetadataEntry") if m.get("key")}
    beats = elem.find("HeartRateVariabilityMetadataList")
    if beats is not None:
        meta["instantaneous_bpm_count"] = len(beats.findall("InstantaneousBeatsPerMinute"))
    return meta


def _workout(elem, index: int, report: ImportReport) -> DeviceSample:
    start, end = _parse_date(elem.get("startDate")), _parse_date(elem.get("endDate"))
    if end < start:
        raise ValueError("endDate before startDate")
    model, dev_name = _parse_device(elem.get("device"))
    duration = float(elem.get("duration") or (end - start).total_seconds())
    unit = elem.get("durationUnit") or "s"
    if unit == "min":
        duration *= 60.0
    elif unit != "s":
        raise ValueError(f"unsupported duration unit {unit!r}")
    totals: dict = {}
    # Older exports: attributes. iOS 16+: <WorkoutStatistics> children.
    for attr, key in (("totalDistance", "distance"), ("totalEnergyBurned", "active_energy")):
        if elem.get(attr):
            totals[key] = f"{elem.get(attr)} {elem.get(attr + 'Unit') or ''}".strip()
    for st in elem.findall("WorkoutStatistics"):
        t = st.get("type") or ""
        if st.get("sum"):
            totals[t.replace(Q, "")] = f"{st.get('sum')} {st.get('unit') or ''}".strip()
    activity = (elem.get("workoutActivityType") or "").replace("HKWorkoutActivityType", "") or "Unknown"
    meta = _metadata(elem)
    meta["totals_as_exported"] = totals
    return DeviceSample(
        measure="workout", value=duration, unit="s", start=start, end=end,
        source_name=elem.get("sourceName") or "Unknown source", source_app=IMPORTER,
        device_model=model, device_class=_device_class(model, dev_name),
        method=None, category=activity,
        provenance=Provenance(report.file_name, report.file_sha256, index, "Workout"),
        conversion="min to s (x60)" if unit == "min" else None, metadata=meta,
    )
