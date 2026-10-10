"""Lab-report extraction. Cases ported from the retired extract_labs tests,
with §2.5 applied: missing units stay null, reference units only when printed,
no added specimens, lab flags as separate INTERPRETATION Floats."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from records.floats import RecordDoc, extract     # noqa: E402


def run(text, title="Lab report", date=None):
    floats, unextracted = extract([RecordDoc("D", title, date, text, "sha")])
    return [f for f in floats if f["kind"] == "MEASUREMENT"], [f for f in floats if f["kind"] == "INTERPRETATION"], unextracted, text


def by(ms, concept):
    return next(f for f in ms if f["concept"] == concept)


class LabReports(unittest.TestCase):
    def check_slices(self, floats, text):
        for f in floats:
            p = f["provenance"]
            self.assertEqual(text[p["char_start"]:p["char_end"]], f["original_text"])
            self.assertEqual(f["content_status"], "UNVERIFIED")

    def test_quest_columns_flags_censored(self):
        t = ("QUEST DIAGNOSTICS - SPECIMEN REPORT\nCollected: 2024-01-15 08:30 Reported: 2024-01-15 11:45\n\n"
             "TEST NAME                  IN RANGE    OUT OF RANGE   UNITS    REF INTERVAL\n"
             "GLUCOSE, FASTING                       105 H          mg/dL    65-99\n"
             "CREATININE                 0.95                       mg/dL    0.70 - 1.30\n"
             "EGFR NON-AFRICAN           >60                        mL/min   >59\n"
             "POTASSIUM                  5.4 H                      mmol/L   3.5-5.0\n"
             "CHOLESTEROL, TOTAL         210 H                      mg/dL    <200\n")
        ms, flags, notes, text = run(t)
        self.assertEqual(len(ms), 5)
        self.check_slices(ms + flags, text)
        glu = by(ms, "glucose")
        self.assertEqual((glu["value"]["numeric"], glu["value"]["unit"], glu["value"]["reported_flag"]), (105, "mg/dL", "H"))
        self.assertEqual(glu["range_comparison"]["result"], "ABOVE")
        self.assertIsNone(glu["value"]["reference_unit"])                    # not printed
        self.assertEqual((glu["event_date"]["source"], glu["event_date"]["value"]), ("DOCUMENTED", "2024-01-15"))
        egfr = by(ms, "eGFR")
        self.assertEqual((egfr["value"]["numeric"], egfr["value"]["comparator"]), (60, ">"))
        self.assertEqual(egfr["range_comparison"]["result"], "WITHIN")
        self.assertEqual(len(flags), 3)                                        # glucose, potassium, cholesterol
        self.assertTrue(all("flagged H" in f["concept"] for f in flags))
        self.assertTrue(any("QUEST DIAGNOSTICS" in n["text"] for n in notes))

    def test_censored_values(self):
        t = ("HOSPITAL CRITICAL CARE LAB\nDate: 2024-02-01\n"
             "Troponin I       <0.01 ng/mL (<0.04)\n"
             "Ferritin         >2000 H ng/mL (30-400)\n")
        ms, flags, _, text = run(t)
        self.check_slices(ms, text)
        trop = by(ms, "troponin I")
        self.assertEqual((trop["value"]["numeric"], trop["value"]["comparator"]), (0.01, "<"))
        self.assertEqual(trop["range_comparison"]["result"], "WITHIN")
        self.assertEqual(trop["event_date"]["source"], "DOCUMENT_DATE_FALLBACK")   # report date = upper bound
        fer = by(ms, "ferritin")
        self.assertEqual((fer["value"]["comparator"], fer["range_comparison"]["result"]), (">", "ABOVE"))

    def test_qualitative_and_negated(self):
        t = ("URINALYSIS WITH MICROSCOPIC\nDate: 2024-03-01\n"
             "Urine Leukocyte Esterase    NEGATIVE (Ref: Negative)\n"
             "Nitrites                    NOT DETECTED (Ref: Negative)\n"
             "Urine Protein               30 mg/dL (Ref: Negative)\n")
        ms, _, _, text = run(t)
        self.assertEqual(len(ms), 3)
        self.check_slices(ms, text)
        le = by(ms, "urine leukocyte esterase")
        self.assertEqual((le["polarity"], le["value"]["qualitative"], le["range_comparison"]["result"]),
                         ("NEGATED", "NEGATIVE", "WITHIN"))
        self.assertEqual(by(ms, "urine nitrites")["polarity"], "NEGATED")
        prot = by(ms, "urine protein")
        self.assertEqual((prot["value"]["numeric"], prot["range_comparison"]["result"]), (30, "ABOVE"))

    def test_two_draws_one_report(self):
        t = ("METABOLIC LAB PROFILE - SERIAL MONITORING\n\n"
             "Collected: 2024-01-15 08:00\nGlucose, Fasting    92 mg/dL 70-99\n\n"
             "Collected: 2024-01-15 12:30\nGlucose, Random     158 H mg/dL 70-140\n")
        ms, _, _, text = run(t)
        self.assertEqual(len(ms), 2)
        a, b = ms
        self.assertNotEqual(a["root"], b["root"])
        self.assertEqual([m["value"]["numeric"] for m in ms], [92, 158])
        self.assertEqual([m["range_comparison"]["result"] for m in ms], ["WITHIN", "ABOVE"])

    def test_no_invented_units_or_specimens(self):
        t = ("Specimen Lab - Comprehensive Metabolic Panel\nCollected: 2024-01-15 08:30\n"
             "Glucose 98 mg/dL 70-99\nCreatinine 1.1 mg/dL 0.7-1.3\neGFR 72 >60\n")
        ms, _, _, _ = run(t)
        egfr = by(ms, "eGFR")
        self.assertIsNone(egfr["value"]["unit"])                             # not printed -> null (§2.5)
        self.assertIn("none assumed", egfr["note"])
        self.assertEqual(egfr["range_comparison"]["result"], "WITHIN")
        self.assertFalse(any("serum" in m["concept"] for m in ms))

    def test_prose_note_not_treated_as_report(self):
        t = ("Patient reports numbness. Labs: Vitamin B12 = 180 pg/mL (Ref: 200-900).")
        ms, _, notes, _ = run(t, title="PCP Note", date="2023-01-15")
        self.assertEqual(len(ms), 1)                                          # statement path, not line path
        self.assertEqual(ms[0]["provenance"]["relay"], "TRANSCRIBED")


if __name__ == "__main__":
    unittest.main(verbosity=2)
