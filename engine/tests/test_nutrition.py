"""Tests for USDA FoodData Central nutrition builder, search, and meal tracking."""
import http.client
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app_state import App
from nutrition import fdc_build, foods
from records.render import FORBIDDEN
from tracker.logs import LogError

def _recent(minutes=5):
    """A time a few minutes ago today (fixed clock times fail before noon or on other days)."""
    t = datetime.now().astimezone() - timedelta(minutes=minutes)
    return t.replace(second=0, microsecond=0).isoformat(timespec="minutes")


FIXTURE_CSV_DIR = Path(__file__).resolve().parent / "fixtures" / "fdc"


class TestFdcBuild(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="fdc-build-test-"))
        self.db_path = self.tmp / "test_fdc.db"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_builder_runs_and_populates_tables(self):
        res = fdc_build.build(FIXTURE_CSV_DIR, self.db_path)
        self.assertEqual(res["foods_count"], 20)
        self.assertEqual(res["portions_count"], 22)
        self.assertTrue(self.db_path.is_file())

        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        foods_count = conn.execute("SELECT COUNT(*) FROM foods").fetchone()[0]
        self.assertEqual(foods_count, 20)
        oats = conn.execute("SELECT * FROM foods WHERE fdc_id = 1001").fetchone()
        self.assertEqual(oats["description"], "Oats, rolled, regular or quick, dry")
        self.assertEqual(oats["energy_kcal"], 379.0)
        self.assertEqual(oats["protein_g"], 13.15)
        self.assertEqual(oats["food_category"], "Breakfast Cereals and Cereal Grains")

        spinach = conn.execute("SELECT * FROM foods WHERE fdc_id = 1005").fetchone()
        self.assertEqual(spinach["vitamin_k_ug"], 483.5)

        coffee = conn.execute("SELECT * FROM foods WHERE fdc_id = 1007").fetchone()
        self.assertEqual(coffee["caffeine_mg"], 40.0)

        fts_res = conn.execute("SELECT rowid FROM foods_fts WHERE foods_fts MATCH '\"oats\"*'").fetchall()
        self.assertTrue(any(r[0] == 1001 for r in fts_res))

        meta = dict(conn.execute("SELECT key, value FROM meta").fetchall())
        self.assertEqual(meta["food_count"], "20")
        self.assertIn("FoodData Central", meta["source"])
        conn.close()


class TestNutritionLogic(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="nutrition-test-"))
        cls.db_path = cls.tmp / "fixture_fdc.db"
        fdc_build.build(FIXTURE_CSV_DIR, cls.db_path)
        foods.set_default_db(cls.db_path)

    @classmethod
    def tearDownClass(cls):
        foods.set_default_db(None)
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_search_ranking_and_prefix(self):
        res = foods.search("oat")
        self.assertTrue(res)
        self.assertEqual(res[0]["fdc_id"], 1001)

        res_blueb = foods.search("blueb")
        self.assertTrue(res_blueb)
        self.assertEqual(res_blueb[0]["fdc_id"], 1002)

        res_spin = foods.search("raw spinach")
        self.assertTrue(res_spin)
        self.assertEqual(res_spin[0]["fdc_id"], 1005)

        self.assertEqual(foods.search(""), [])
        self.assertEqual(foods.search("   "), [])
        self.assertEqual(foods.search("nonexistentfoodxyz"), [])

    def test_portion_math(self):
        p_list = foods.portions(1001)
        self.assertTrue(any(p["amount"] == 1.0 and p["gram_weight"] == 81.0 for p in p_list))
        self.assertTrue(any(p["gram_weight"] == 100.0 for p in p_list))

        nut_81 = foods.nutrients(1001, 81.0)
        self.assertEqual(nut_81["grams"], 81.0)
        self.assertEqual(nut_81["energy_kcal"], round(379.0 * 0.81, 2))
        self.assertEqual(nut_81["protein_g"], round(13.15 * 0.81, 2))
        self.assertEqual(nut_81["fiber_g"], round(10.1 * 0.81, 2))

        nut_200 = foods.nutrients(1001, 200.0)
        self.assertEqual(nut_200["energy_kcal"], 379.0 * 2)

    def test_normalize_validation(self):
        from tracker import logs as LG
        valid = {
            "type": "MEAL",
            "at": _recent(),
            "items": [{"fdc_id": 1001, "grams": 81.0, "portion_label": "1 cup"}],
        }
        norm = LG.normalize(valid)
        self.assertEqual(norm["type"], "MEAL")
        self.assertEqual(len(norm["items"]), 1)
        self.assertEqual(norm["items"][0]["fdc_id"], 1001)
        self.assertEqual(norm["items"][0]["provenance"]["source"], "USDA FoodData Central")

        with self.assertRaises(LogError):
            LG.normalize({"type": "MEAL", "items": []})

        with self.assertRaises(LogError):
            LG.normalize({"type": "MEAL", "items": [{"fdc_id": 1001, "grams": 50}] * 31})

        with self.assertRaises(LogError):
            LG.normalize({"type": "MEAL", "items": [{"fdc_id": 1001, "grams": 0.5}]})

        with self.assertRaises(LogError):
            LG.normalize({"type": "MEAL", "items": [{"fdc_id": 1001, "grams": 3500.0}]})

        with self.assertRaises(LogError):
            LG.normalize({"type": "MEAL", "items": [{"fdc_id": 999999, "grams": 100.0}]})

    def test_totals_frozen_at_log_time(self):
        from tracker import logs as LG
        meal = {
            "type": "MEAL",
            "at": _recent(),
            "items": [
                {"fdc_id": 1001, "grams": 81.0, "portion_label": "1 cup"},
                {"fdc_id": 1002, "grams": 148.0, "portion_label": "1 cup"},
            ],
            "note": "Breakfast",
        }
        norm = LG.normalize(meal)
        expected_cal = round(379.0 * 0.81, 2) + round(57.0 * 1.48, 2)
        self.assertAlmostEqual(norm["totals"]["energy_kcal"], expected_cal, places=1)
        self.assertEqual(norm["items"][0]["provenance"]["dataset_date"], "2019-04-01")


