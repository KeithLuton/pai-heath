"""Encrypted storage + audit chain. Every test works on a throwaway vault."""
import json
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from health_server import SETTING_VALUES                                  # noqa: E402
from storage import (IntegrityError, StoreError, Vault, VaultLocked,      # noqa: E402
                     create_backup, restore_backup)
from storage.store import AUDIT_FILE, DB_FILE, KEY_FILE                   # noqa: E402

PW = "correct horse battery"
BPW = "separate backup phrase"
SECRET_TEXT = "Metformin 500 mg twice daily; B12 182 pg/mL LOW"
SECRET_NAME = "2023-03-10 neurology.pdf"


class VaultCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pai-health-test-"))
        self.dir = self.tmp / "vault"
        self.v = Vault.create(self.dir, PW)

    def tearDown(self):
        try:
            self.v.close()
        except Exception:
            pass
        shutil.rmtree(self.tmp, ignore_errors=True)

    def reopen(self, pw=PW):
        self.v.close()
        self.v = Vault.open(self.dir, pw)
        return self.v


class TestEncryption(VaultCase):
    def test_no_plaintext_on_disk(self):
        doc, _ = self.v.add_document(SECRET_NAME, SECRET_TEXT.encode())
        self.v.log({"type": "DOSE_TAKEN", "medication": "Metformin", "note": SECRET_TEXT})
        self.v.set_setting("ai_mode", "PAi", SETTING_VALUES)
        self.v.close()
        for name in (DB_FILE, AUDIT_FILE, KEY_FILE):
            raw = (self.dir / name).read_bytes()
            for needle in ("Metformin", "B12", "neurology", "DOSE_TAKEN", "ai_mode", "PAi\""):
                self.assertNotIn(needle.encode(), raw, f"{needle} visible in {name}")
        import hashlib
        self.assertNotIn(hashlib.sha256(SECRET_TEXT.encode()).hexdigest().encode(),
                         (self.dir / DB_FILE).read_bytes(), "plain content hash leaks possession")
        self.v = Vault.open(self.dir, PW)

    def test_round_trip(self):
        doc, new = self.v.add_document(SECRET_NAME, b"%PDF-1.7 bytes")
        lid = self.v.log({"type": "READING", "systolic": 128, "diastolic": 82})
        v = self.reopen()
        self.assertTrue(new)
        self.assertEqual(v.get(doc).body, b"%PDF-1.7 bytes")
        self.assertEqual(v.get(doc).meta["file_name"], SECRET_NAME)
        self.assertEqual(v.get(lid).body["systolic"], 128)

    def test_wrong_passphrase(self):
        self.v.close()
        with self.assertRaises(VaultLocked):
            Vault.open(self.dir, "wrong passphrase!!")
        self.v = Vault.open(self.dir, PW)

    def test_short_passphrase_refused(self):
        with self.assertRaises(ValueError):
            Vault.create(self.tmp / "other", "short")

    def test_change_passphrase(self):
        lid = self.v.log({"type": "MEAL", "items": ["oats"]})
        self.v.change_passphrase(PW, "a brand new passphrase")
        self.v.close()
        with self.assertRaises(VaultLocked):
            Vault.open(self.dir, PW)
        self.v = Vault.open(self.dir, "a brand new passphrase")
        self.assertEqual(self.v.get(lid).body["items"], ["oats"])

    def test_create_refuses_existing(self):
        with self.assertRaises(FileExistsError):
            Vault.create(self.dir, PW)


