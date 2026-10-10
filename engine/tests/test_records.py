"""Segmenter + context classifier tests.

Three sets, none of which the code was written to memorize:
  1. Constitution §2.8 acceptance table (verbatim cases).
  2. Adversarial set — the phrasings PAi Legal's negation code and the Gemini
     classifier got wrong.
  3. A fresh note the classifier has never seen, covering FH, R/O, plans,
     "unlikely", pseudo-negation and multi-target statements.
SCENARIO_001 documents are used only to check statement boundaries.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from records.segment import segment            # noqa: E402
from records.context import classify           # noqa: E402

A, N = "AFFIRMED", "NEGATED"
D, H, Y = "DEFINITE", "HEDGED", "HYPOTHETICAL"
C, P, L, U = "CURRENT", "HISTORICAL", "PLANNED", "UNSPECIFIED"
PT, FAM = "PATIENT", "FAMILY"


def run(text):
    return [(st.text(text), classify(text, st)) for st in segment("T", text)]


def axes(r):
    return (r.polarity, r.certainty, r.temporality, r.experiencer)


class Base(unittest.TestCase):
    def check(self, text, expected):
        got = run(text)
        self.assertEqual([g[0] for g in got], [e[0] for e in expected], f"segmentation of {text!r}")
        for (seg, r), (_, *want) in zip(got, expected):
            with self.subTest(text=text, statement=seg):
                self.assertEqual(axes(r), tuple(want), f"triggers={[(t.axis, t.text) for t in r.triggers]}")


class ConstitutionTable(Base):
    def test_rows(self):
        self.check("ROS: Negative for numbness.", [("numbness", N, D, C, PT)])
        self.check("Denies chest pain, shortness of breath, palpitations, dizziness, or numbness.",
                   [(x, N, D, C, PT) for x in ("chest pain", "shortness of breath", "palpitations", "dizziness", "numbness")])
        self.check("Neuro: numbness absent", [("numbness absent", N, D, C, PT)])
        self.check("Mother had multiple sclerosis.", [("Mother had multiple sclerosis", A, D, P, FAM)])
        self.check("Rule out B12 deficiency.", [("Rule out B12 deficiency", A, Y, C, PT)])
        self.check("Hx of numbness in 2019, resolved.", [("Hx of numbness in 2019, resolved", A, D, P, PT)])
        self.check("Findings not consistent with demyelination.", [("Findings not consistent with demyelination", N, D, C, PT)])
        self.check("No acute distress. Numbness in bilateral feet.",
                   [("acute distress", N, D, C, PT), ("Numbness in bilateral feet", A, D, C, PT)])


class Adversarial(Base):
    def test_rows(self):
        self.check("Patient reports numbness. Not drawn: B12.",
                   [("Patient reports numbness", A, D, C, PT), ("Not drawn: B12", N, D, C, PT)])
        self.check("Father denies smoking; patient smokes.",
                   [("Father denies smoking", N, D, C, FAM), ("patient smokes", A, D, C, PT)])
        self.check("Numbness worse than prior visit.", [("Numbness worse than prior visit", A, D, C, PT)])
        self.check("Possible gait improvement? Pt unsure.",
                   [("Possible gait improvement?", A, Y, C, PT), ("Pt unsure", A, D, C, PT)])
        self.check("Mother had a stroke.", [("Mother had a stroke", A, D, P, FAM)])
        self.check("Plan: Check MMA, continue Metformin.",
                   [("Check MMA", A, D, L, PT), ("continue Metformin", A, D, L, PT)])
        self.check("Numbness not present today.", [("Numbness not present today", N, D, C, PT)])
        self.check("No improvement in numbness.", [("improvement in numbness", A, D, C, PT)])
        self.check("Pt has no insurance; reports numbness.",
                   [("Pt has no insurance", N, D, C, PT), ("numbness", A, D, C, PT)])
        self.check("PMH: Type 2 Diabetes.", [("Type 2 Diabetes", A, D, U, PT)])

    def test_multi_target(self):
        text = "Neuropathy without evidence of myelopathy."
        st = segment("T", text)[0]
        whole = classify(text, st)
        self.assertTrue(whole.review, "mixed statement must be flagged for per-concept classification")
        neuro = classify(text, st, (0, 10))
        myelo = classify(text, st, (text.index("myelopathy"), text.index("myelopathy") + 10))
        self.assertEqual(neuro.polarity, A)
        self.assertEqual(myelo.polarity, N)

    def test_no_scope_limited(self):
        text = "Pt has no insurance and reports numbness."
        st = segment("T", text)[0]
        i = text.index("numbness")
        self.assertEqual(classify(text, st, (i, i + 8)).polarity, A)


class FreshNote(Base):
    NOTE = ("HPI: 61 y/o with fatigue for 3 weeks. Denies fever or chills. "
            "ROS: Positive for fatigue. Negative for chest pain, palpitations. "
            "PMH: HTN, hyperlipidemia. "
            "FH: Mother with breast cancer. Father had MI at 60. "
            "Exam: No edema. Gram negative rods on prior culture. "
            "Assessment: Fatigue, unlikely to be thyroid related; cannot exclude anemia. Chest pain resolved. "
            "Plan: Start lisinopril 10 mg daily; will return if fever develops.")

    def test_fresh_note(self):
        self.check(self.NOTE, [
            ("61 y/o with fatigue for 3 weeks", A, D, C, PT),
            ("fever", N, D, C, PT), ("chills", N, D, C, PT),
            ("fatigue", A, D, C, PT),
            ("chest pain", N, D, C, PT), ("palpitations", N, D, C, PT),
            ("HTN", A, D, U, PT), ("hyperlipidemia", A, D, U, PT),
            ("Mother with breast cancer", A, D, U, FAM),
            ("Father had MI at 60", A, D, P, FAM),
            ("edema", N, D, C, PT),
            ("Gram negative rods on prior culture", A, D, P, PT),
            ("Fatigue, unlikely to be thyroid related", N, H, C, PT),
            ("cannot exclude anemia", A, Y, C, PT),
            ("Chest pain resolved", A, D, P, PT),
            ("Start lisinopril 10 mg daily", A, D, L, PT),
            ("will return if fever develops", A, Y, L, PT),
        ])

    def test_mixed_statement_flagged(self):
        st = next(s for s in segment("T", self.NOTE) if s.text(self.NOTE).startswith("Fatigue, unlikely"))
        self.assertTrue(classify(self.NOTE, st).review)


class Scenario001Boundaries(unittest.TestCase):
    DOC1 = ("Patient reports gradual onset of numbness in bilateral feet starting 'around early December 2022'. "
            "Denies weakness, bowel/bladder changes. PMH: Type 2 Diabetes. Meds: Metformin 1000mg BID. "
            "Labs: Vitamin B12 = 180 pg/mL (Ref: 200-900).")
    DOC2 = ("Patient reports gradual onset of numbness in bilateral feet starting 'around early December 2022'. "
            "Denies weakness. Exam: decreased vibration sense in toes. Assessment: Likely diabetic neuropathy, "
            "though B12 deficiency remains on differential. Plan: Check MMA, continue Metformin.")
    DOC3 = "Vitamin B12: 140 pmol/L (Ref: 150-600 pmol/L). Note: Unit changed from pg/mL to pmol/L."

    def test_offsets_exact(self):
        for doc in (self.DOC1, self.DOC2, self.DOC3):
            for st in segment("D", doc):
                self.assertEqual(doc[st.span.start:st.span.end], st.text(doc))
                self.assertNotIn(st.text(doc)[:1], " ,;:")

    def test_sections_and_items(self):
        got = [(s.section, s.text(self.DOC2)) for s in segment("D", self.DOC2)]
        self.assertIn(("EXAM", "decreased vibration sense in toes"), got)
        self.assertIn(("ASSESSMENT", "Likely diabetic neuropathy"), got)
        self.assertIn(("ASSESSMENT", "B12 deficiency remains on differential"), got)
        self.assertIn(("PLAN", "Check MMA"), got)
        self.assertIn(("PLAN", "continue Metformin"), got)
        got3 = [(s.section, s.text(self.DOC3)) for s in segment("D", self.DOC3)]
        self.assertEqual(got3[0], ("UNLABELED", "Vitamin B12: 140 pmol/L (Ref: 150-600 pmol/L)"))
        self.assertEqual(got3[1][0], "NOTE")

    def test_axes_on_scenario(self):
        r = {s.text(self.DOC2): classify(self.DOC2, s) for s in segment("D", self.DOC2)}
        self.assertEqual(r["weakness"].polarity, N)
        self.assertEqual(r["decreased vibration sense in toes"].polarity, A)
        self.assertEqual(r["Likely diabetic neuropathy"].certainty, H)
        self.assertEqual(r["B12 deficiency remains on differential"].certainty, Y)
        self.assertEqual(r["Check MMA"].temporality, L)


class Audit(unittest.TestCase):
    def test_triggers_recorded(self):
        text = "Denies chest pain."
        st = segment("T", text)[0]
        r = classify(text, st)
        self.assertEqual([(t.axis, t.text) for t in r.triggers], [("polarity", "Denies")])


if __name__ == "__main__":
    unittest.main(verbosity=2)
