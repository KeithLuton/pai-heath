"""Apple Health importer tests — synthetic fixtures only.

These prove the importer handles the published structure and its known
traps. They do NOT prove it handles a real export; that requires a real file
(importer status stays UNVERIFIED_AGAINST_REAL_EXPORT until then).
"""
import io
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from devices import apple_health as ah                     # noqa: E402
from devices.dedupe import daily_totals, drop_exact_duplicates, place, series_key  # noqa: E402

FX = Path(__file__).parent / "fixtures" / "apple"


class ProvidedSample(unittest.TestCase):
    def test_minimal_export(self):
        samples, rep = ah.import_export(FX / "provided_sample.xml")
        self.assertEqual(rep.importer_status, "UNVERIFIED_AGAINST_REAL_EXPORT")
        self.assertEqual(rep.export_date, "2024-01-15 10:30:00 -0500")
        self.assertEqual(sorted(s.measure for s in samples), ["heart_rate", "steps", "workout"])
        hr = next(s for s in samples if s.measure == "heart_rate")
        self.assertEqual((hr.value, hr.unit), (72.0, "bpm"))
        # No device field in the record: the model is NOT inferred from the source name.
        self.assertIsNone(hr.device_model)
        self.assertEqual(hr.device_class, "OTHER_SOURCE")
        wk = next(s for s in samples if s.measure == "workout")
        self.assertEqual((wk.category, wk.value), ("Running", 1800.0))
        self.assertEqual(wk.metadata["totals_as_exported"]["distance"], "5.2 km")

    def test_profile_not_imported(self):
        samples, _ = ah.import_export(FX / "provided_sample.xml")
        self.assertFalse(any("Characteristic" in s.provenance.raw_type for s in samples))


class StructuredSample(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.samples, cls.rep = ah.import_export(FX / "structured_sample.xml")

    def test_doctype_parses(self):
        self.assertEqual(self.rep.export_date, "2026-10-05 21:10:44 -0400")

    def test_device_field(self):
        hr = next(s for s in self.samples if s.measure == "heart_rate")
        self.assertEqual((hr.device_model, hr.device_class), ("Watch7,2", "WATCH"))
        self.assertEqual(hr.metadata["HKMetadataKeyHeartRateMotionContext"], "0")
        self.assertEqual(hr.source_name, "Demo’s Apple Watch")   # verbatim, curly apostrophe kept

    def test_hrv_method(self):
        hrv = next(s for s in self.samples if s.measure == "hrv")
        self.assertEqual((hrv.method, hrv.value), ("SDNN", 41.3))
        self.assertEqual(hrv.metadata["instantaneous_bpm_count"], 2)
        self.assertEqual(series_key(hrv), ("hrv", "SDNN", "Demo’s Apple Watch"))

    def test_conversions_logged(self):
        spo2 = next(s for s in self.samples if s.measure == "spo2")
        self.assertAlmostEqual(spo2.value, 96.0)
        self.assertIn("fraction to percent", spo2.conversion)
        glu = next(s for s in self.samples if s.measure == "glucose")
        self.assertAlmostEqual(glu.value, 6.7 * 18.0156, places=3)
        self.assertIn("mmol/L to mg/dL", glu.conversion)
        dist = next(s for s in self.samples if s.measure == "distance")
        self.assertAlmostEqual(dist.value, 0.804672)

    def test_sleep_stages_per_source(self):
        stages = [s for s in self.samples if s.measure == "sleep_stage"]
        self.assertEqual(sorted(s.category for s in stages), ["ASLEEP_UNSPECIFIED", "CORE", "DEEP"])
        self.assertEqual(len({series_key(s) for s in stages}), 2)   # watch and app never merged

    def test_rejects_and_unmapped(self):
        reasons = " | ".join(r for _, r in self.rep.rejected)
        self.assertIn("non-numeric value", reasons)
        self.assertIn("endDate before startDate", reasons)
        self.assertEqual(self.rep.unmapped_types.get("HKQuantityTypeIdentifierEnvironmentalAudioExposure"), 1)
        self.assertEqual(self.rep.unmapped_types.get("ActivitySummary"), 1)

    def test_workout_statistics_children(self):
        wk = next(s for s in self.samples if s.measure == "workout")
        self.assertEqual(wk.value, 31.5 * 60)
        self.assertEqual(wk.metadata["totals_as_exported"]["ActiveEnergyBurned"], "141.2 kcal")

    def test_provenance(self):
        for s in self.samples:
            self.assertEqual(len(s.provenance.file_sha256), 64)
            self.assertGreaterEqual(s.provenance.record_index, 0)

    def test_exact_duplicates(self):
        kept, dropped = drop_exact_duplicates(self.samples)
        self.assertEqual(dropped, 1)
        self.assertEqual(sum(1 for s in kept if s.measure == "heart_rate"), 1)

    def test_overlapping_steps(self):
        kept, _ = drop_exact_duplicates(self.samples)
        placed = place(kept)
        roles = {(p.sample.source_name, p.sample.value): p.role for p in placed if p.sample.measure == "steps"}
        self.assertEqual(roles[("Demo’s Apple Watch", 1010.0)], "PRIMARY")
        self.assertEqual(roles[("Demo’s iPhone", 980.0)], "SUPERSEDED")
        self.assertEqual(roles[("Demo’s iPhone", 400.0)], "PRIMARY")   # no watch data then
        totals = daily_totals(placed, "steps")
        self.assertEqual(list(totals.values()), [1410.0])              # not 2390

    def test_user_priority_overrides_default(self):
        kept, _ = drop_exact_duplicates(self.samples)
        placed = place(kept, {"steps": ["Demo’s iPhone", "Demo’s Apple Watch"]})
        totals = daily_totals(placed, "steps")
        self.assertEqual(list(totals.values()), [1380.0])


class Packaging(unittest.TestCase):
    def test_zip_export(self):
        with tempfile.TemporaryDirectory() as d:
            z = Path(d) / "export.zip"
            with zipfile.ZipFile(z, "w") as zf:
                zf.write(FX / "structured_sample.xml", "apple_health_export/export.xml")
                zf.writestr("apple_health_export/export_cda.xml", "<ClinicalDocument/>")
            samples, rep = ah.import_export(z)
            self.assertIn("apple_health_export/export.xml", rep.file_name)
            self.assertGreater(len(samples), 10)

    def test_entity_declaration_refused(self):
        bomb = ('<?xml version="1.0"?><!DOCTYPE HealthData [<!ENTITY a "aaaaaaaaaa">'
                '<!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">]><HealthData>&b;</HealthData>')
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "export.xml"
            p.write_text(bomb)
            with self.assertRaises(ah.AppleHealthError):
                ah.import_export(p)

    def test_not_a_health_export(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "export.xml"
            p.write_text("<?xml version='1.0'?><Other/>")
            with self.assertRaises(ah.AppleHealthError):
                ah.import_export(p)


if __name__ == "__main__":
    unittest.main(verbosity=2)