class TestTamper(VaultCase):
    def _raw(self):
        return sqlite3.connect(self.dir / DB_FILE, isolation_level=None)

    def test_ciphertext_flip_detected(self):
        lid = self.v.log({"type": "READING", "glucose": 101})
        self.v.close()
        db = self._raw()
        ct = bytearray(db.execute("SELECT ciphertext FROM records WHERE id=?", (lid,)).fetchone()[0])
        ct[-1] ^= 1
        db.execute("UPDATE records SET ciphertext=? WHERE id=?", (bytes(ct), lid))
        db.close()
        self.v = Vault.open(self.dir, PW)
        with self.assertRaises(IntegrityError):
            self.v.get(lid)
        self.assertFalse(self.v.verify()["valid"])

    def test_row_swap_detected(self):
        a = self.v.log({"type": "READING", "glucose": 90})
        b = self.v.log({"type": "READING", "glucose": 250})
        self.v.close()
        db = self._raw()
        ra = db.execute("SELECT nonce, ciphertext FROM records WHERE id=?", (a,)).fetchone()
        rb = db.execute("SELECT nonce, ciphertext FROM records WHERE id=?", (b,)).fetchone()
        db.execute("UPDATE records SET nonce=?, ciphertext=? WHERE id=?", (*rb, a))
        db.execute("UPDATE records SET nonce=?, ciphertext=? WHERE id=?", (*ra, b))
        db.close()
        self.v = Vault.open(self.dir, PW)
        with self.assertRaises(IntegrityError):
            self.v.get(a)

    def test_kind_relabel_detected(self):
        lid = self.v.log({"type": "DOSE_TAKEN"})
        self.v.close()
        db = self._raw()
        db.execute("UPDATE records SET kind='QUESTION' WHERE id=?", (lid,))
        db.close()
        self.v = Vault.open(self.dir, PW)
        with self.assertRaises(IntegrityError):
            self.v.get(lid)

    def test_silent_row_deletion_detected(self):
        lid = self.v.log({"type": "DOSE_TAKEN"})
        self.v.close()
        db = self._raw()
        db.execute("DELETE FROM records WHERE id=?", (lid,))
        db.close()
        self.v = Vault.open(self.dir, PW)
        state = self.v.verify()
        self.assertFalse(state["valid"])
        self.assertTrue(any("missing" in p for p in state["problems"]))

    def test_row_from_other_vault_rejected(self):
        other = Vault.create(self.tmp / "other", PW)
        oid = other.log({"type": "READING", "glucose": 300})
        other.close()
        self.v.close()
        src = sqlite3.connect(self.tmp / "other" / DB_FILE)
        row = src.execute("SELECT id, kind, ref, supersedes, nonce, ciphertext FROM records WHERE id=?",
                          (oid,)).fetchone()
        src.close()
        db = self._raw()
        db.execute("INSERT INTO records(id, kind, ref, supersedes, nonce, ciphertext) VALUES (?,?,?,?,?,?)", row)
        db.close()
        self.v = Vault.open(self.dir, PW)
        with self.assertRaises(IntegrityError):
            self.v.get(oid)
        self.assertTrue(any("no audit record" in p for p in self.v.verify()["problems"]))

    def test_db_from_other_vault_refused(self):
        Vault.create(self.tmp / "other", PW).close()
        self.v.close()
        shutil.copy(self.tmp / "other" / DB_FILE, self.dir / DB_FILE)
        with self.assertRaises(IntegrityError):
            Vault.open(self.dir, PW)
        shutil.rmtree(self.dir)
        self.v = Vault.create(self.dir, PW)

    def test_audit_edit_blocks_open(self):
        self.v.log({"type": "MEAL"})
        self.v.close()
        lines = (self.dir / AUDIT_FILE).read_text().splitlines()
        rec = json.loads(lines[-1])
        rec["kind"] = "SETTING"
        lines[-1] = json.dumps(rec, separators=(",", ":"))
        (self.dir / AUDIT_FILE).write_text("\n".join(lines) + "\n")
        with self.assertRaises(IntegrityError):
            Vault.open(self.dir, PW)
        shutil.rmtree(self.dir)
        self.v = Vault.create(self.dir, PW)


class TestAudit(VaultCase):
    def test_every_write_audited_without_content(self):
        self.v.log({"type": "DOSE_TAKEN", "medication": "Lisinopril"})
        self.v.add_document("x.pdf", b"abc")
        events = [json.loads(l) for l in (self.dir / AUDIT_FILE).read_text().splitlines()]
        self.assertEqual([e["event"] for e in events], ["VAULT_CREATED", "WRITE", "WRITE"])
        self.assertNotIn("Lisinopril", (self.dir / AUDIT_FILE).read_text())
        self.assertTrue(self.v.verify()["valid"])

    def test_verify_clean(self):
        for i in range(5):
            self.v.log({"type": "READING", "i": i})
        s = self.v.verify()
        self.assertTrue(s["valid"], s)
        self.assertEqual(s["records"], 5)


class TestSemantics(VaultCase):
    def test_document_dedupe(self):
        a, new_a = self.v.add_document("a.pdf", b"same bytes")
        b, new_b = self.v.add_document("renamed.pdf", b"same bytes")
        self.assertEqual(a, b)
        self.assertTrue(new_a)
        self.assertFalse(new_b)

    def test_correction_supersedes_never_overwrites(self):
        first = self.v.log({"type": "DOSE_TAKEN", "time": "08:00"})
        second = self.v.log({"type": "DOSE_TAKEN", "time": "09:00"}, supersedes=first)
        current = list(self.v.iter("USER_LOG"))
        self.assertEqual([r.id for r in current], [second])
        self.assertEqual([r.body["time"] for r in self.v.history(second)], ["08:00", "09:00"])
        self.assertEqual(len(list(self.v.iter("USER_LOG", current_only=False))), 2)

    def test_supersede_across_kinds_refused(self):
        doc, _ = self.v.add_document("a.pdf", b"x")
        with self.assertRaises(StoreError):
            self.v.log({"type": "MEAL"}, supersedes=doc)

    def test_unknown_kind_refused(self):
        with self.assertRaises(StoreError):
            self.v.put("DIAGNOSIS", {"x": 1})

    def test_settings_validated_and_latest_wins(self):
        self.v.set_setting("data_path", "REVIEW", SETTING_VALUES)
        self.v.set_setting("ai_mode", "PAi", SETTING_VALUES)
        self.v.set_setting("ai_mode", "None", SETTING_VALUES)
        with self.assertRaises(ValueError):
            self.v.set_setting("ai_mode", "GPT", SETTING_VALUES)
        self.assertEqual(self.reopen().settings(), {"data_path": "REVIEW", "ai_mode": "None"})

    def test_repeat_setting_not_rewritten(self):
        a = self.v.set_setting("ai_mode", "PAi", SETTING_VALUES)
        self.assertEqual(a, self.v.set_setting("ai_mode", "PAi", SETTING_VALUES))

    def test_purge_removes_and_audits(self):
        first = self.v.log({"type": "MEAL", "note": "private"})
        second = self.v.log({"type": "MEAL", "note": "private 2"}, supersedes=first)
        with self.assertRaises(StoreError):
            self.v.purge(first)  # must purge newest version
        self.v.purge(second)
        self.assertEqual(list(self.v.iter("USER_LOG", current_only=False)), [])
        self.assertTrue(self.v.verify()["valid"])
        events = [json.loads(l)["event"] for l in (self.dir / AUDIT_FILE).read_text().splitlines()]
        self.assertEqual(events.count("PURGE"), 2)

    def test_engine_cache(self):
        self.v.cache_output("sig-1", {"floats": [1]})
        self.v.cache_output("sig-1", {"floats": [2]})
        self.assertEqual(self.v.cached_output("sig-1"), {"floats": [2]})
        self.assertIsNone(self.v.cached_output("sig-2"))

    def test_document_text_versions(self):
        doc, _ = self.v.add_document("a.pdf", b"x")
        self.v.put_document_text(doc, {"canonical_text": "v1"})
        self.v.put_document_text(doc, {"canonical_text": "v2"})
        ref = self.v.tag("document-text", doc)
        self.assertEqual([r.body["canonical_text"] for r in self.v.iter("DOCUMENT_TEXT", ref=ref)], ["v2"])


