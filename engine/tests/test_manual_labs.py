"""Manual-lab tests: validation, provenance, corrections, tracker output."""
import json
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from app_state import App
from tracker.logs import normalize, LogError

DEMO = json.loads((Path(__file__).resolve().parents[2] / 'src/data/tracker_demo.json').read_text())


class ManualLabs(unittest.TestCase):
    def lab(self, **row):
        return {'type': 'LAB', 'at': date.today().isoformat(), 'results': [row]}

    def test_normalizes_alias_and_origin(self):
        r = normalize(self.lab(analyte='HbA1c', value=5.7, unit='%'))
        self.assertEqual(r['results'][0]['analyte'], 'hemoglobin A1c')
        self.assertEqual(r['results'][0]['analyte_original'], 'HbA1c')
        self.assertEqual(r['origin'], 'USER_TYPED')
        self.assertFalse(r['verified'])

    def test_rejects_limits_and_nonfinite(self):
        for a, v, u in [('glucose', 2001, 'mg/dL'), ('a1c', 26, '%'), ('ldl', 1001, 'mg/dL'), ('glucose', 'nan', 'mg/dL')]:
            with self.assertRaises(LogError):
                normalize(self.lab(analyte=a, value=v, unit=u))

    def test_qualitative_and_unknown(self):
        r = normalize(self.lab(analyte='Urine test custom', value_text='Negative', ref_text='Negative'))
        self.assertEqual(r['results'][0]['value_text'], 'Negative')
        self.assertEqual(r['results'][0]['analyte'], 'Urine test custom')

    def test_correction_and_tracker(self):
        with tempfile.TemporaryDirectory() as temp:
            app = App(root=Path(temp))
            app.create_user('Manual Lab Test', 'a long test passphrase')
            try:
                a = app.log_entry(self.lab(analyte='glucose', value=95, unit='mg/dL'))['id']
                b = app.log_entry(self.lab(analyte='glucose', value=97, unit='mg/dL'))['id']
                self.assertEqual([r.id for r in app.vault.history(b)], [a, b])
                t = app.tracker(DEMO, now=datetime.now().astimezone())
                self.assertEqual(t['labs']['annual_status'], 'CURRENT')
                self.assertEqual(len(t['labs']['typed_results']), 1)
                self.assertEqual(t['labs']['typed_results'][0]['value'], 97)
                self.assertEqual(t['labs']['typed_results'][0]['origin'], 'USER_TYPED')
                self.assertFalse(t['labs']['typed_results'][0]['verified'])
            finally:
                app.lock_user()


if __name__ == '__main__':
    unittest.main()
