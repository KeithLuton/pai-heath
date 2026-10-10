"""Record engine tests.

1. SCENARIO_001 gate: the engine runs on the fixture's documents (it has never
   seen the expected output) and is diffed against it field by field. Where the
   engine and the fixture disagree because the FIXTURE breaks the constitution,
   the disagreement is listed in FIXTURE_ISSUES for human sign-off — never
   silently accepted.
2. No-authority variant.
3. SCENARIO_002: a different case written after the engine, so no rule can be
   fitted to SCENARIO_001's strings.
"""
import json
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from records import lexicon                                  # noqa: E402
from records.floats import RecordDoc                         # noqa: E402
from records.pipeline import run                             # noqa: E402
from records.render import lint                              # noqa: E402

FIXTURE = json.loads((Path(__file__).resolve().parents[2] / "src" / "data" / "scenario_001.json").read_text())
QUESTION = "Could the metformin be causing the numbness?"

# Authorities arrive from retrieval with declared links. The fixture stubs predate that
# contract, so the harness declares the links each stub's text states. Input, not expected output.
AUTH_LINKS = {"AUTH-STUB-01": [{"from": "metformin", "to": "low vitamin B12"}],
              "AUTH-STUB-02": [{"from": "vitamin B12 deficiency", "to": "peripheral neuropathy"}]}

# Resolved 2026-10-07 by the owner (recorded in the fixture's verification.decisions):
#   1 list items without trigger word, 2 no section labels in Float text, 3 unprinted reference unit = null,
#   4 no added specimen, 5 CONTINUE_PLANNED, 6 sections only from real headers, 7 v0.4 disclaimer,
#   8 no-authority variant: no chain, F-12 as alternative.
FIXTURE_ISSUES = {}   # all 8 decided 2026-10-08; see the fixture's verification.decisions


def docs_from(fx):
    return [RecordDoc(k, v["title"], v["date"], v["text"], v["sha256"]) for k, v in fx["documents"].items()]


def run_001(with_auth=True):
    auth = [dict(a, links=AUTH_LINKS[a["id"]]) for a in FIXTURE["authority_inputs"]] if with_auth else []
    return run(docs_from(FIXTURE), QUESTION, "USER", auth)


def match_floats(engine, fixture):
    """Pair floats by document and overlapping span, then concept overlap (ids differ by design)."""
    pairs = {}
    for fx in fixture:
        p = fx["provenance"]
        cands = [e for e in engine if e["provenance"]["document_id"] == p["document_id"]
                 and e["provenance"]["char_start"] < p["char_end"] and p["char_start"] < e["provenance"]["char_end"]]
        exact = [e for e in cands if e["concept"].lower() == fx["concept"].lower()]
        cands = exact or [e for e in cands if lexicon.same_concept(e["concept"], fx["concept"])
                          or lexicon.core_terms(e["concept"]) & lexicon.core_terms(fx["concept"])] or cands
        assert len(cands) == 1, (fx["id"], [c["concept"] for c in cands])
        pairs[fx["id"]] = cands[0]
    return pairs