class TestBackup(VaultCase):
    def test_round_trip(self):
        doc, _ = self.v.add_document("a.pdf", b"%PDF data")
        self.v.set_setting("ai_mode", "PAi", SETTING_VALUES)
        out = create_backup(self.v, self.tmp / "b.paihbk", BPW)
        raw = out.read_bytes()
        self.assertNotIn(b"%PDF data", raw)
        state = restore_backup(out, self.tmp / "restored", BPW, PW)
        self.assertTrue(state["valid"], state)
        with Vault.open(self.tmp / "restored", PW) as r:
            self.assertEqual(r.get(doc).body, b"%PDF data")
            self.assertEqual(r.settings(), {"ai_mode": "PAi"})

    def test_wrong_backup_passphrase(self):
        out = create_backup(self.v, self.tmp / "b.paihbk", BPW)
        with self.assertRaises(ValueError):
            restore_backup(out, self.tmp / "restored", "not the backup phrase", PW)
        self.assertFalse((self.tmp / "restored").exists())

    def test_altered_backup(self):
        out = create_backup(self.v, self.tmp / "b.paihbk", BPW)
        data = bytearray(out.read_bytes())
        data[40] ^= 1
        out.write_bytes(bytes(data))
        with self.assertRaises(ValueError):
            restore_backup(out, self.tmp / "restored", BPW, PW)

    def test_restore_refuses_nonempty_target(self):
        out = create_backup(self.v, self.tmp / "b.paihbk", BPW)
        with self.assertRaises(FileExistsError):
            restore_backup(out, self.dir, BPW, PW)

    def test_backup_inside_vault_refused(self):
        with self.assertRaises(ValueError):
            create_backup(self.v, self.dir / "b.paihbk", BPW)

    def test_backup_refused_when_vault_invalid(self):
        lid = self.v.log({"type": "MEAL"})
        db = sqlite3.connect(self.dir / DB_FILE, isolation_level=None)
        db.execute("DELETE FROM records WHERE id=?", (lid,))
        db.close()
        with self.assertRaises(RuntimeError):
            create_backup(self.v, self.tmp / "b.paihbk", BPW)


class TestServerWiring(VaultCase):
    """A setting posted through the loopback server lands in the vault, audited, and survives restart."""

    def test_setting_persists_through_server(self):
        import http.client
        from health_server import HealthServer
        from run_engine import settings_handlers
        dist = self.tmp / "dist"
        dist.mkdir()
        (dist / "index.html").write_text("<html></html>")
        tracker = {"settings": {"ai_mode": "None", "data_path": "REVIEW", "device_dose_confirmation": False}}
        view, sink = settings_handlers(tracker, self.v)
        server = HealthServer(dist, lambda: ({}, True), view, sink)
        url = server.start()
        try:
            host, rest = url.split("//", 1)[1].split("/", 1)
            prefix = "/" + rest.rstrip("/") + "/"

            def post(body):
                c = http.client.HTTPConnection(host)
                c.request("POST", prefix + "api/settings", json.dumps(body),
                          {"X-PAi-Request": "1", "Content-Type": "application/json"})
                r = c.getresponse()
                return r.status, r.read()

            self.assertEqual(post({"key": "ai_mode", "value": "PAi"})[0], 200)
            self.assertEqual(post({"key": "data_path", "value": "LIVE"})[0], 400)
        finally:
            server.stop()
        v = self.reopen()
        self.assertEqual(v.settings(), {"ai_mode": "PAi"})
        self.assertTrue(v.verify()["valid"])


if __name__ == "__main__":
    unittest.main()
