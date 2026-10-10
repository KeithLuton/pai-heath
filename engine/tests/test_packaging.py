"""Microsoft Store packaging: manifest, firewall/port agreement, data notice consent, backups
outside the package, bundled resources, process cleanup, single instance, logo set."""
from __future__ import annotations

import os
import re
import shutil
import socket
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import runtime                                              # noqa: E402
from ai import local_ai                                     # noqa: E402
from app_state import App, AppError                         # noqa: E402
from companion import service as S                          # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
PKG = ROOT / "packaging"
PW = "correct horse battery staple"
NS = {"m": "http://schemas.microsoft.com/appx/manifest/foundation/windows10",
      "uap": "http://schemas.microsoft.com/appx/manifest/uap/windows10",
      "desktop2": "http://schemas.microsoft.com/appx/manifest/desktop/windows10/2",
      "rescap": "http://schemas.microsoft.com/appx/manifest/foundation/windows10/restrictedcapabilities"}


class ManifestTest(unittest.TestCase):
    def setUp(self):
        text = (PKG / "AppxManifest.template.xml").read_text(encoding="utf-8")
        self.placeholders = set(re.findall(r"\{\{(\w+)\}\}", text))
        filled = (text.replace("{{IDENTITY_NAME}}", "PAiLuton.PAiHealth").replace("{{PUBLISHER}}", "CN=Test")
                  .replace("{{PUBLISHER_DISPLAY_NAME}}", "PAi Luton").replace("{{VERSION}}", "1.0.0.0"))
        self.root = ET.fromstring(filled.encode())

    def test_placeholders_are_exactly_the_ones_build_fills(self):
        build = (PKG / "build.ps1").read_text(encoding="utf-8")
        self.assertEqual(self.placeholders, {"IDENTITY_NAME", "PUBLISHER", "PUBLISHER_DISPLAY_NAME", "VERSION"})
        for p in self.placeholders:
            self.assertIn("{{%s}}" % p, build)

    def test_capabilities_are_only_what_the_app_uses(self):
        caps = {c.get("Name") for c in self.root.find("m:Capabilities", NS)}
        self.assertEqual(caps, {"internetClient", "privateNetworkClientServer", "runFullTrust", "bluetooth"})

    def test_firewall_rule_matches_companion_ports_private_only(self):
        rules = self.root.findall(".//desktop2:Rule", NS)
        self.assertEqual(len(rules), 1)
        r = rules[0]
        self.assertEqual((r.get("Direction"), r.get("IPProtocol"), r.get("Profile")), ("in", "TCP", "private"))
        self.assertEqual(range(int(r.get("LocalPortMin")), int(r.get("LocalPortMax")) + 1), runtime.COMPANION_PORTS)
        fw = self.root.find(".//desktop2:FirewallRules", NS)
        app = self.root.find(".//m:Application", NS)
        self.assertEqual(fw.get("Executable"), app.get("Executable"))

    def test_executable_matches_pyinstaller_name(self):
        app = self.root.find(".//m:Application", NS)
        self.assertEqual(app.get("Executable"), r"PAiHealth\PAiHealth.exe")
        self.assertEqual(app.get("EntryPoint"), "Windows.FullTrustApplication")
        spec = (PKG / "paihealth.spec").read_text(encoding="utf-8")
        self.assertIn('name="PAiHealth"', spec)
        self.assertIn("console=False", spec)
        self.assertIn("upx=False", spec)

    def test_every_referenced_logo_is_generated(self):
        sys.path.insert(0, str(PKG))
        import make_assets
        out = Path(tempfile.mkdtemp())
        try:
            make_assets.build(out, None)
            text = (PKG / "AppxManifest.template.xml").read_text(encoding="utf-8")
            for ref in re.findall(r"Assets\\(\w+)\.png", text):
                for s in make_assets.SCALES:
                    self.assertTrue((out / f"{ref}.scale-{s}.png").is_file(), ref)
            for t in make_assets.TARGET_SIZES:
                self.assertTrue((out / f"Square44x44Logo.targetsize-{t}_altform-unplated.png").is_file())
            from PIL import Image
            self.assertEqual(Image.open(out / "Wide310x150Logo.scale-200.png").size, (620, 300))
            self.assertEqual(Image.open(out / "listing" / "StoreIcon-300.png").size, (300, 300))
            self.assertTrue((out / "PAiHealth.ico").is_file())
        finally:
            shutil.rmtree(out, ignore_errors=True)

    def test_min_os_and_version_rules(self):
        dep = self.root.find(".//m:TargetDeviceFamily", NS)
        self.assertEqual(dep.get("Name"), "Windows.Desktop")
        self.assertGreaterEqual(tuple(map(int, dep.get("MinVersion").split("."))), (10, 0, 17763, 0))
        self.assertRegex(runtime.APP_VERSION, r"^\d+\.\d+\.\d+$")
        build = (PKG / "build.ps1").read_text(encoding="utf-8")
        self.assertIn("APP_VERSION", build)
        self.assertIn(r"^\d+\.\d+\.\d+\.0$", build)          # Store: revision must be 0