class Scenario001Gate(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.out = run_001()
        cls.e = cls.out["expected"]
        cls.x = FIXTURE["expected"]
        cls.pairs = match_floats(cls.e["floats"], cls.x["floats"])
        cls.map = {fx: en["id"] for fx, en in cls.pairs.items()}

    def test_float_count_and_one_to_one(self):
        self.assertEqual(len(self.e["floats"]), len(self.x["floats"]))
        self.assertEqual(len(set(self.map.values())), len(self.map))

    def test_float_fields(self):
        for fx in self.x["floats"]:
            en = self.pairs[fx["id"]]
            with self.subTest(float=fx["id"]):
                for k in ("kind", "speaker", "polarity", "certainty", "temporality", "experiencer",
                          "provenance_lock", "content_status"):
                    self.assertEqual(en[k], fx[k], k)
                for k in ("precision", "source", "value"):
                    self.assertEqual(en["event_date"].get(k), fx["event_date"].get(k), f"event_date.{k}")
                self.assertEqual(en["provenance"]["relay"], fx["provenance"]["relay"])
                self.assertEqual(en["section"], fx["section"])
                if fx["kind"] == "MEASUREMENT":
                    self.assertEqual(en["concept"], fx["concept"])
                self.assertEqual(en["original_text"], fx["original_text"])
                self.assertEqual((en["provenance"]["char_start"], en["provenance"]["char_end"]),
                                 (fx["provenance"]["char_start"], fx["provenance"]["char_end"]))
                if fx.get("action_state"):
                    self.assertEqual(en.get("action_state"), fx["action_state"])
                if fx.get("medication"):
                    self.assertEqual(en["medication"]["med_status"], fx["medication"]["med_status"])
                    self.assertEqual(en["medication"]["start_date"], "UNKNOWN")
                if fx.get("value"):
                    for k in ("numeric", "unit", "reference_low", "reference_high", "performing_lab", "primary_report_in_record_set", "lab_note"):
                        self.assertEqual(en["value"].get(k), fx["value"].get(k), f"value.{k}")
                    self.assertEqual(en["value"]["reference_unit"], fx["value"]["reference_unit"])
                if fx.get("range_comparison"):
                    self.assertEqual(en["range_comparison"]["result"], fx["range_comparison"]["result"])

    def test_roots(self):
        def groups(fs, key):
            g = {}
            for f in fs:
                g.setdefault(f["root"], set()).add(key(f))
            return sorted(sorted(v) for v in g.values())
        fixture_groups = groups(self.x["floats"], lambda f: f["id"])
        engine_groups = groups(self.e["floats"], lambda f: next(k for k, v in self.map.items() if v == f["id"]))
        self.assertEqual(engine_groups, fixture_groups)

    def test_mixes(self):
        def key(m):
            inv = {v: k for k, v in self.map.items()}
            return (m["relation"], tuple(inv.get(e, e) for e in m["endpoints"]))
        engine = {key(m): m for m in self.e["mixes"]}
        for m in self.x["mixes"]:
            k = (m["relation"], tuple(m["endpoints"]))
            with self.subTest(mix=m["id"]):
                self.assertIn(k, engine)
                en = engine[k]
                if m.get("temporal"):
                    self.assertEqual(en["temporal"]["temporal_resolution"], m["temporal"]["temporal_resolution"])
                    self.assertEqual(en["temporal"].get("order_basis"), m["temporal"].get("order_basis"))
                    self.assertIsNone(en["temporal"]["delta_days"])
                if m.get("comparability"):
                    self.assertEqual(en["comparability"]["class"], m["comparability"]["class"])
                    self.assertEqual(sorted(en["comparability"]["reasons"]), sorted(m["comparability"]["reasons"]))
                    self.assertEqual(en["values"]["prior_converted"], m["values_pmol_L"]["prior_converted"])
                    self.assertEqual(en["values"]["current"], m["values_pmol_L"]["current"])
                    self.assertEqual(en["delta"], m["delta_pmol_L"])
                    self.assertEqual(en["delta_percent"], m["delta_percent"])
                    self.assertEqual(en["direction"], m["direction"])
                    self.assertEqual(en["reference_ranges"]["prior_converted"], m["reference_ranges_pmol_L"]["prior_converted"])
                    self.assertEqual(en["conversion"]["result_floor"], "UNVERIFIED")   # EpiFloat floor
        self.assertEqual(len(engine), len(self.x["mixes"]), "no extra relations")

    def test_refusals(self):
        inv = {v: k for k, v in self.map.items()}
        engine = {(r["relation"], tuple(inv.get(e, e) for e in r["endpoints"])) for r in self.e["mixes_not_created"]}
        for r in self.x["mixes_not_created"]:
            with self.subTest(refusal=r):
                self.assertIn((r["relation"], tuple(r["endpoints"])), engine)

    def test_gaps_and_flags(self):
        for g in self.x["gaps"]:
            with self.subTest(gap=g["id"]):
                en = next(e for e in self.e["gaps"] if {s.lower() for s in e["sought"]} & {s.lower() for s in g["sought"]})
                self.assertEqual(en["result"], g["result"])
                self.assertEqual(en["coverage"], g["coverage"])
                self.assertEqual(en["date_window"][1], g["date_window"][1])
        self.assertEqual(sorted(f["type"] for f in self.e["flags"]), sorted(f["type"] for f in self.x["flags"]))
        self.assertEqual([f["type"] for f in self.e["flags_not_raised"]], [f["type"] for f in self.x["flags_not_raised"]])

    def test_stir(self):
        s, x = self.e["stir"], self.x["stir"]
        m = self.map
        self.assertEqual(s["opening"], x["opening"])
        self.assertEqual(sorted(s["subject"]["exposure"]), sorted(m[i] for i in x["subject"]["exposure"]))
        self.assertEqual(sorted(s["subject"]["finding"]), sorted(m[i] for i in x["subject"]["finding"]))
        self.assertEqual([(l["link"], l["source"]["type"], l["source"].get("also") or l["source"]["id"]) for l in s["chain"]],
                         [(l["link"], l["source"]["type"], l["source"].get("also") or l["source"]["id"]) for l in x["chain"]])
        self.assertIsNone(s["direct_link"]["source"])
        sub = set(s["subject"]["exposure"]) | set(s["subject"]["finding"])
        self.assertFalse(any(i["cites"].split(".")[0] in sub for i in s["support"]))
        conv = lambda c: m.get(c.split(".")[0], c) + (c[c.index("."):] if "." in c else "")
        self.assertEqual(sorted(i["cites"] for i in s["support"]), sorted(conv(i["cites"]) for i in x["support"]))
        for xi, si in zip(x["challenge"], s["challenge"]):
            with self.subTest(checklist=xi["checklist"]):
                self.assertEqual(si["checklist"], xi["checklist"])
                xc = [m.get(c, c) for c in xi.get("cites", [])]
                sc = si.get("cites", [])
                fl = [c for c in xc if c.startswith("F-")]
                self.assertEqual(sorted(c for c in sc if c.startswith("F-")), sorted(fl))
                self.assertEqual(sum(c.startswith(("GAP", "M-")) for c in sc), sum(c.startswith(("GAP", "M-")) for c in xc))
                self.assertEqual(si.get("status"), xi.get("status"))
        self.assertEqual([u["cites"] for u in s["unplaced"]], [m[u["cites"]] for u in x["unplaced"]])
        self.assertEqual(s["content_status"], "UNVERIFIED")
        self.assertIsNone(s["seats"]["RECORDER"]["verdict"])

    def test_rendering(self):
        text = self.e["rendering"]["reference_text"]
        self.assertEqual(lint(text, QUESTION), [])
        self.assertTrue(text.endswith(self.x["rendering"]["reference_text"].splitlines()[-1]))   # same disclaimer
        for needed in (f'"{QUESTION}"', "on your medication list", "around early December 2022",
                       "below the reference ranges", "different units", "could not be established",
                       "do not show when metformin was started", "No records from before December 2022",
                       "describe groups of people", "diabetic neuropathy as likely", "still on the differential",
                       "MMA test was not found", "does not diagnose"):
            self.assertIn(needed, text)
        ref = self.x["rendering"]["reference_text"]
        self.assertEqual(text.count("\n- "), ref.count("\n- "))      # same three clinician questions


class NoAuthorityVariant(unittest.TestCase):
    def test_variant(self):
        out = run_001(with_auth=False)["expected"]
        s = out["stir"]
        self.assertEqual(s["chain"], [])                                     # L1 has no source -> not added
        f12 = next(f["id"] for f in out["floats"] if f["concept"] == "B12 deficiency")
        self.assertIn(f12, next(c for c in s["challenge"] if c["checklist"] == 1)["cites"])   # decision 8
        self.assertTrue(all(u["cites"].endswith(".range_comparison") or u["cites"].startswith("F-")
                            for u in s["unplaced"]))
        self.assertEqual(sum(u["cites"].endswith(".range_comparison") for u in s["unplaced"]), 2)
        self.assertFalse(any(i.get("level") == "POPULATION" for i in s["support"]))
        text = out["rendering"]["reference_text"]
        self.assertIn("No medical reference sources are attached", text)
        self.assertNotIn("describe groups of people", text)
        self.assertEqual(lint(text, QUESTION), [])


class Scenario002Unseen(unittest.TestCase):
    """Written after the engine: ACE-inhibitor cough. Shares no strings with SCENARIO_001."""
    D1 = ("Patient reports dry cough since mid-January 2024. Denies fever, sputum. PMH: Hypertension. "
          "Meds: Lisinopril 10 mg daily. Labs: Potassium = 5.4 mmol/L (Ref: 3.5-5.1 mmol/L).")
    D2 = ("Patient reports dry cough since mid-January 2024. Exam: Lungs clear. "
          "Assessment: Possible ACE inhibitor cough; rule out asthma. "
          "Plan: Check spirometry, stop lisinopril, start losartan 50 mg daily.")
    Q = "Is the lisinopril behind my cough?"

    @classmethod
    def setUpClass(cls):
        import hashlib
        docs = [RecordDoc("A", "Primary care visit", "2024-02-01", cls.D1, hashlib.sha256(cls.D1.encode()).hexdigest()),
                RecordDoc("B", "Follow-up visit", "2024-03-05", cls.D2, hashlib.sha256(cls.D2.encode()).hexdigest())]
        cls.out = run(docs, cls.Q, "USER", [])["expected"]
        cls.by = {(f["provenance"]["document_id"], f["concept"]): f for f in cls.out["floats"]}

    def f(self, doc, concept):
        return next(v for (d, c), v in self.by.items() if d == doc and concept.lower() in c.lower())

    def test_floats(self):
        self.assertEqual(self.f("A", "lisinopril")["medication"]["med_status"], "LISTED")
        self.assertEqual(self.f("B", "lisinopril")["medication"]["med_status"], "STOP_PLANNED")
        los = self.f("B", "losartan")["medication"]
        self.assertEqual((los["med_status"], los["dose_text"], los["frequency_text"]), ("START_PLANNED", "50 mg", "daily"))
        self.assertEqual(self.f("B", "ACE inhibitor cough")["certainty"], "HEDGED")
        self.assertEqual(self.f("B", "asthma")["certainty"], "HYPOTHETICAL")
        self.assertEqual(self.f("A", "fever")["polarity"], "NEGATED")
        self.assertEqual(self.f("A", "sputum")["polarity"], "NEGATED")
        self.assertEqual(self.f("B", "lungs clear")["kind"], "OBSERVATION")
        k = self.f("A", "potassium")
        self.assertEqual((k["value"]["numeric"], k["value"]["reference_unit"], k["range_comparison"]["result"]),
                         (5.4, "mmol/L", "ABOVE"))
        self.assertEqual(self.f("A", "hypertension")["temporality"], "UNSPECIFIED")
        cough = self.f("A", "dry cough")
        self.assertEqual((cough["event_date"]["precision"], cough["event_date"]["value"]), ("APPROXIMATE", "2024-01 (mid)"))

    def test_short_repeat_is_not_copy_forward(self):
        roots = {f["root"] for f in self.out["floats"] if "dry cough" in f["concept"]}
        self.assertEqual(len(roots), 2)                     # 5 tokens < 8-token minimum

    def test_gaps_flags_stir(self):
        self.assertIn("EXPECTED_ITEM_NOT_FOUND", [f["type"] for f in self.out["flags"]])
        sought = [g["sought"] for g in self.out["gaps"]]
        self.assertTrue(any("lisinopril start date" in s for s in sought))
        s = self.out["stir"]
        self.assertEqual(s["opening"]["verbatim"], self.Q)
        self.assertEqual(s["chain"], [])
        alts = {c for c in next(c for c in s["challenge"] if c["checklist"] == 1)["cites"]}
        self.assertIn(self.f("B", "asthma")["id"], alts)
        text = self.out["rendering"]["reference_text"]
        self.assertEqual(lint(text, self.Q), [])
        self.assertIn("Was the spirometry test done", text)
        self.assertIn("No records from before January 2024 were provided.", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
