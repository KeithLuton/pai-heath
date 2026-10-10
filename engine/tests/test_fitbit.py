"""Fitbit importer tests — synthetic, built from the values in the provided sample,
laid out the way the export delivers them (one array file per type per day)."""
import json
import sys
import tempfile
import unittest
import zipfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from devices import fitbit                       # noqa: E402
from devices.dedupe import series_key            # noqa: E402

TZ = "America/New_York"


def build(root: Path, with_data=False, classic=False):
    pa = root / "Takeout" / "Fitbit" / "Global Export Data"
    pa.mkdir(parents=True)
    (pa / "heart_rate-2024-01-15.json").write_text(json.dumps([
        {"dateTime": "01/15/24 08:32:15", "value": {"bpm": 72, "confidence": "HIGH"}},
        {"dateTime": "01/15/24 08:32:20", "value": {"bpm": 73, "confidence": 2}},
        {"dateTime": "15/01/24 08:32:25", "value": {"bpm": 74, "confidence": 2}},   # bad date order
    ]))
    (pa / "steps-2024-01-15.json").write_text(json.dumps([
        {"dateTime": "01/15/24 08:00:00", "value": "1234"},
        {"dateTime": "01/15/24 08:01:00", "value": "0"},
    ]))
    (pa / "resting_heart_rate-2024-01-15.json").write_text(json.dumps([
        {"dateTime": "01/15/24 00:00:00", "value": {"date": "01/15/24", "value": 58.4, "error": 6.1}},
        {"dateTime": "01/16/24 00:00:00", "value": {"date": "01/16/24", "value": 0, "error": 0}},
    ]))
    levels = {"summary": {"deep": {"minutes": 90}, "light": {"minutes": 180}, "rem": {"minutes": 120}, "wake": {"minutes": 30}}}
    if with_data:
        levels["data"] = [
            {"dateTime": "2024-01-14T23:00:00.000", "level": "light", "seconds": 1800},
            {"dateTime": "2024-01-14T23:30:00.000", "level": "deep", "seconds": 3600},
        ]
    log = {"dateOfSleep": "2024-01-15", "duration": 28800000, "efficiency": 92,
           "startTime": "2024-01-14T23:00:00.000", "endTime": "2024-01-15T07:00:00.000",
           "type": "stages", "levels": levels}
    if classic:
        log = {"dateOfSleep": "2024-01-16", "duration": 25200000, "efficiency": 88, "type": "classic",
               "startTime": "2024-01-15T23:30:00.000", "endTime": "2024-01-16T06:30:00.000",
               "levels": {"summary": {"asleep": {"minutes": 380}, "restless": {"minutes": 25}, "awake": {"minutes": 15}}}}
    (pa / "sleep-2024-01-15.json").write_text(json.dumps([log]))
    (pa / "distance-2024-01-15.json").write_text(json.dumps([
        {"dateTime": "01/15/24 08:00:00", "value": "0.42"},
    ]))
    (pa / "floors-2024-01-15.json").write_text("[]")          # not mapped: counted
    return root / "Takeout"


class FitbitTests(unittest.TestCase):
    def test_provided_values(self):
        with tempfile.TemporaryDirectory() as d:
            samples, rep = fitbit.import_export(build(Path(d)), TZ)
        self.assertEqual(rep.importer_status, "UNVERIFIED_AGAINST_REAL_EXPORT")
        hr = [s for s in samples if s.measure == "heart_rate"]
        self.assertEqual([s.value for s in hr], [72.0, 73.0])
        self.assertEqual(hr[0].metadata["confidence"], 3)                 # "HIGH" accepted
        self.assertEqual(hr[0].start, datetime(2024, 1, 15, 8, 32, 15, tzinfo=timezone.utc))
        self.assertIn("UTC (assumed", hr[0].metadata["timebase"])
        self.assertTrue(any("invalid date" in r for _, r in rep.rejected))

    def test_steps_and_zero_minutes(self):
        with tempfile.TemporaryDirectory() as d:
            samples, rep = fitbit.import_export(build(Path(d)), TZ)
        steps = [s for s in samples if s.measure == "steps"]
        self.assertEqual([s.value for s in steps], [1234.0])
        self.assertEqual((steps[0].end - steps[0].start).total_seconds(), 60)
        self.assertIn("Zero-step minutes skipped: 1", rep.notes)

    def test_resting_hr_zero_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            samples, rep = fitbit.import_export(build(Path(d)), TZ)
        self.assertEqual([s.value for s in samples if s.measure == "resting_heart_rate"], [58.4])
        self.assertTrue(any("no resting heart rate computed" in r for _, r in rep.rejected))

    def test_sleep_summary_only_invents_no_timing(self):
        with tempfile.TemporaryDirectory() as d:
            samples, _ = fitbit.import_export(build(Path(d)), TZ)
        mins = {s.category: s.value for s in samples if s.measure == "sleep_stage_minutes"}
        self.assertEqual(mins, {"DEEP": 90.0, "LIGHT": 180.0, "REM": 120.0, "AWAKE": 30.0})
        self.assertFalse(any(s.measure == "sleep_stage" for s in samples))
        one = next(s for s in samples if s.measure == "sleep_stage_minutes")
        self.assertEqual(one.start.utcoffset().total_seconds(), -5 * 3600)   # local EST applied
        self.assertEqual(one.method, "FITBIT_STAGES")

    def test_sleep_intervals_when_present(self):
        with tempfile.TemporaryDirectory() as d:
            samples, _ = fitbit.import_export(build(Path(d), with_data=True), TZ)
        segs = [s for s in samples if s.measure == "sleep_stage"]
        self.assertEqual([(s.category, (s.end - s.start).seconds) for s in segs], [("LIGHT", 1800), ("DEEP", 3600)])

    def test_classic_kept_separate(self):
        with tempfile.TemporaryDirectory() as d:
            samples, _ = fitbit.import_export(build(Path(d), classic=True), TZ)
        cats = {s.category for s in samples if s.measure == "sleep_stage_minutes"}
        self.assertEqual(cats, {"ASLEEP_UNSPECIFIED", "RESTLESS", "AWAKE"})
        self.assertTrue(all(s.method == "FITBIT_CLASSIC" for s in samples if s.measure == "sleep_stage_minutes"))
        self.assertNotEqual(series_key(next(s for s in samples if s.measure == "sleep_stage_minutes"))[1], "FITBIT_STAGES")

    def test_unmapped_counted(self):
        with tempfile.TemporaryDirectory() as d:
            samples, rep = fitbit.import_export(build(Path(d)), TZ)
        self.assertEqual(rep.unmapped_types.get("floors"), 1)
        dist = [s for s in samples if s.measure == "distance"]
        self.assertEqual([s.value for s in dist], [0.42])

    def test_zip_and_timezone_required(self):
        with tempfile.TemporaryDirectory() as d:
            root = build(Path(d))
            z = Path(d) / "takeout.zip"
            with zipfile.ZipFile(z, "w") as zf:
                for p in root.rglob("*.json"):
                    zf.write(p, p.relative_to(root.parent).as_posix())
            samples, _ = fitbit.import_export(z, TZ)
            self.assertTrue(samples)
            with self.assertRaises(fitbit.FitbitError):
                fitbit.import_export(z, "Eastern")

    def test_not_a_fitbit_export(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "x.json").write_text("{}")
            with self.assertRaises(fitbit.FitbitError):
                fitbit.import_export(Path(d), TZ)


if __name__ == "__main__":
    unittest.main(verbosity=2)
