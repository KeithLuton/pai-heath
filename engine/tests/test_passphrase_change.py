"""Passphrase rewrap invariants, including a simulated interrupted replace."""
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from storage import Vault, VaultLocked
from storage import keys

OLD = 'original secure passphrase'
NEW = 'replacement secure passphrase'


class ChangePassphraseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'vault'
        self.vault = Vault.create(self.path, OLD)

    def tearDown(self):
        self.vault.close()
        self.tmp.cleanup()

    def ciphertexts(self):
        db = sqlite3.connect(self.path / 'health.db')     # `with sqlite3.connect()` doesn't close; Windows needs it closed
        try:
            return db.execute('SELECT id, nonce, ciphertext FROM records ORDER BY seq').fetchall()
        finally:
            db.close()

    def test_change_keeps_ciphertext_and_audit(self):
        rec = self.vault.log({'type': 'READING', 'value': 100})
        before = self.ciphertexts()
        self.vault.change_passphrase(OLD, NEW)
        self.assertEqual(before, self.ciphertexts())
        self.assertTrue(self.vault.verify()['valid'])
        self.assertIn('PASSPHRASE_CHANGED', [e['event'] for e in self.vault.audit._read()])
        self.assertFalse((self.path / 'vault.key.prev').exists())
        with self.assertRaises(VaultLocked):
            Vault.open(self.path, OLD)
        with Vault.open(self.path, NEW) as reopened:
            self.assertEqual(reopened.get(rec).body['value'], 100)

    def test_wrong_old_and_short_new(self):
        with self.assertRaises(VaultLocked):
            self.vault.change_passphrase('wrong old passphrase', NEW)
        with self.assertRaises(ValueError):
            self.vault.change_passphrase(OLD, 'short')
        with Vault.open(self.path, OLD):
            pass

    def test_replace_failure_still_opens_with_old(self):
        self.vault.log({'type': 'READING', 'value': 99})
        before = self.ciphertexts()
        with patch.object(keys.os, 'replace', side_effect=OSError('simulated crash')):
            with self.assertRaises(OSError):
                self.vault.change_passphrase(OLD, NEW)
        self.assertEqual(before, self.ciphertexts())
        with Vault.open(self.path, OLD) as reopened:
            self.assertTrue(reopened.verify()['valid'])
        with self.assertRaises(VaultLocked):
            Vault.open(self.path, NEW)
        self.assertTrue((self.path / 'vault.key.prev').exists())
        self.assertFalse((self.path / 'vault.key.tmp').exists())


    def test_retry_after_failed_swap(self):
        with patch.object(keys.os, 'replace', side_effect=OSError('simulated crash')):
            with self.assertRaises(OSError):
                self.vault.change_passphrase(OLD, NEW)
        self.vault.change_passphrase(OLD, NEW)          # stale .prev equals active key, so retry proceeds
        self.assertFalse((self.path / 'vault.key.prev').exists())
        with Vault.open(self.path, NEW):
            pass

    def test_route(self):
        import json, os, shutil, urllib.request
        import app as app_mod
        from app_state import App
        root = Path(self.tmp.name) / 'app'
        a = App(root=root)
        a.create_user('Ann', OLD)
        server = app_mod.make_server(a)
        url = server.start()
        try:
            req = urllib.request.Request(url + 'api/users/passphrase', method='POST',
                                         data=json.dumps({'old': OLD, 'new': NEW}).encode(),
                                         headers={'Content-Type': 'application/json', 'X-PAi-Request': '1'})
            self.assertEqual(json.loads(urllib.request.urlopen(req, timeout=10).read()), {'ok': True})
            uid = a.user
            a.lock_user()
            self.assertEqual(a.unlock(uid, NEW)['id'], uid)
        finally:
            server.stop()
            a.lock_user()

if __name__ == '__main__':
    unittest.main()
