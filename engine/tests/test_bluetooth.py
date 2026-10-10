"""Bluetooth LE decoding and capture. Cases from the bluetooth test build, plus the
spec corner cases it missed. No hardware: packets are built by hand from the GATT specs."""
import json
import shutil
import struct
import sys
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from devices.bluetooth.collector import batch_record, export_packet, to_samples  # noqa: E402
from devices.bluetooth.protocol import decode, float11073, sfloat                  # noqa: E402

B = "-0000-1000-8000-00805f9b34fb"
HR, BP, TEMP, WT, SPOT, GLU = ("00002a37" + B, "00002a35" + B, "00002a1c" + B, "00002a9d" + B,
                               "00002a5e" + B, "00002a18" + B)
T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def sf(m, e=0):
    return ((e & 0xF) << 12) | (m & 0xFFF)


def dt(y, mo, d, h, mi, s):
    return struct.pack("<HBBBBB", y, mo, d, h, mi, s)


class Decode(unittest.TestCase):
    # --- from the test build
    def test_hr_8bit(self):
        self.assertEqual(decode(HR, bytes([0, 72]))[0]["value"], 72)

    def test_hr_16bit(self):
        self.assertEqual(decode(HR, bytes([1, 44, 1]))[0]["value"], 300)

    def test_truncated(self):
        with self.assertRaises(ValueError):
            decode(HR, b"\x01")

    def test_bp(self):
        rows = decode(BP, bytes([0, 120, 0, 80, 0, 93, 0]))
        self.assertEqual([r["value"] for r in rows], [120, 80, 93])
        self.assertEqual(rows[0]["measure"], "bp_systolic")

    def test_weight(self):
        self.assertEqual(decode(WT, bytes([0, 200, 0]))[0]["value"], 1.0)

    # --- spec cases the test build missed
    def test_sfloat_specials(self):
        for raw in (0x07FF, 0x0800, 0x07FE, 0x0802, 0x0801):
            self.assertIsNone(sfloat(raw), hex(raw))
        self.assertEqual(sfloat(sf(367, -1)), 36.7)
        self.assertIsNone(float11073(0x007FFFFF))

    def test_weight_unsuccessful_is_no_value(self):
        self.assertEqual(decode(WT, bytes([0, 0xFF, 0xFF])), [])

    def test_bp_with_time_and_pulse(self):
        payload = bytes([0x06]) + struct.pack("<HHH", sf(128), sf(82), sf(97)) + dt(2026, 9, 30, 7, 15, 0) + \
            struct.pack("<H", sf(64))
        rows = decode(BP, payload)
        self.assertEqual([(r["measure"], r["value"]) for r in rows],
                         [("bp_systolic", 128), ("bp_diastolic", 82), ("bp_mean", 97), ("heart_rate", 64)])
        self.assertTrue(all(r["device_time"] == "2026-09-30T07:15:00" for r in rows))
        self.assertEqual(rows[3]["meta"]["method"], "BP_CUFF")

    def test_bp_kpa_unit_kept_in_decode(self):
        rows = decode(BP, bytes([0x01]) + struct.pack("<HHH", sf(160, -1), sf(107, -1), sf(125, -1)))
        self.assertEqual((rows[0]["value"], rows[0]["unit"]), (16.0, "kPa"))

    def test_bp_truncated_timestamp(self):
        with self.assertRaises(ValueError):
            decode(BP, bytes([0x02]) + struct.pack("<HHH", sf(120), sf(80), sf(90)) + b"\x00\x00")

    def test_temperature_f(self):
        raw = (0xFF << 24) | 986          # 98.6 with exponent -1
        rows = decode(TEMP, bytes([0x01]) + struct.pack("<I", raw))
        self.assertEqual((rows[0]["value"], rows[0]["unit"]), (98.6, "degF"))

    def test_hr_no_contact_flagged(self):
        self.assertEqual(decode(HR, bytes([0x04, 0]))[0]["meta"], {"sensor_contact": "NOT_DETECTED"})
        self.assertNotIn("meta", decode(HR, bytes([0x06, 70]))[0])

    def test_spot_check(self):
        rows = decode(SPOT, bytes([0]) + struct.pack("<HH", sf(97), sf(68)))
        self.assertEqual([(r["measure"], r["value"]) for r in rows], [("spo2", 97), ("heart_rate", 68)])

    def test_glucose_emits_nothing(self):
        self.assertEqual(decode(GLU, bytes(10)), [])