class UiAgreementTest(unittest.TestCase):
    def test_notice_version_and_links_agree(self):
        about = (ROOT / "src" / "components" / "About.tsx").read_text(encoding="utf-8")
        self.assertIn(f"NOTICE_VERSION = '{runtime.NOTICE_VERSION}'", about)
        url = re.search(r"PRIVACY_URL = '([^']+)'", about).group(1)
        self.assertIn(url, (ROOT / "store" / "LISTING.md").read_text(encoding="utf-8"))
        self.assertIn("notice: NOTICE_VERSION", (ROOT / "src" / "setup" / "Setup.tsx").read_text(encoding="utf-8"))

    def test_nlm_credit_everywhere_it_must_be(self):
        credit = "NLM is not responsible for the product and does not endorse or recommend this or any other product"
        for f in ("src/components/About.tsx", "store/LISTING.md", "store/privacy-policy.html",
                  "packaging/NOTICES_HEADER.txt"):
            self.assertIn(credit, (ROOT / f).read_text(encoding="utf-8"), f)

    def test_runtime_dependencies_declared(self):
        req = (ROOT / "engine" / "requirements.txt").read_text(encoding="utf-8")
        for name in ("cryptography", "psutil", "segno", "pytesseract", "pypdfium2"):
            self.assertRegex(req, rf"(?m)^{name}\b", name)


class AppPackagingTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.docs = Path(tempfile.mkdtemp())
        os.environ["PAI_HEALTH_HOME"] = str(self.tmp)
        os.environ["PAI_HEALTH_DOCUMENTS"] = str(self.docs)
        self.app = App(root=self.tmp)

    def tearDown(self):
        self.app.lock_user()
        os.environ.pop("PAI_HEALTH_HOME", None)
        os.environ.pop("PAI_HEALTH_DOCUMENTS", None)
        shutil.rmtree(self.tmp, ignore_errors=True)
        shutil.rmtree(self.docs, ignore_errors=True)

    def test_data_notice_consent_recorded_in_audit(self):
        self.app.create_user("Ann", PW, runtime.NOTICE_VERSION)
        events = [r for r in self.app.vault.audit._read() if r.get("event") == "DATA_NOTICE_ACCEPTED"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["notice"], runtime.NOTICE_VERSION)
        self.assertTrue(self.app.vault.audit.verify()["valid"])

    def test_stale_notice_refused(self):
        with self.assertRaises(AppError) as cm:
            self.app.create_user("Ann", PW, "PAIH-DATA-NOTICE-v0")
        self.assertEqual(cm.exception.status, 400)

    def test_backup_goes_to_documents_outside_app_data(self):
        self.app.create_user("Ann", PW, runtime.NOTICE_VERSION)
        out = self.app.backup("backup passphrase 1")
        path = Path(out["path"])
        self.assertTrue(path.is_file())
        self.assertEqual(path.parent, self.docs / "PAi Health Backups")
        with self.assertRaises(ValueError):
            path.relative_to(self.tmp)
        self.assertNotIn(PW.encode(), path.read_bytes())
        with self.assertRaises(AppError):
            self.app.backup("short")

    def test_bundled_kb_is_found_when_none_installed(self):
        res = Path(tempfile.mkdtemp())
        try:
            (res / "resources" / "knowledge").mkdir(parents=True)
            fake = res / "resources" / "knowledge" / "psi_health_kb_test.db"
            fake.write_bytes(b"")
            with mock.patch.object(runtime, "install_root", return_value=res):
                self.assertEqual(runtime.bundled_kb(), fake)
                self.assertEqual(runtime.ui_dir(), res / "dist")
        finally:
            shutil.rmtree(res, ignore_errors=True)

    def test_companion_stays_inside_firewall_ports(self):
        from tests.test_companion import lan_test_host
        host = lan_test_host()
        busy = socket.socket()
        busy.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)     # earlier tests leave TIME_WAIT
        busy.bind((host, runtime.COMPANION_PORTS[0]))
        busy.listen(1)
        try:
            self.app.create_user("Ann", PW, runtime.NOTICE_VERSION)
            st = self.app.companion_enable(True, host=host)
            port = int(st["address"].rsplit(":", 1)[1])
            self.assertIn(port, runtime.COMPANION_PORTS)
            self.assertNotEqual(port, runtime.COMPANION_PORTS[0])
        finally:
            self.app.companion_enable(False)
            busy.close()

    def test_about_route_data(self):
        a = self.app.about()
        self.assertEqual(a["version"], runtime.APP_VERSION)
        self.assertEqual(a["backup_folder"], str(self.docs / "PAi Health Backups"))
        self.assertFalse(a["packaged"])


class ProcessCleanupTest(unittest.TestCase):
    def test_close_all_stops_registered_models(self):
        m = local_ai.LocalModel("analysis", resources={})
        proc = mock.Mock()
        m._proc = proc
        local_ai._LIVE.add(m)
        local_ai.close_all()
        proc.terminate.assert_called_once()
        self.assertNotIn(m, local_ai._LIVE)
        self.assertIsNone(m._proc)

    def test_single_instance(self):
        import desktop
        d = Path(tempfile.mkdtemp())
        try:
            a, b = desktop.SingleInstance(d / "x.lock"), desktop.SingleInstance(d / "x.lock")
            self.assertTrue(a.acquire())
            self.assertFalse(b.acquire())
            a.release()
            self.assertTrue(b.acquire())
            b.release()
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_app_start_and_shutdown(self):
        import urllib.request
        import app as app_mod
        tmp = Path(tempfile.mkdtemp())
        os.environ["PAI_HEALTH_HOME"] = str(tmp)
        try:
            a, server, url = app_mod.start()
            self.assertTrue(url.startswith("http://127.0.0.1:"))
            self.assertEqual(urllib.request.urlopen(url + "api/about", timeout=5).status, 200)
            app_mod.shutdown(a, server)
            with self.assertRaises(OSError):
                urllib.request.urlopen(url + "api/about", timeout=2)
        finally:
            os.environ.pop("PAI_HEALTH_HOME", None)
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
