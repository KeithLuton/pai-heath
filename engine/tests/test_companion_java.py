"""The Android app's protocol core (plain Java) against the real PC server.

Compiles android/app/src/main/java/com/pailuton/health/core/*.java with the JDK, pairs with a
live LAN listener, syncs, tries a replay, and checks that the phone raises exactly the dose
prompts the PC raises — same ids, same words. Skipped when no JDK is installed.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from app_state import App                         # noqa: E402
from test_companion import lan_test_host          # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
CORE = ROOT / "android" / "app" / "src" / "main" / "java" / "com" / "pailuton" / "health" / "core"


@unittest.skipUnless(shutil.which("javac") and shutil.which("java"), "needs a JDK")
class JavaCore(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        os.environ["PAI_HEALTH_HOME"] = str(self.tmp)
        self.app = App(root=self.tmp)
        self.app.create_user("Java", "a long test passphrase")
        self.app.companion_enable(True, host=lan_test_host())
        self.build = self.tmp / "classes"
        out = subprocess.run(["javac", "-d", str(self.build), *map(str, CORE.glob("*.java")),
                              str(ROOT / "android" / "core-test" / "CoreTest.java")], capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)

    def tearDown(self):
        self.app.companion_enable(False)
        self.app.lock_user()
        os.environ.pop("PAI_HEALTH_HOME", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_interop(self):
        start = (datetime.now() - timedelta(days=3)).date().isoformat()
        self.app.log_entry({"type": "MEDICATION", "name": "Lisinopril", "schedule": ["00:00"], "start": start})
        uri = self.app.companion_pair()["uri"]
        r = subprocess.run(["java", "-cp", str(self.build), "CoreTest", uri], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        lines = [l for l in r.stdout.splitlines() if "\t" in l]
        phone = {tuple(l.split("\t")) for l in lines}
        pc = {(p["id"], p["text"]) for p in self.app.tracker({})["prompts"] if p["kind"] == "DOSE"}
        self.assertTrue(pc)
        self.assertEqual(phone, pc)
        self.assertTrue(r.stdout.strip().endswith("OK"))
        self.assertEqual(len(self.app.tracker({})["vitals"]["bp"]), 1)     # the phone's reading reached the vault


if __name__ == "__main__":
    unittest.main()
