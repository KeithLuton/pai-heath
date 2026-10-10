"""Phone companion: pairing by QR secret, sealed sync over the LAN listener, replay refusal,
entries and Health Connect samples into the vault, and the rules bundle the phone runs.

A reference phone (below) speaks the protocol exactly as android/.../core does; the Java
core is checked against this same server in test_companion_java.py.
"""
import ipaddress
import json
import os
import shutil
import sys
import tempfile
import unittest
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app_state import App                                  # noqa: E402
from companion import protocol as P                        # noqa: E402
from companion import service as S                         # noqa: E402
from tracker import library as L                           # noqa: E402




def lan_test_host():
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
    net = ipaddress.ip_network(ip + "/32")
    if not S.is_private_lan(ip):
        S.LAN_NETS.append(net)          # the test container's own address, for this test only
    return ip


class Phone:
    """Reference phone client."""

    def __init__(self, uri):
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(uri).query))
        self.base = f"http://{q['h']}:{q['p']}"
        self.pair_id, secret = q["id"], P.b64d(q["k"])
        self.k_up, self.k_down = P.keys(secret, self.pair_id)
        self.seq = 0

    def call(self, path, payload, seq=None):
        self.seq = seq if seq is not None else self.seq + 1
        body = P.seal(self.k_up, self.pair_id, "up", path, dict(payload, seq=self.seq,
                                                                device={"name": "Pixel", "model": "Pixel 8", "app_version": "0.1"}))
        req = urllib.request.Request(self.base + path, data=body, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return 200, P.open_(self.k_down, self.pair_id, "down", path, r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        os.environ["PAI_HEALTH_HOME"] = str(self.tmp)
        self.host = lan_test_host()
        self.app = App(root=self.tmp)
        self.app.create_user("Phone Test", "a long test passphrase")
        self.state = self.app.companion_enable(True, host=self.host)
        self.pair = self.app.companion_pair()
        self.phone = Phone(self.pair["uri"])

    def tearDown(self):
        self.app.companion_enable(False)
        self.app.lock_user()
        os.environ.pop("PAI_HEALTH_HOME", None)
        shutil.rmtree(self.tmp, ignore_errors=True)


class Pairing(Base):
    def test_listener_is_lan_only(self):
        self.assertTrue(self.state["running"])
        self.assertTrue(self.state["address"].startswith(self.host + ":"))
        with self.assertRaises(ValueError):
            from companion.server import LanSyncServer
            LanSyncServer(self.app, "0.0.0.0", 0)
        with self.assertRaises(ValueError):
            from companion.server import LanSyncServer
            LanSyncServer(self.app, "8.8.8.8", 0)

    def test_qr_and_hello(self):
        self.assertIn("<svg", self.pair["qr_svg"])
        self.assertTrue(self.pair["uri"].startswith("paihealth://pair?v=1&h="))
        code, r = self.phone.call("/v1/hello", {})
        self.assertEqual(code, 200)
        self.assertTrue(r["paired"])
        self.assertEqual(r["rules"]["safety_line"], L.DOSE_SAFETY)
        dev = self.app.companion_state()["devices"]
        self.assertEqual((dev[0]["confirmed"], dev[0]["device"]["model"]), (True, "Pixel 8"))
        self.assertTrue(self.app.tracker({})["settings"]["live_available"])
        self.app.set_setting("data_path", "LIVE")          # allowed once a phone is paired

    def test_live_refused_without_phone(self):
        with self.assertRaises(ValueError):
            self.app.set_setting("data_path", "LIVE")

    def test_wrong_key_rejected(self):
        bad = Phone(self.pair["uri"].replace(self.pair["uri"].split("k=")[1], P.b64e(os.urandom(32))))
        code, r = bad.call("/v1/hello", {})
        self.assertEqual(code, 400)

    def test_replay_rejected(self):
        self.phone.call("/v1/hello", {})
        code, _ = self.phone.call("/v1/sync", {}, seq=1)
        self.assertEqual(code, 403)

    def test_path_bound(self):
        body = P.seal(self.phone.k_up, self.phone.pair_id, "up", "/v1/hello", {"seq": 5})
        req = urllib.request.Request(self.phone.base + "/v1/sync", data=body, method="POST")
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req, timeout=10)
        self.assertEqual(cm.exception.code, 400)

    def test_revoked_phone_refused(self):
        self.phone.call("/v1/hello", {})
        self.app.companion_revoke(self.phone.pair_id)
        code, _ = self.phone.call("/v1/sync", {})
        self.assertEqual(code, 403)

    def test_locked_pc_says_locked(self):
        self.phone.call("/v1/hello", {})
        self.app.lock_user()
        code, r = self.phone.call("/v1/sync", {})
        self.assertEqual(code, 423)
        self.app.unlock(self.app.users()[0]["id"], "a long test passphrase")

    def test_ping_has_no_data(self):
        with urllib.request.urlopen(self.phone.base + "/v1/ping", timeout=5) as r:
            self.assertEqual(json.loads(r.read()), {"app": "PAi Health", "protocol": 1})


class Sync(Base):
    def test_entries_and_samples_into_vault(self):
        self.phone.call("/v1/hello", {})
        now = datetime.now().astimezone().replace(microsecond=0)
        m = self.app.log_entry({"type": "MEDICATION", "name": "Lisinopril", "schedule": ["08:00"]})["entry"]
        uid = str(uuid.uuid4())
        entries = [{"uid": uid, "entry": {"type": "DOSE", "med_id": m["med_id"], "taken": "YES",
                                          "at": (now - timedelta(hours=1)).isoformat()}},
                   {"uid": str(uuid.uuid4()), "entry": {"type": "BP", "systolic": 131, "diastolic": 84,
                                                        "at": (now - timedelta(minutes=30)).isoformat()}},
                   {"uid": str(uuid.uuid4()), "entry": {"type": "MEDICATION", "name": "sneaky"}},
                   {"uid": str(uuid.uuid4()), "entry": {"type": "BP", "systolic": 80, "diastolic": 120}}]
        hr = [[(now - timedelta(minutes=i)).isoformat(), (now - timedelta(minutes=i)).isoformat(), 70 + i % 3, "bpm",
               "Pixel Watch", "x", "WATCH", None, None, None, "HeartRateRecord", None] for i in range(30)]
        code, r = self.phone.call("/v1/sync", {"entries": entries, "samples": {"heart_rate": hr, "evil": hr}})
        self.assertEqual(code, 200, r)
        self.assertEqual((r["accepted"], len(r["rejected"])), (2, 2))
        self.assertEqual(r["samples_saved"], {"heart_rate": 30})
        # same entries again: duplicates, not double-counted
        code, r2 = self.phone.call("/v1/sync", {"entries": entries[:2], "samples": {"heart_rate": hr}})
        self.assertEqual((r2["accepted"], r2["duplicates"], r2["samples_saved"]), (0, 2, {"heart_rate": 0}))
        t = self.app.tracker({})
        self.assertEqual(len(t["vitals"]["bp"]), 1)
        from tracker import device_store as DS
        rows = DS.rows_for(self.app.vault, "heart_rate")
        self.assertEqual(rows[0][5], "health_connect")
        raw = (self.tmp / "users" / self.app.user / "vault" / "health.db").read_bytes()
        self.assertNotIn(b"Pixel Watch", raw)

    def test_rules_bundle_carries_schedule(self):
        self.app.log_entry({"type": "MEDICATION", "name": "Metformin", "schedule": ["08:00", "20:00"]})
        code, r = self.phone.call("/v1/hello", {})
        meds = r["rules"]["meds"]
        self.assertEqual((meds[0]["name"], meds[0]["schedule"]), ("Metformin", ["08:00", "20:00"]))
        self.assertEqual(r["rules"]["bp_severe"], [180, 120])
        self.assertIn("{symptom}", r["rules"]["lane3_text"])

    def test_audit_has_no_content(self):
        self.phone.call("/v1/hello", {})
        self.phone.call("/v1/sync", {"entries": [{"uid": "u1", "entry": {"type": "SYMPTOM", "text": "tingling feet"}}]})
        log = (self.tmp / "users" / self.app.user / "vault" / "audit.log").read_text()
        self.assertIn("COMPANION_PAIRED", log)
        self.assertNotIn("tingling", log)


if __name__ == "__main__":
    unittest.main()