class TestAppNutritionIntegration(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="app-nutrition-test-"))
        os.environ["PAI_HEALTH_HOME"] = str(self.tmp)
        self.db_path = self.tmp / "test_fdc.db"
        fdc_build.build(FIXTURE_CSV_DIR, self.db_path)
        foods.set_default_db(self.db_path)

        self.app = App(root=self.tmp)
        self.app.create_user("NutritionTester", "a very secure test passphrase")

    def tearDown(self):
        self.app.lock_user()
        foods.set_default_db(None)
        os.environ.pop("PAI_HEALTH_HOME", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_med_food_flag_and_wording(self):
        self.app.log_entry({"type": "MEDICATION", "name": "Warfarin", "schedule": ["18:00"]})
        self.app.log_entry({
            "type": "MEAL",
            "at": _recent(),
            "items": [{"fdc_id": 1005, "grams": 100.0, "portion_label": "100 g raw spinach"}],
        })

        t = self.app.tracker({})
        checks = t["nutrition"]["interaction_checks"]
        wf = next(c for c in checks if "Warfarin" in c["pair"] or "warfarin" in c["pair"])
        self.assertEqual(wf["state"], "NOTED")
        self.assertIn("worth discussing with your clinician or pharmacist", wf["text"])

        for forbidden in FORBIDDEN:
            self.assertFalse(re.search(forbidden, wf["text"], re.I), f"Forbidden regex {forbidden} matched")
        self.assertNotIn("unsafe", wf["text"].lower())

    def test_routes_and_server(self):
        from app import make_server
        dist = self.tmp / "dist"
        dist.mkdir()
        (dist / "index.html").write_text("<html></html>")
        server = make_server(self.app, dist)
        url = server.start()

        try:
            host, rest = url.split("//", 1)[1].split("/", 1)
            prefix = "/" + rest.rstrip("/") + "/"

            # GET api/foods/search
            c = http.client.HTTPConnection(host)
            c.request("GET", prefix + "api/foods/search?q=oats")
            r = c.getresponse()
            self.assertEqual(r.status, 200)
            res = json.loads(r.read())
            self.assertTrue(res["foods"])
            self.assertEqual(res["foods"][0]["fdc_id"], 1001)

            # GET api/foods/portions/1001
            c = http.client.HTTPConnection(host)
            c.request("GET", prefix + "api/foods/portions/1001")
            r = c.getresponse()
            self.assertEqual(r.status, 200)
            res = json.loads(r.read())
            self.assertEqual(res["fdc_id"], 1001)
            self.assertTrue(res["portions"])
            self.assertEqual(res["portions"][0]["gram_weight"], 81.0)

            # Bad ID
            c = http.client.HTTPConnection(host)
            c.request("GET", prefix + "api/foods/portions/invalid")
            r = c.getresponse()
            self.assertEqual(r.status, 400)

            # POST api/log with MEAL
            meal_payload = {
                "type": "MEAL",
                "at": _recent(),
                "items": [{"fdc_id": 1001, "grams": 81.0, "portion_label": "1 cup"}],
                "note": "Breakfast",
            }
            c = http.client.HTTPConnection(host)
            c.request("POST", prefix + "api/log", json.dumps(meal_payload),
                      {"Content-Type": "application/json", "X-PAi-Request": "1"})
            r = c.getresponse()
            self.assertEqual(r.status, 200)

            # Verify tracker response
            c = http.client.HTTPConnection(host)
            c.request("GET", prefix + "api/tracker.json")
            r = c.getresponse()
            self.assertEqual(r.status, 200)
            tr = json.loads(r.read())["data"]
            self.assertEqual(len(tr["nutrition"]["meals"]), 1)
            self.assertTrue(tr["nutrition"]["totals"])
            self.assertIn("USDA FoodData Central", tr["nutrition"]["source"])
        finally:
            server.stop()


if __name__ == "__main__":
    unittest.main()
