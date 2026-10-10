"""Offline Bluetooth LE scan and capture (BLE only, not Bluetooth Classic). Uses `bleak`.

Merged from the bluetooth test build. Changes:
  * Captures go into the user's encrypted vault (DEVICE_BATCH), never a
    plaintext file. A plaintext JSONL file exists only behind an explicit
    --unencrypted-test-file flag for bench testing.
  * Packets become normalized DeviceSamples (schema.py, §20): unit
    conversions logged, device time used when the packet carries one, the
    capture time otherwise — and the sample says which.
  * Every raw packet (hex) is kept beside the samples, so any value can be
    re-decoded from exactly what the device sent.

    python -m devices.bluetooth.collector scan
    python -m devices.bluetooth.collector collect ADDRESS --vault %LOCALAPPDATA%\\PAiHealth\\users\\<id>\\vault
"""
from __future__ import annotations

import argparse
import asyncio
import getpass
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from ..schema import DeviceSample, ImportReport, Provenance
from .protocol import CHARACTERISTICS, SERVICES, decode

IMPORTER = "bluetooth_le"
# §20.2 device class, from the GATT profile the device itself exposes. A heart-rate
# notification can come from a strap, a watch or a bike sensor, so it stays generic.
DEVICE_CLASS = {"blood_pressure": "HOME_CUFF", "weight": "SMART_SCALE", "temperature": "THERMOMETER",
                "pulse_ox_spot": "PULSE_OXIMETER", "heart_rate": "OTHER_SOURCE", "glucose": "GLUCOSE_METER"}
CONVERT = {("bp_systolic", "kPa"): ("mmHg", 7.50062, "kPa to mmHg"),
           ("bp_diastolic", "kPa"): ("mmHg", 7.50062, "kPa to mmHg"),
           ("bp_mean", "kPa"): ("mmHg", 7.50062, "kPa to mmHg"),
           ("body_mass", "lb"): ("kg", 0.45359237, "lb to kg")}


def available() -> bool:
    try:
        import bleak  # noqa: F401
        return True
    except ImportError:
        return False


def export_packet(uuid: str, data: bytes, device_name: str, address: str, index: int,
                  captured: Optional[datetime] = None) -> dict:
    """One raw transport observation, exactly as received, plus its decode."""
    captured = captured or datetime.now(timezone.utc)
    raw = bytes(data)
    identity = hashlib.sha256(f"{str(uuid).lower()}|{address}|{raw.hex()}|{index}|{captured.isoformat()}"
                              .encode()).hexdigest()
    try:
        values, error = decode(uuid, raw), None
    except ValueError as exc:
        values, error = [], str(exc)
    return {"event_id": identity, "standing": "DEVICE_UNVERIFIED", "captured_at": captured.isoformat(),
            "device_name": device_name, "device_address": address, "characteristic": str(uuid).lower(),
            "profile": CHARACTERISTICS.get(str(uuid).lower()), "raw_hex": raw.hex(), "packet_index": index,
            "measurements": values, "parse_error": error, "source": "bluetooth_le_notification",
            "transport_provenance": "RAW_PACKET"}


def to_samples(packets: List[dict], local_tz=None) -> Tuple[List[DeviceSample], ImportReport]:
    """Packets -> DeviceSamples. Device clock times are naive; they are read in this computer's time zone (declared)."""
    local_tz = local_tz or datetime.now().astimezone().tzinfo
    batch_sha = hashlib.sha256("\n".join(p["event_id"] for p in packets).encode()).hexdigest()
    first = packets[0] if packets else {}
    report = ImportReport(IMPORTER, "UNVERIFIED_AGAINST_REAL_EXPORT",
                          f"BLE capture {first.get('device_name', '')}".strip(), batch_sha)
    report.notes.append("Device clock times carry no time zone; read as this computer's time zone.")
    samples: List[DeviceSample] = []
    for p in packets:
        if p["parse_error"]:
            report.rejected.append((p["packet_index"], p["parse_error"]))
            continue
        if not p["measurements"]:
            report.unmapped_types[p["profile"] or p["characteristic"]] = \
                report.unmapped_types.get(p["profile"] or p["characteristic"], 0) + 1
            continue
        captured = datetime.fromisoformat(p["captured_at"])
        for m in p["measurements"]:
            value, unit, conv = m["value"], m["unit"], None
            if (m["measure"], unit) in CONVERT:
                unit, factor, conv = CONVERT[(m["measure"], unit)]
                value = round(value * factor, 3)
            if m["measure"] == "body_temperature" and unit == "degF":
                value, unit, conv = round((value - 32.0) * 5.0 / 9.0, 3), "degC", "degF to degC"
            if m.get("device_time"):
                t = datetime.fromisoformat(m["device_time"]).replace(tzinfo=local_tz)
                time_basis = "DEVICE_CLOCK"
            else:
                t, time_basis = captured, "RECEIVED_AT"
            meta = {"time_basis": time_basis, "packet_event_id": p["event_id"], **(m.get("meta") or {})}
            s = DeviceSample(m["measure"], value, unit, t, t, p["device_name"], IMPORTER, None,
                             DEVICE_CLASS.get(p["profile"], "OTHER_SOURCE"),
                             (m.get("meta") or {}).get("method"), None,
                             Provenance(report.file_name, batch_sha, p["packet_index"], p["profile"]),
                             conversion=conv, metadata=meta)
            samples.append(s)
            report.by_measure[s.measure] = report.by_measure.get(s.measure, 0) + 1
    report.samples = len(samples)
    report.by_source = {first.get("device_name", ""): len(samples)} if samples else {}
    if samples:
        report.first = min(s.start for s in samples).isoformat()
        report.last = max(s.start for s in samples).isoformat()
    return samples, report


