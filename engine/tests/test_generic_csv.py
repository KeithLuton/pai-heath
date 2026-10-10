import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from devices import generic_csv as gc        # noqa: E402

FX = Path(__file__).parent / "fixtures" / "csv"


class GenericCsv(unittest.TestCase):
    def test_provided_sample(self):
        samples, rep = gc.import_export(FX / "hourly_example.csv", FX / "hourly_example.mapping.json")
        self.assertEqual(rep.importer_status, "USER_MAPPED")
        steps = [s for s in samples if s.measure == "steps"]
        self.assertEqual([s.value for s in steps], [1234.0, 2456.0])
        self.assertEqual((steps[0].end - steps[0].start).total_seconds(), 3600)
        self.assertEqual(steps[0].start.utcoffset().total_seconds(), -5 * 3600)
        dist = [s for s in samples if s.measure == "distance"]
        self.assertAlmostEqual(dist[0].value, 0.85 * 1.609344)
        self.assertIn("mi to km", dist[0].conversion)
        cal = [s for s in samples if s.measure == "energy_unspecified"]
        self.assertEqual(len(cal), 2)                       # never promoted to active energy
        self.assertEqual(len(samples[0].metadata["mapping_sha256"]), 64)

    def _mapping(self, d, **change):
        m = json.loads((FX / "hourly_example.mapping.json").read_text())
        for k, v in change.items():
            m[k] = v
        p = Path(d) / "m.json"
        p.write_text(json.dumps(m))
        return p

    def test_mapping_validation(self):
        with tempfile.TemporaryDirectory() as d:
            for bad in (
                {"interval": {"kind": "instant"}},                                             # steps need a period
                {"columns": {"steps": {"measure": "steps", "unit": "km"}}},                     # wrong unit
                {"columns": {"steps": {"measure": "mood", "unit": "count"}}},                   # unknown measure
                {"timestamp": {"date_column": "date", "time_column": "time", "timezone": "EST5"}},
            ):
                with self.subTest(bad=bad), self.assertRaises(gc.MappingError):
                    gc.import_export(FX / "hourly_example.csv", self._mapping(d, **bad))

    def test_missing_column_and_unmapped(self):
        with tempfile.TemporaryDirectory() as d:
            m = self._mapping(d, columns={"steps": {"measure": "steps", "unit": "count"}})
            _, rep = gc.import_export(FX / "hourly_example.csv", m)
            self.assertEqual(set(rep.unmapped_types), {"distance", "calories"})
            m2 = self._mapping(d, columns={"heartrate": {"measure": "heart_rate", "unit": "bpm"}})
            with self.assertRaises(gc.MappingError):
                gc.import_export(FX / "hourly_example.csv", m2)

    def test_bad_rows_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            csvp = Path(d) / "x.csv"
            csvp.write_text("date,time,steps,distance,calories\n2024-01-15,8am,10,0.1,1\n2024-01-15,09:00:00,abc,--,2\n")
            samples, rep = gc.import_export(csvp, FX / "hourly_example.mapping.json")
            reasons = " | ".join(r for _, r in rep.rejected)
            self.assertIn("doesn't match declared format", reasons)
            self.assertIn("non-numeric value 'abc'", reasons)
            self.assertEqual([s.measure for s in samples], ["energy_unspecified"])   # "--" skipped as missing


if __name__ == "__main__":
    unittest.main(verbosity=2)
