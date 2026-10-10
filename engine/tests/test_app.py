"""App layer: users, AI setup, ingest upload, the AI double-check and the server routes.

External AI is exercised through a fake transport (no network). The fake model
deliberately mixes a real discrepancy, an invented quote and agreement, so the
guards are tested, not just the happy path.
"""
import http.client
import json
import os
import re
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app_state import App, AppError                 # noqa: E402
from review.check import PAGE_PROMPT, VALUE_PROMPT  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures" / "ingest"
SCEN = json.loads((Path(__file__).resolve().parents[2] / "src" / "data" / "scenario_001.json").read_text())
PW = "a long test passphrase"


class FakeProvider:
    """Stands in for integrate.api.nvidia.com. Records what was sent."""

    def __init__(self):
        self.calls = []

    def __call__(self, method, url, headers, body, timeout):
        self.calls.append((method, url, json.loads(body) if body else None))
        if url.endswith("/models"):
            return {"data": [{"id": "meta/llama-3.3-70b-instruct"}, {"id": "nvidia/nemotron-nano-vl-8b"}]}
        req = json.loads(body)
        system = req["messages"][0]["content"]
        user = req["messages"][1]["content"]
        if system == PAGE_PROMPT:
            text = user[1]["text"].split("\n", 1)[1]
            num = re.search(r"\d+(\.\d+)?", text)
            reply = {"verdict": "DISAGREES", "differences": [
                {"text_says": num.group(0), "page_shows": "something else"},
                {"text_says": "WORDS THAT ARE NOT ON THE PAGE", "page_shows": "x"}]}
        else:
            items = json.loads(user)["items"]
            out = []
            for k, it in enumerate(items):
                if k == 0:
                    out.append({"id": it["id"], "verdict": "DISAGREE", "source_says": it["source_line"][:12],
                                "reason": "unit differs"})
                elif k == 1:
                    out.append({"id": it["id"], "verdict": "DISAGREE", "source_says": "INVENTED QUOTE",
                                "reason": "made up"})
                else:
                    out.append({"id": it["id"], "verdict": "AGREE", "source_says": "", "reason": ""})
            reply = {"items": out}
        return {"choices": [{"message": {"content": "Here you go:\n" + json.dumps(reply)}}]}


def wait(app, jid, timeout=120):
    end = time.time() + timeout
    while time.time() < end:
        j = app.jobs.get(jid)
        if j["state"] in ("DONE", "FAILED"):
            return j
        time.sleep(0.05)
    raise AssertionError("job did not finish")


class AppCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pai-app-test-"))
        os.environ["PAI_HEALTH_HOME"] = str(self.tmp)
        self.fake = FakeProvider()
        self.app = App(root=self.tmp, transport=self.fake)

    def tearDown(self):
        self.app.lock_user()
        os.environ.pop("PAI_HEALTH_HOME", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def user(self):
        return self.app.create_user("Test Person", PW)

    def psi(self, pages=True, values=True):
        from app_state import CONSENT_VERSION
        self.app.save_external("nemotron", "nvidia/nemotron-nano-vl-8b", "nvapi-test-key-123", pages, values,
                               consent=CONSENT_VERSION if (pages or values) else None)
        self.app.set_setting("ai_mode", "PSi")


class TestUsers(AppCase):
    def test_create_lock_unlock(self):
        u = self.user()
        self.assertEqual(self.app.users(), [{"id": u["id"], "name": "Test Person"}])
        self.app.lock_user()
        with self.assertRaises(AppError):
            self.app.need_vault()
        with self.assertRaises(AppError):
            self.app.unlock(u["id"], "wrong passphrase!!")
        self.app.unlock(u["id"], PW)
        self.assertEqual(self.app.vault.settings()["ai_mode"], "None")

    def test_two_users_same_name_kept_apart(self):
        a = self.user()
        b = self.app.create_user("Test Person", "another passphrase!")
        self.assertNotEqual(a["id"], b["id"])

    def test_short_passphrase(self):
        with self.assertRaises(AppError):
            self.app.create_user("X", "short")


class TestAISetup(AppCase):
    def test_system_scan_and_options(self):
        s = self.app.system()
        self.assertGreater(s["hardware"]["total_ram_gb"], 0)
        self.assertTrue(s["analysis_models"])
        self.assertEqual({p["id"] for p in s["providers"]}, {"nemotron", "openai", "anthropic"})

    def test_external_models_and_key_never_exposed(self):
        self.user()
        m = self.app.external_models("nemotron", "nvapi-test-key-123")
        self.assertEqual(m["suggested"], "nvidia/nemotron-nano-vl-8b")
        self.psi()
        state = json.dumps(self.app.app_state())
        self.assertNotIn("nvapi-test-key-123", state)
        self.assertTrue(self.app.ai_public()["external"]["key_set"])
        audit = (self.tmp / "users" / self.app.user / "vault" / "audit.log").read_text()
        self.assertNotIn("nvapi-test-key-123", audit)

    def test_sharing_needs_consent_and_is_audited(self):
        from app_state import CONSENT_VERSION
        self.user()
        with self.assertRaises(AppError) as cm:
            self.app.save_external("anthropic", "claude-x", "sk-ant-test-key", True, False)
        self.assertEqual(cm.exception.status, 409)
        self.app.save_external("anthropic", "claude-x", "sk-ant-test-key", True, False, consent=CONSENT_VERSION)
        # same scope again: no new consent needed; widening scope needs it again
        self.app.save_external("anthropic", "claude-x", "", True, False)
        with self.assertRaises(AppError):
            self.app.save_external("anthropic", "claude-x", "", True, True)
        self.app.save_external("anthropic", "claude-x", "", False, False)
        events = [json.loads(l)["event"] for l in (self.tmp / "users" / self.app.user / "vault" / "audit.log")
                  .read_text().splitlines()]
        self.assertEqual(events.count("CONSENT_GIVEN"), 1)
        self.assertEqual(events.count("CONSENT_WITHDRAWN"), 1)
        self.assertIsNone(self.app.ai_public()["external"]["consent"])
        self.assertIn("leave this computer", self.app.consent_text("anthropic")["text"])

    def test_mode_needs_setup(self):
        self.user()
        with self.assertRaises(ValueError):
            self.app.set_setting("ai_mode", "PSi")
        with self.assertRaises(ValueError):
            self.app.set_setting("ai_mode", "PAi")


class TestIngestCheck(AppCase):
    def upload_scenario(self):
        out = []
        for k, d in SCEN["documents"].items():
            name = f"{d['date']} {d['title']}.txt"
            out.append(self.app.upload(name, d["text"].encode()))
        return out

    def test_upload_without_ai_is_not_checked_and_says_why(self):
        self.user()
        res = self.upload_scenario()
        wait(self.app, res[-1]["job"])
        docs = self.app.documents()
        self.assertEqual(len(docs), 3)
        self.assertTrue(all(d["status"] == "NOT_CHECKED" for d in docs), docs)
        detail = self.app.document(docs[0]["doc_id"])
        self.assertIn("AI is off", detail["report"]["items"][0]["reason"])
        self.assertFalse(self.fake.calls)

    def test_duplicate_upload(self):
        self.user()
        self.upload_scenario()
        again = self.app.upload("2023-01-10 PCP Note.txt", SCEN["documents"]["DOC-1"]["text"].encode())
        self.assertTrue(again["documents"][0]["duplicate"])
        self.assertIsNone(again["job"])

    def test_value_check_guards_and_locks(self):
        self.user()
        self.psi()
        res = self.upload_scenario()
        for r in res:
            self.assertEqual(wait(self.app, r["job"])["state"], "DONE")
        doc = next(d for d in self.app.documents() if d["summary"]["items_disagree"])
        detail = self.app.document(doc["doc_id"])
        items = detail["report"]["items"]
        self.assertEqual(items[0]["verdict"], "DISAGREE")
        self.assertEqual(items[1]["verdict"], "CANNOT_TELL")
        self.assertEqual(items[1]["guard"], "QUOTE_NOT_IN_SOURCE")
        f0 = next(f for f in detail["floats"] if f["id"] == items[0]["id"])
        self.assertEqual(f0["provenance_lock"], "CHECKER_DISCREPANCY")
        self.assertEqual(f0["content_status"], "UNVERIFIED")
        self.assertEqual(doc["status"], "NEEDS_REVIEW")
        # the scenario the rest of the app reads carries the same lock
        sf = next(f for f in self.app.scenario()["expected"]["floats"] if f["id"] == items[0]["id"])
        self.assertEqual(sf["provenance_lock"], "CHECKER_DISCREPANCY")
        # a person settles it
        after = self.app.resolve(doc["doc_id"], f"item:{items[0]['id']}", "EXTRACTION_WRONG")
        f0 = next(f for f in after["floats"] if f["id"] == items[0]["id"])
        self.assertEqual((f0["provenance_lock"], f0["lock_basis"]), ("FAILED", "REJECTED_BY_USER"))
        self.assertFalse(any(i["target"] == f"item:{items[0]['id']}" for i in after["issues"]))

    def test_external_calls_audited_without_content(self):
        self.user()
        self.psi()
        res = self.upload_scenario()
        for r in res:
            wait(self.app, r["job"])
        audit = [json.loads(l) for l in (self.tmp / "users" / self.app.user / "vault" / "audit.log")
                 .read_text().splitlines()]
        calls = [e for e in audit if e["event"] == "EXTERNAL_AI_CALL"]
        self.assertTrue(calls)
        self.assertTrue(all(c["provider"] == "nemotron" and c["bytes_sent"] > 0 for c in calls))
        raw = json.dumps(audit)
        self.assertNotIn("metformin", raw.lower())

    def test_external_not_allowed_sends_nothing(self):
        self.user()
        self.psi(pages=False, values=False)
        self.fake.calls.clear()
        res = self.upload_scenario()
        for r in res:
            wait(self.app, r["job"])
        self.assertFalse([c for c in self.fake.calls if c[1].endswith("/chat/completions")])
        d = self.app.document(self.app.documents()[0]["doc_id"])
        self.assertIn("haven't allowed", d["report"]["items"][0]["reason"])

    def test_pdf_page_check_and_page_image(self):
        self.user()
        self.psi(pages=True, values=False)
        res = self.app.upload("lab_report_sample.pdf", (FIX / "lab_report_sample.pdf").read_bytes())
        self.assertEqual(wait(self.app, res["job"])["state"], "DONE")
        did = res["documents"][0]["doc_id"]
        d = self.app.document(did)
        p1 = d["report"]["pages"][0]
        self.assertEqual(p1["verdict"], "DISAGREES")
        self.assertEqual(len(p1["differences"]), 1)            # invented one discarded
        self.assertIn("discarded", p1["note"])
        sent = [c for c in self.fake.calls if c[1].endswith("/chat/completions")]
        self.assertIn("image_url", json.dumps(sent[0][2]))
        # value items carry the number itself and never span a page break
        for it in d["report"]["items"]:
            self.assertNotIn("\f", it["source_line"])
        glucose = next(it for it in d["report"]["items"] if it["fields"].get("analyte") == "glucose")
        self.assertEqual(glucose["fields"]["numeric"], 98)
        png = self.app.page_png(did, 1)
        self.assertTrue(png.startswith(b"\x89PNG"))
        self.assertTrue(any(i["target"] == "page:1" for i in d["issues"]))


class TestServer(AppCase):
    def setUp(self):
        super().setUp()
        from app import make_server
        dist = self.tmp / "dist"
        dist.mkdir()
        (dist / "index.html").write_text("<html></html>")
        self.server = make_server(self.app, dist)
        url = self.server.start()
        self.host, rest = url.split("//", 1)[1].split("/", 1)
        self.prefix = "/" + rest

    def tearDown(self):
        self.server.stop()
        super().tearDown()

    def req(self, method, path, body=None, headers=None, prefix=True):
        c = http.client.HTTPConnection(self.host)
        h = {"X-PAi-Request": "1"} if method == "POST" else {}
        h.update(headers or {})
        c.request(method, (self.prefix if prefix else "/") + path, body, h)
        r = c.getresponse()
        return r.status, r.read(), r

    def test_token_required(self):
        self.assertEqual(self.req("GET", "api/app", prefix=False)[0], 404)
        self.assertEqual(self.req("GET", "api/app")[0], 200)

    def test_post_needs_header_and_size_limit(self):
        s, _, _ = self.req("POST", "api/users/create", json.dumps({"name": "A", "passphrase": PW}),
                           headers={"X-PAi-Request": ""})
        self.assertEqual(s, 403)
        s, _, _ = self.req("POST", "api/users/create", "x" * 20000)
        self.assertEqual(s, 413)

    def test_flow_over_http(self):
        s, b, _ = self.req("POST", "api/users/create", json.dumps({"name": "A", "passphrase": PW}))
        self.assertEqual(s, 200, b)
        s, b, _ = self.req("POST", "api/ingest/upload", (FIX / "lab_report_sample.pdf").read_bytes(),
                           {"X-File-Name": "lab%20report.pdf"})
        self.assertEqual(s, 200, b)
        out = json.loads(b)
        wait(self.app, out["job"])
        did = out["documents"][0]["doc_id"]
        s, b, r = self.req("GET", f"api/ingest/page/{did}/1")
        self.assertEqual((s, r.getheader("Content-Type")), (200, "image/png"))
        s, b, _ = self.req("GET", "api/ingest/list")
        self.assertEqual(json.loads(b)["documents"][0]["file_name"], "lab report.pdf")
        s, b, _ = self.req("GET", "api/scenario.json")
        self.assertEqual(s, 200)

    def test_locked_user_errors_are_clean(self):
        s, b, _ = self.req("GET", "api/ingest/list")
        self.assertEqual(s, 401)
        self.assertIn("Unlock", json.loads(b)["error"])


class TestProviderFormats(unittest.TestCase):
    """Each provider gets its own wire format; endpoints are fixed."""

    def run_one(self, provider):
        from ai.providers import ExternalModel
        seen = []

        def t(method, url, headers, body, timeout):
            seen.append((url, headers, json.loads(body)))
            if provider == "anthropic":
                return {"content": [{"type": "text", "text": "ok"}]}
            return {"choices": [{"message": {"content": "ok"}}]}
        m = ExternalModel(provider, "key-12345678", "model-x", transport=t)
        msgs = [{"role": "system", "content": "SYS"},
                {"role": "user", "content": [{"type": "image_url", "image_url": {"url": "data:image/png;base64,QUJD"}},
                                             {"type": "text", "text": "hello"}]}]
        self.assertEqual(m.chat(msgs), "ok")
        return seen[0]

    def test_anthropic(self):
        url, headers, body = self.run_one("anthropic")
        self.assertEqual(url, "https://api.anthropic.com/v1/messages")
        self.assertEqual(headers["x-api-key"], "key-12345678")
        self.assertEqual(body["system"], "SYS")
        self.assertEqual(body["messages"][0]["content"][0]["source"],
                         {"type": "base64", "media_type": "image/png", "data": "QUJD"})

    def test_openai(self):
        url, headers, body = self.run_one("openai")
        self.assertEqual(url, "https://api.openai.com/v1/chat/completions")
        self.assertEqual(headers["Authorization"], "Bearer key-12345678")
        self.assertIn("max_completion_tokens", body)

    def test_nemotron(self):
        url, headers, body = self.run_one("nemotron")
        self.assertEqual(url, "https://integrate.api.nvidia.com/v1/chat/completions")
        self.assertEqual(body["max_tokens"], 800)

    def test_unknown_provider_refused(self):
        from ai.providers import ExternalModel, ProviderError
        with self.assertRaises(ProviderError):
            ExternalModel("evil", "key-12345678")


if __name__ == "__main__":
    unittest.main()
