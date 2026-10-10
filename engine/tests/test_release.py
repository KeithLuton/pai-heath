"""Release gates from the 2026-10-08 audit: disaster recovery end to end, the constitution ships
in the tree at the contract version, and companion tests don't depend on free fixed ports."""
from __future__ import annotations

import json
import os
import re
import shutil
import socket
import sys
import tempfile
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import runtime                                              # noqa: E402
from app_state import App, AppError                         # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
PW, BPW = "correct horse battery staple", "backup phrase that is long"


class DisasterRecoveryTest(unittest.TestCase):
    """Create → log → back up → destroy → restore → same records, valid audit chain."""

    def setUp(self):
        self.tmp, self.docs = Path(tempfile.mkdtemp()), Path(tempfile.mkdtemp())
        os.environ["PAI_HEALTH_HOME"] = str(self.tmp)
        os.environ["PAI_HEALTH_DOCUMENTS"] = str(self.docs)
        self.app = App(root=self.tmp)

    def tearDown(self):
        self.app.lock_user()
        for k in ("PAI_HEALTH_HOME", "PAI_HEALTH_DOCUMENTS"):
            os.environ.pop(k, None)
        shutil.rmtree(self.tmp, ignore_errors=True)
        shutil.rmtree(self.docs, ignore_errors=True)

    def _records(self):
        return sorted((r.id, json.dumps(r.body, sort_keys=True)) for r in self.app.vault.iter("USER_LOG"))

    def test_full_cycle(self):
        user = self.app.create_user("Ann", PW, runtime.NOTICE_VERSION)
        self.app.log_entry({"type": "BP", "systolic": 128, "diastolic": 82, "at": "2026-10-08T08:00:00-04:00"})
        self.app.log_entry({"type": "WEIGHT", "kg": 81.5, "at": "2026-10-08T08:05:00-04:00"})
        before = self._records()
        self.assertEqual(len(before), 2)
        path = Path(self.app.backup(BPW)["path"])
        self.app.lock_user()
        shutil.rmtree(self.tmp / "users" / user["id"])                 # the disaster
        self.assertEqual(self.app.users(), [])

        out = self.app.restore(path.read_bytes(), "Ann", BPW, PW)
        self.assertTrue(out["verified"])
        self.assertEqual(self._records(), before)
        state = self.app.vault.verify()
        self.assertTrue(state["valid"], state)
        events = [r.get("event") for r in self.app.vault.audit._read()]
        self.assertIn("DATA_NOTICE_ACCEPTED", events)
        self.assertNotIn("BACKUP_CREATED", events)      # logged after the snapshot, so not inside it
        self.assertEqual(events[-1], "RESTORED_FROM_BACKUP")

    def test_wrong_passphrases_leave_nothing_behind(self):
        self.app.create_user("Ann", PW, runtime.NOTICE_VERSION)
        data = Path(self.app.backup(BPW)["path"]).read_bytes()
        self.app.lock_user()
        n = len(self.app.users())
        for b, v in ((BPW + "x", PW), (BPW, PW + "x")):
            with self.assertRaises(AppError):
                self.app.restore(data, "Bob", b, v)
        with self.assertRaises(AppError):
            self.app.restore(b"not a backup at all", "Bob", BPW, PW)
        self.assertEqual(len(self.app.users()), n)
        self.assertFalse(any(p.name.startswith("bob") for p in (self.tmp / "users").iterdir()))

    def test_restore_route(self):
        import app as app_mod
        self.app.create_user("Ann", PW, runtime.NOTICE_VERSION)
        data = Path(self.app.backup(BPW)["path"]).read_bytes()
        self.app.lock_user()
        server = app_mod.make_server(self.app)
        url = server.start()
        try:
            q = urllib.parse.quote
            req = urllib.request.Request(url + "api/backup/restore", data=data, method="POST", headers={
                "X-PAi-Request": "1", "Content-Type": "application/octet-stream", "X-User-Name": q("Ann restored"),
                "X-Backup-Passphrase": q(BPW), "X-Vault-Passphrase": q(PW)})
            body = json.loads(urllib.request.urlopen(req, timeout=30).read())
            self.assertEqual(body["name"], "Ann restored")
            self.assertTrue(body["verified"])
        finally:
            server.stop()


class ConstitutionShipsTest(unittest.TestCase):
    def test_constitution_in_tree_at_contract_version(self):
        doc = (ROOT / "docs" / "MEDICAL_PRIMITIVES.md").read_text(encoding="utf-8")
        version = json.loads((ROOT / "locks.json").read_text(encoding="utf-8"))["contract_version"]
        self.assertRegex(doc.splitlines()[0], rf"\bv{re.escape(version)}\b")


class CompanionPortFallbackTest(unittest.TestCase):
    """From source (no MSIX firewall rule) the phone listener may fall back to any free port."""

    def test_falls_back_when_whole_range_busy(self):
        from tests.test_companion import lan_test_host
        host = lan_test_host()
        held = []
        tmp = Path(tempfile.mkdtemp())
        app = App(root=tmp)
        try:
            for p in runtime.COMPANION_PORTS:
                s = socket.socket()
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                try:
                    s.bind((host, p))
                    s.listen(1)
                    held.append(s)
                except OSError:
                    s.close()
            app.create_user("Ann", PW, runtime.NOTICE_VERSION)
            st = app.companion_enable(True, host=host)
            self.assertTrue(st["running"])
            port = int(st["address"].rsplit(":", 1)[1])
            self.assertNotIn(port, [s.getsockname()[1] for s in held])
        finally:
            app.companion_enable(False)
            app.lock_user()
            for s in held:
                s.close()
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
