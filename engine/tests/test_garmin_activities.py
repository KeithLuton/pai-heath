import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from devices import garmin_activities as ga        # noqa: E402

PROVIDED = """Activity Type,Date,Favorite,Title,Distance,Calories,Time,Avg HR,Max HR,Avg Pace,Best Pace,Elev Gain,Elev Loss
running,2024-01-15,false,Morning Run,5.2,320,00:30:00,145,172,5:46,4:52,45,42
cycling,2024-01-14,false,Evening Ride,25.8,680,01:15:00,138,165,2:54,2:12,125,118
"""
MESSY = "\ufeff" + """Activity Type,Date,Favorite,Title,Distance,Calories,Time,Avg HR,Max HR,Aerobic TE,Avg Pace,Best Pace,Elev Gain,Elev Loss
walking,2024-01-16 07:05:12,false,Walk,3.10,"1,045",45:10,--,--,2.1,14:34,12:01,--,--
strength_training,2024-01-17 18:00:00,true,Gym,0.00,210,00:40:00.5,98,131,1.4,--,--,--,--
running,2024/01/18,false,Bad date,5.0,300,00:30:00,140,160,--,--,--,--,--
running,2024-01-19,false,Bad time,5.0,300,thirty,140,160,--,--,--,--,--
"""


def write(d, text, name="Activities.csv"):
    p = Path(d) / name
    p.write_text(text, encoding="utf-8")
    return p


class GarminActivities(unittest.TestCase):
    def test_provided_metric(self):
        with tempfile.TemporaryDirectory() as d:
            samples, rep = ga.import_export(write(d, PROVIDED), "metric", "America/New_York")
        run = samples[0]
        self.assertEqual((run.category, run.value), ("running", 1800.0))
        self.assertEqual(run.metadata["timing"], "DAY_ONLY")             # no start time invented
        self.assertEqual((run.end - run.start).days, 1)
        self.assertEqual(run.metadata["totals"]["distance_km"], 5.2)
        self.assertEqual(run.metadata["totals"]["avg_hr_summary"], 145.0)
        self.assertFalse(any(s.measure == "heart_rate" for s in samples))
        self.assertEqual(rep.importer_status, "UNVERIFIED_AGAINST_REAL_EXPORT")

    def test_statute_conversion(self):
        with tempfile.TemporaryDirectory() as d:
            samples, _ = ga.import_export(write(d, PROVIDED), "statute", "America/New_York")
        self.assertAlmostEqual(samples[0].metadata["totals"]["distance_km"], 5.2 * 1.609344)
        self.assertEqual(samples[0].metadata["totals"]["distance_as_exported"], "5.2 mi")
        self.assertIn("mi to km", samples[0].conversion)

    def test_units_required(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ga.GarminError):
                ga.import_export(write(d, PROVIDED), "km", "America/New_York")

    def test_messy_real_world_shapes(self):
        with tempfile.TemporaryDirectory() as d:
            samples, rep = ga.import_export(write(d, MESSY), "statute", "America/Chicago")
        self.assertEqual([s.category for s in samples], ["walking", "strength_training"])
        walk = samples[0]
        self.assertEqual(walk.metadata["timing"], "START_TIME")
        self.assertEqual(walk.value, 45 * 60 + 10)
        self.assertEqual((walk.end - walk.start).total_seconds(), 2710)
        self.assertEqual(walk.metadata["totals"]["calories"], 1045.0)       # "1,045"
        self.assertIsNone(walk.metadata["totals"]["avg_hr_summary"])        # "--"
        self.assertEqual(walk.metadata["other_columns_as_exported"], {"Aerobic TE": "2.1"})
        self.assertEqual(samples[1].value, 2400.5)
        reasons = [r for _, r in rep.rejected]
        self.assertTrue(any("unexpected date" in r for r in reasons))
        self.assertTrue(any("unexpected duration" in r for r in reasons))

    def test_not_activities_csv(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ga.GarminError):
                ga.import_export(write(d, "a,b\n1,2\n"), "metric", "UTC")


if __name__ == "__main__":
    unittest.main(verbosity=2)
