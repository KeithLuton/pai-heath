"""The iPhone app's protocol core (Swift package ios/CompanionCore) against the real PC server.

Builds the `companion-interop` executable with SwiftPM, pairs with a live LAN listener, syncs,
tries a replay, and checks the phone raises exactly the dose prompts the PC raises — same ids,
same words. Skipped where no Swift toolchain exists (it runs on the macOS CI job).
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
from companion.service import is_private_lan      # noqa: E402
from test_companion import lan_test_host          # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
PKG = ROOT / "ios" / "CompanionCore"


@unittest.skipUnless(shutil.which("swift"), "needs a Swift toolchain")
class SwiftCore(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        out = subprocess.run(["swift", "build", "--package-path", str(PKG), "--product", "companion-interop"],
                             capture_output=True, text=True, timeout=900)
        assert out.returncode == 0, out.stdout + out.stderr
        bindir = subprocess.run(["swift", "build", "--package-path", str(PKG), "--show-bin-path"],
                                capture_output=True, text=True).stdout.strip()
        cls.exe = str(Path(bindir) / "companion-interop")

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        os.environ["PAI_HEALTH_HOME"] = str(self.tmp)
        self.app = App(root=self.tmp)
        self.app.create_user("Swift", "a long test passphrase")
        self.host = lan_test_host()
        self.app.companion_enable(True, host=self.host)

    def tearDown(self):
        self.app.companion_enable(False)
        self.app.lock_user()
        os.environ.pop("PAI_HEALTH_HOME", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_interop(self):
        start = (datetime.now() - timedelta(days=3)).date().isoformat()
        self.app.log_entry({"type": "MEDICATION", "name": "Lisinopril", "schedule": ["00:00"], "start": start})
        uri = self.app.companion_pair()["uri"]
        cmd = [self.exe, uri] + ([] if is_private_lan(self.host) else ["--any-host"])
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        phone = {tuple(l.split("\t")) for l in r.stdout.splitlines() if "\t" in l}
        pc = {(p["id"], p["text"]) for p in self.app.tracker({})["prompts"] if p["kind"] == "DOSE"}
        self.assertTrue(pc)
        self.assertEqual(phone, pc)
        self.assertTrue(r.stdout.strip().endswith("OK"))
        self.assertEqual(len(self.app.tracker({})["vitals"]["bp"]), 1)


if __name__ == "__main__":
    unittest.main()
