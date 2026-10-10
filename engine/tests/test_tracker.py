"""Tracker (Part II) from the person's own data, and device exports into the vault.

Each test builds a small history by logging entries the way the app does, then
reads the Tracker contract the UI renders. Thresholds come from tracker/library.py.
"""
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from app_state import App                       # noqa: E402
from records.render import FORBIDDEN, lint       # noqa: E402
from tracker import library as L                 # noqa: E402
from tracker.logs import LogError                # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures"
DEMO = json.loads((Path(__file__).resolve().parents[2] / "src" / "data" / "tracker_demo.json").read_text())
NOW = datetime.now().astimezone().replace(second=0, microsecond=0)


def ago(days=0, hours=0, at=None):
    t = NOW - timedelta(days=days, hours=hours)
    if at:
        h, m = at
        t = t.replace(hour=h, minute=m)
        if t > NOW:
            t -= timedelta(days=1)
    return t.isoformat(timespec="minutes")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        os.environ["PAI_HEALTH_HOME"] = str(self.tmp)
        self.app = App(root=self.tmp)
        self.app.create_user("Tracker Test", "a long test passphrase")

    def tearDown(self):
        self.app.lock_user()
        os.environ.pop("PAI_HEALTH_HOME", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def log(self, **e):
        return self.app.log_entry(e)

    def bp_days(self, n, sys_, dia, start=1, per_day=2):
        for d in range(start, start + n):
            for k in range(per_day):
                self.log(type="BP", systolic=sys_ + (k % 2), diastolic=dia, at=ago(d, at=(7 + 12 * k, 30)))

    def t(self):
        return self.app.tracker(DEMO, now=NOW)

    def lane(self, area):
        return next(l for l in self.t()["lanes"] if l["area"] == area)


class Logs(Base):
    def test_validation(self):
        for bad in ({"type": "BP", "systolic": 80, "diastolic": 120}, {"type": "BP", "systolic": "x", "diastolic": 80},
                    {"type": "DOSE", "med_id": "M", "taken": "maybe"}, {"type": "NOPE"},
                    {"type": "MEDICATION", "name": "x", "schedule": ["8am"]},
                    {"type": "BP", "systolic": 120, "diastolic": 80, "at": (NOW + timedelta(days=1)).isoformat()}):
            with self.assertRaises(LogError, msg=bad):
                self.log(**bad)

    def test_medication_edit_supersedes(self):
        m = self.log(type="MEDICATION", name="Lisinopril", dose="10 mg", schedule=["08:00"])["entry"]
        self.log(type="MEDICATION", med_id=m["med_id"], name="Lisinopril", dose="20 mg", schedule=["08:00"])
        meds = [x for x in self.t()["medications"] if x["name"] == "Lisinopril"]
        self.assertEqual([x["dose"] for x in meds], ["20 mg"])

    def test_empty_vault_is_honest(self):
        t = self.t()
        self.assertFalse(t["synthetic"])
        self.assertEqual(t["vitals"]["bp"], [])
        self.assertIsNone(self.lane("Blood pressure")["lane"])
        self.assertEqual(t["labs"]["annual_status"], "OVERDUE")
        self.assertEqual(t["notices"][0]["id"], "LAB-ANNUAL")
        self.assertEqual(t["suggestions"], [])


class Lanes(Base):
    def test_lane2_persistent_high(self):
        self.bp_days(8, 142, 90)
        l = self.lane("Blood pressure")
        self.assertEqual(l["lane"], 2)
        self.assertIn("16 readings", l["history"])

    def test_too_few_readings_no_lane(self):
        self.bp_days(3, 150, 95)
        self.assertIsNone(self.lane("Blood pressure")["lane"])

    def test_lane1_suggestions_and_hold_list(self):
        self.bp_days(8, 132, 82)
        t = self.t()
        self.assertEqual(self.lane("Blood pressure")["lane"], 1)
        s = {x["id"]: x for x in t["suggestions"]}
        self.assertEqual(s["BP-SODIUM"]["state"], "ACTIVE")
        self.assertEqual(s["BP-POTASSIUM"]["state"], "HELD")
        self.assertIn("kidney function is known", s["BP-POTASSIUM"]["held_reason"])
        self.assertFalse(any(x["verified"] for x in t["suggestions"]))

    def test_ace_inhibitor_holds_potassium_and_flags_interaction(self):
        self.bp_days(8, 132, 82)
        self.log(type="MEDICATION", name="Lisinopril", dose="10 mg", schedule=["08:00"])
        t = self.t()
        pot = next(x for x in t["suggestions"] if x["id"] == "BP-POTASSIUM")
        self.assertIn("raise potassium", pot["held_reason"])
        self.assertTrue(any("Potassium foods" in c["pair"] for c in t["nutrition"]["interaction_checks"]))
        self.assertIn("don't replace it", next(x for x in t["suggestions"] if x["id"] == "BP-SODIUM")["note"])

    def test_lane3_fixed_text_only(self):
        self.bp_days(8, 132, 82)
        self.log(type="MEDICATION", name="Lisinopril", schedule=["08:00"], start=ago(10)[:10])
        self.log(type="BP", systolic=186, diastolic=122, at=ago(hours=1))
        self.log(type="SYMPTOM", urgent=["chest pain"], at=ago(hours=1))
        t = self.t()
        l = self.lane("Blood pressure")
        self.assertEqual(l["lane"], 3)
        self.assertEqual(l["summary"], L.LANE3_TEXT.format(symptom="chest pain"))
        self.assertEqual(t["prompts"], [])
        self.assertEqual(t["suggestions"], [])

    def test_severe_without_symptoms_is_lane2_recheck(self):
        self.log(type="BP", systolic=184, diastolic=110, at=ago(hours=2))
        t = self.t()
        self.assertEqual(self.lane("Blood pressure")["lane"], 2)
        self.assertTrue(any(p["kind"] == "MEASURE" for p in t["prompts"]))

    def test_glucose_lane2(self):
        for d in (2, 5, 9):
            self.log(type="GLUCOSE", mg_dl=131, fasting=True, at=ago(d, at=(7, 0)))
        self.assertEqual(self.lane("Blood sugar")["lane"], 2)

    def test_weight_loss_lane_and_hold(self):
        self.bp_days(8, 132, 82)
        self.log(type="WEIGHT", kg=90, at=ago(60))
        self.log(type="WEIGHT", kg=84, at=ago(1))
        self.assertEqual(self.lane("Weight")["lane"], 2)
        w = next(x for x in self.t()["suggestions"] if x["id"] == "BP-WEIGHT")
        self.assertEqual(w["state"], "HELD")


class Doses(Base):
    def test_prompt_with_safety_line_and_logging_clears_it(self):
        m = self.log(type="MEDICATION", name="Lisinopril", schedule=["08:00"], start=ago(5)[:10])["entry"]
        prompts = [p for p in self.t()["prompts"] if p["kind"] == "DOSE"]
        self.assertTrue(prompts)
        self.assertTrue(all(p["safety_line"] == L.DOSE_SAFETY for p in prompts))
        self.assertTrue(all(p["text"].endswith("?") for p in prompts))     # asks; never tells (§28.2)
        for p in prompts:
            self.log(type="DOSE", med_id=m["med_id"], due=p["action"]["due"], taken="YES", at=p["action"]["due"])
        self.assertEqual([p for p in self.t()["prompts"] if p["kind"] == "DOSE"], [])

    def test_pattern_learns_then_qualifies(self):
        """Heart rate falls ~8 bpm after each logged dose; flat at the same hour before the start."""
        from tracker import device_store as DS
        start = (NOW - timedelta(days=20)).date()
        m = self.log(type="MEDICATION", name="Metoprolol", schedule=["08:00"], start=start.isoformat())["entry"]
        rows = []
        for d in range(1, 50):
            day = NOW - timedelta(days=d)
            dose_day = day.date() >= start
            for minute in range(6 * 60, 12 * 60, 5):
                t = day.replace(hour=minute // 60, minute=minute % 60)
                drop = 8 if dose_day and minute >= 8 * 60 + 30 else 0
                rows.append([t.isoformat(), t.isoformat(), 70 + (minute % 3) - drop, "bpm", "Watch", "apple_health",
                             "WATCH", None, None, None, "HR", None])
            if dose_day:
                self.log(type="DOSE", med_id=m["med_id"], taken="YES", at=day.replace(hour=8, minute=0).isoformat())
        self.app.vault.put("DEVICE_SAMPLES", DS._pack(rows), ref=self.app.vault.tag("device-measure", "heart_rate"),
                           meta={"format": DS.FORMAT, "measure": "heart_rate"})
        med = next(x for x in self.t()["medications"] if x["name"] == "Metoprolol")
        self.assertEqual(med["verification"]["state"], "QUALIFIED", med["verification"]["text"])
        self.assertIn("falls about 8 bpm", med["verification"]["text"])


class ChangeWindows(Base):
    def test_window_with_challenge_items(self):
        start = (NOW - timedelta(days=15)).date()
        self.bp_days(14, 146, 92, start=16)
        self.bp_days(14, 129, 81, start=1)
        self.log(type="MEDICATION", name="Amlodipine", schedule=["08:00"], start=start.isoformat(), targets=["bp"])
        w = self.t()["change_windows"]
        self.assertEqual(len(w), 1)
        self.assertEqual(w[0]["difference_vs_spread"], "LARGER")
        self.assertEqual([c["item"] for c in w[0]["challenge"]],
                         ["Other changes in the window", "Regression to the mean", "Measurement change", "Adherence"])

    def test_no_target_no_window(self):
        self.bp_days(14, 146, 92, start=16)
        self.bp_days(14, 129, 81, start=1)
        self.log(type="MEDICATION", name="Amlodipine", start=(NOW - timedelta(days=15)).date().isoformat())
        self.assertEqual(self.t()["change_windows"], [])          # §22.2: the engine never picks the target


class Wording(Base):
    def test_no_forbidden_wording_anywhere(self):
        self.bp_days(8, 142, 90)
        self.log(type="MEDICATION", name="Metformin", schedule=["08:00"], start=ago(5)[:10])
        self.log(type="MEDICATION", name="Levothyroxine", schedule=["07:00"])
        self.log(type="ITEM", name="Ibuprofen 400 mg")
        self.log(type="PROFILE", birth_year=1971, sex="male")
        t = self.t()
        texts = []

        def walk(x):
            if isinstance(x, str):
                texts.append(x)
            elif isinstance(x, dict):
                [walk(v) for k, v in x.items() if k not in ("audit", "name", "item")]
            elif isinstance(x, list):
                [walk(v) for v in x]
        walk(t)
        import re
        texts = [s for s in texts if s != L.DOSE_SAFETY]        # §21.4 fixed text, verbatim by rule
        bad = [(s, p) for s in texts for p in FORBIDDEN if re.search(p, s, re.I)]
        self.assertEqual(bad, [])
        self.assertTrue(any(r["item"] == "Ibuprofen 400 mg" for r in t["raises_bp"]))
        asks = {a["test"] for a in t["labs"]["ask_to_include"]}
        self.assertIn("Vitamin B12", asks)
        self.assertIn("Cholesterol (lipids)", asks)


class DeviceImport(Base):
    def wait(self, jid):
        import time
        for _ in range(400):
            j = self.app.jobs.get(jid)
            if j["state"] in ("DONE", "FAILED"):
                return j
            time.sleep(0.05)
        raise AssertionError("timeout")

    def apple_zip(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.write(FIX / "apple" / "structured_sample.xml", "apple_health_export/export.xml")
        return buf.getvalue()

    def test_apple_import_dedupes_and_encrypts(self):
        data = self.apple_zip()
        r = self.app.upload("export.zip", data, {"timezone": "America/New_York"})
        self.assertEqual(r["device"]["kind"], "apple")
        j = self.wait(r["job"])
        self.assertEqual(j["state"], "DONE", j["error"])
        first = j["result"]
        self.assertGreater(sum(first["saved"].values()), 0)
        j2 = self.wait(self.app.upload("export.zip", data, {})["job"])
        self.assertEqual(sum(j2["result"]["saved"].values()), 0)
        self.assertEqual(sum(j2["result"]["already_present"].values()), j2["result"]["samples_read"])
        self.assertEqual(first["samples_read"] - sum(first["saved"].values()), 1)   # the file's own exact duplicate
        raw = (self.tmp / "users" / self.app.user / "vault" / "health.db").read_bytes()
        self.assertNotIn(b"Apple Watch", raw)
        self.assertEqual(len(self.app.device_imports()), 2)
        self.assertFalse(list(Path(tempfile.gettempdir()).glob("pai-health-import-*/export.zip")))

    def test_garmin_needs_units(self):
        csv = b'Activity Type,Date,Time,Distance\nRunning,2026-09-01 07:00:00,00:30:00,5.0\n'
        r = self.app.upload("Activities.csv", csv, {"timezone": "America/New_York"})
        self.assertEqual((r["device"]["kind"], r["device"]["needs"], r["job"]), ("garmin", ["units"], None))

    def test_ordinary_documents_still_ingest(self):
        r = self.app.upload("note.txt", b"Patient reports headache.", {})
        self.assertNotIn("device", r)
        self.assertTrue(r["documents"])


if __name__ == "__main__":
    unittest.main()