class Capture(unittest.TestCase):
    def test_packet_provenance(self):
        row = export_packet(HR, b"\x00\x48", "test", "AA", 0, T0)
        self.assertEqual((row["standing"], row["raw_hex"], row["measurements"][0]["value"]),
                         ("DEVICE_UNVERIFIED", "0048", 72))

    def test_malformed_preserved(self):
        row = export_packet(HR, b"\x01", "test", "AA", 0, T0)
        self.assertTrue(row["parse_error"])
        self.assertEqual(row["raw_hex"], "01")

    def test_samples_convert_and_say_time_basis(self):
        tz = timezone(timedelta(hours=-4))
        stored = bytes([0x03]) + struct.pack("<HHH", sf(160, -1), sf(107, -1), sf(125, -1)) + dt(2026, 9, 29, 21, 0, 0)
        live = b"\x00\x48"
        bad = b"\x01"
        pk = [export_packet(BP, stored, "Cuff", "AA", 0, T0), export_packet(HR, live, "Cuff", "AA", 1, T0),
              export_packet(HR, bad, "Cuff", "AA", 2, T0)]
        samples, rep = to_samples(pk, tz)
        sys_ = next(s for s in samples if s.measure == "bp_systolic")
        self.assertEqual((sys_.value, sys_.unit, sys_.conversion), (120.01, "mmHg", "kPa to mmHg"))
        self.assertEqual(sys_.start.isoformat(), "2026-09-29T21:00:00-04:00")
        self.assertEqual(sys_.metadata["time_basis"], "DEVICE_CLOCK")
        self.assertEqual(sys_.device_class, "HOME_CUFF")
        hr = next(s for s in samples if s.measure == "heart_rate")
        self.assertEqual((hr.start, hr.metadata["time_basis"]), (T0, "RECEIVED_AT"))
        self.assertEqual(rep.rejected, [(2, "Truncated heart_rate notification")])
        self.assertEqual(rep.importer_status, "UNVERIFIED_AGAINST_REAL_EXPORT")

    def test_batch_goes_into_vault_encrypted(self):
        from storage import Vault
        tmp = Path(tempfile.mkdtemp())
        try:
            v = Vault.create(tmp / "v", "a long test passphrase")
            rec = batch_record([export_packet(HR, b"\x00\x48", "Polar H10", "AA:BB", 0, T0)])
            rid = v.put("DEVICE_BATCH", rec, meta={"source": "bluetooth_le"})
            self.assertEqual(v.get(rid).body["samples"][0]["value"], 72.0)
            v.close()
            self.assertNotIn(b"Polar", (tmp / "v" / "health.db").read_bytes())
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class AppWiring(unittest.TestCase):
    """The app lists saved Bluetooth batches; without bleak it says how to install, never crashes."""

    def test_status_and_batches(self):
        import os
        from app_state import App
        tmp = Path(tempfile.mkdtemp())
        os.environ["PAI_HEALTH_HOME"] = str(tmp)
        try:
            app = App(root=tmp)
            app.create_user("BT", "a long test passphrase")
            self.assertIn("bleak", app.bluetooth_status()["install"])
            rec = batch_record([export_packet(HR, b"\x00\x48", "Polar H10", "AA:BB", 0, T0)])
            app.vault.put("DEVICE_BATCH", rec, meta={"source": "bluetooth_le"})
            rows = app.device_batches()
            self.assertEqual((rows[0]["device_name"], rows[0]["samples"], rows[0]["recent"][0]["value"]),
                             ("Polar H10", 1, 72.0))
            with self.assertRaises(ValueError) as cm:
                app.bluetooth_collect("not an address; rm -rf", 10)
            self.assertIn("Invalid device address", str(cm.exception))
            app.lock_user()
        finally:
            os.environ.pop("PAI_HEALTH_HOME", None)
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
