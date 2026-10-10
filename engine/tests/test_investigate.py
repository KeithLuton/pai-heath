"""AI investigation: free to explore and propose; no authority over evidence.

A scripted model plays the investigator and deliberately breaks every rule once —
an invented quote, causal wording, an unknown id, an uncited answer — so each guard
is shown to bounce the attempt back rather than let it through.
"""
import copy
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from investigate.agent import Workspace, investigate     # noqa: E402
from records.floats import RecordDoc                      # noqa: E402
from records.pipeline import run                          # noqa: E402

FIX = json.loads((Path(__file__).resolve().parents[2] / "src" / "data" / "scenario_001.json").read_text())
Q = "Could the metformin be causing the numbness?"


def scenario():
    docs = [RecordDoc(k, v["title"], v["date"], v["text"], v["sha256"]) for k, v in FIX["documents"].items()]
    return run(docs, Q, "USER", [])


class Scripted:
    """Plays a fixed plan; fills in ids from what the tools returned."""
    label = "scripted"

    def __init__(self):
        self.calls = []
        self.fid = None

    def chat(self, messages, max_tokens=900, temperature=0.0):
        self.calls.append(messages[-1]["content"])
        last = messages[-1]["content"]
        if last.startswith("RESULT ") and self.fid is None:
            m = re.search(r'"id": "(F-[0-9a-f]+)"', last)
            self.fid = m.group(1) if m else None
        n = len(self.calls)
        plan = [
            {"tool": "search_records", "query": "metformin"},
            {"tool": "get_record", "id": "__F__"},
            {"tool": "related", "id": "__F__"},
            {"tool": "get_record", "id": "F-doesnotexist"},
            {"tool": "propose", "kind": "HYPOTHESIS", "text": "Metformin is causing the numbness.", "cites": ["__F__"]},
            {"tool": "propose", "kind": "CONNECTION", "text": "Metformin and the low B12 results appear together.",
             "cites": ["__F__"], "quotes": [{"id": "__F__", "text": "words that are not in the record"}]},
            {"tool": "propose", "kind": "TEST", "text": "Would an MMA test help tell the two explanations apart?",
             "cites": ["__F__"]},
            {"tool": "propose", "kind": "QUESTION", "text": "When did the metformin start?", "cites": []},
            {"tool": "finish", "answer": "Metformin is on your list. It is linked to B12."},
            {"tool": "finish", "answer": "Your records list metformin [__F__]. When did it start?"},
        ]
        step = json.loads(json.dumps(plan[min(n - 1, len(plan) - 1)]).replace("__F__", self.fid or "F-x"))
        return "Thinking out loud first... " + json.dumps(step)

    def close(self):
        pass


class TestAgent(unittest.TestCase):
    def setUp(self):
        self.sc = scenario()
        self.before = copy.deepcopy(self.sc["expected"])
        self.inv = investigate(Scripted(), Workspace(self.sc), Q)
        self.res = [s.result for s in self.inv.steps]

    def test_finishes_with_cited_answer(self):
        self.assertEqual(self.inv.stopped, "FINISHED")
        self.assertTrue(all(s["cites"] or s["text"].endswith("?") for s in self.inv.answer["sentences"]))
        self.assertEqual(self.inv.answer["content_status"], "SPECULATIVE")

    def test_every_rule_bounces(self):
        self.assertIn("error", self.res[3])                                       # unknown id
        self.assertIn("wording not allowed", self.res[4]["errors"][0])            # causal wording
        self.assertIn("quotes not found", " ".join(self.res[5]["errors"]))        # invented quote
        self.assertIn("every sentence must cite", " ".join(self.res[8]["errors"]))  # uncited answer

    def test_ideas_kept_as_proposals_only(self):
        kinds = [p["kind"] for p in self.inv.proposals]
        self.assertEqual(kinds, ["TEST", "QUESTION"])
        self.assertTrue(all(p["standing"] == "MODEL_PROPOSAL" and p["content_status"] == "SPECULATIVE"
                            for p in self.inv.proposals))
        self.assertIn("No evidence cited", " ".join(self.inv.proposals[1]["notes"]))

    def test_evidence_untouched(self):
        self.assertEqual(json.dumps(self.sc["expected"], sort_keys=True), json.dumps(self.before, sort_keys=True))

    def test_full_trail_kept(self):
        d = self.inv.as_dict()
        self.assertEqual(len(d["steps"]), 10)
        self.assertEqual(d["steps"][0]["action"]["tool"], "search_records")
        self.assertTrue(d["steps"][0]["result"]["matches"])

    def test_step_limit(self):
        class Loops(Scripted):
            def chat(self, messages, max_tokens=900, temperature=0.0):
                return json.dumps({"tool": "search_records", "query": "numbness"})
        inv = investigate(Loops(), Workspace(self.sc), Q, max_steps=4)
        self.assertEqual((inv.stopped, inv.answer), ("STEP_LIMIT", None))


class TestApp(unittest.TestCase):
    def setUp(self):
        from app_state import App
        from test_app import FakeProvider
        self.tmp = Path(tempfile.mkdtemp())
        os.environ["PAI_HEALTH_HOME"] = str(self.tmp)
        self.app = App(root=self.tmp, transport=FakeProvider())
        self.app.create_user("Inv", "a long test passphrase")

    def tearDown(self):
        self.app.lock_user()
        os.environ.pop("PAI_HEALTH_HOME", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_external_needs_investigate_consent(self):
        from app_state import AppError, CONSENT_VERSION
        self.app.save_external("anthropic", "claude-x", "sk-ant-test-key", True, False, consent=CONSENT_VERSION)
        self.app.set_setting("ai_mode", "PSi")
        with self.assertRaises(AppError) as cm:
            self.app.investigate(Q)
        self.assertEqual(cm.exception.status, 409)
        with self.assertRaises(AppError):        # widening the scope asks again
            self.app.save_external("anthropic", "claude-x", "", True, False, allow_investigate=True)

    def test_job_saves_to_vault(self):
        import time
        for k, d in FIX["documents"].items():
            self.app.upload(f"{d['date']} {d['title']}.txt", d["text"].encode())
        self.app._investigator = lambda: Scripted()
        jid = self.app.investigate(Q)
        for _ in range(200):
            j = self.app.jobs.get(jid)
            if j["state"] in ("DONE", "FAILED"):
                break
            time.sleep(0.05)
        self.assertEqual(j["state"], "DONE", j["error"])
        saved = self.app.investigations()
        self.assertEqual(saved[0]["stopped"], "FINISHED")
        self.assertEqual(saved[0]["standing"], "MODEL_PROPOSAL")

    def test_off_mode_refuses(self):
        from app_state import AppError
        with self.assertRaises(AppError):
            self.app.investigate(Q)


if __name__ == "__main__":
    unittest.main()