def batch_record(packets: List[dict]) -> dict:
    samples, report = to_samples(packets)
    first = packets[0] if packets else {}
    return {"source": IMPORTER, "device_name": first.get("device_name"), "device_address": first.get("device_address"),
            "packets": packets, "samples": [s.as_dict() for s in samples], "report": report.__dict__}


async def scan(seconds: int) -> List[dict]:
    from bleak import BleakScanner
    found = await BleakScanner.discover(timeout=seconds, return_adv=True)
    out = []
    for address, (dev, adv) in sorted(found.items()):
        services = sorted({s.lower() for s in (adv.service_uuids or [])})
        out.append({"name": dev.name, "address": address, "service_uuids": services, "rssi": adv.rssi,
                    "possible_health_device": any(short in s for s in services for short in SERVICES)})
    return out


async def collect(address: str, seconds: int, on_packet: Optional[Callable[[dict], None]] = None) -> List[dict]:
    from bleak import BleakClient
    packets: List[dict] = []
    async with BleakClient(address, timeout=20) as client:
        name = str(getattr(client, "name", None) or "BLE device")
        chars = [c for s in client.services for c in s.characteristics
                 if str(c.uuid).lower() in CHARACTERISTICS and ("notify" in c.properties or "indicate" in c.properties)]
        if not chars:
            raise RuntimeError("This device has no standard Bluetooth health measurements (GATT). "
                               "Many watches only share data through their own app — use that app's export instead.")

        def on_data(sender, data):
            p = export_packet(str(sender.uuid), data, name, address, len(packets))
            packets.append(p)
            if on_packet:
                on_packet(p)
        started = []
        try:
            for c in chars:
                await client.start_notify(c, on_data)
                started.append(c)
            await asyncio.sleep(seconds)
        finally:
            for c in started:
                if client.is_connected:
                    try:
                        await client.stop_notify(c)
                    except Exception:
                        pass
    return packets


def main() -> int:
    ap = argparse.ArgumentParser(description="PAi Health Bluetooth LE capture (unverified device measurements)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("scan")
    a.add_argument("--seconds", type=int, default=10)
    b = sub.add_parser("collect")
    b.add_argument("address")
    b.add_argument("--seconds", type=int, default=60)
    b.add_argument("--vault", help="the user's vault folder")
    b.add_argument("--unencrypted-test-file", help="bench testing only: write plaintext JSONL here instead")
    args = ap.parse_args()
    if not 1 <= args.seconds <= 3600:
        ap.error("seconds must be 1..3600")
    if not available():
        print("Install the Bluetooth package once:  py -m pip install \"bleak>=0.22,<2\"")
        return 2
    if args.cmd == "scan":
        for d in asyncio.run(scan(args.seconds)):
            print(json.dumps(d))
        return 0
    if not args.vault and not args.unencrypted_test_file:
        ap.error("give --vault (encrypted) or, for bench tests only, --unencrypted-test-file")
    vault = None
    if args.vault:
        from storage import Vault
        vault = Vault.open(Path(args.vault), os.environ.get("PAI_HEALTH_PASSPHRASE") or getpass.getpass("Vault passphrase: "))
    packets = asyncio.run(collect(args.address, args.seconds,
                                  lambda p: print(json.dumps({"packet": p["packet_index"], "measurements": p["measurements"],
                                                              "error": p["parse_error"]}))))
    if vault:
        rid = vault.put("DEVICE_BATCH", batch_record(packets), meta={"source": IMPORTER})
        print(f"{len(packets)} packets saved to the encrypted vault ({rid}).")
        vault.close()
    else:
        out = Path(args.unencrypted_test_file)
        with out.open("a", encoding="utf-8") as fh:
            for p in packets:
                fh.write(json.dumps(p, sort_keys=True) + "\n")
        print(f"{len(packets)} packets written UNENCRYPTED to {out}. It contains health data — delete it after testing.")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    sys.exit(main())
