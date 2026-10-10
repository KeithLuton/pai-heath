"""Bluetooth SIG GATT characteristic decoders (BLE health profiles).

Merged from the bluetooth test build, with these corrections:
  * SFLOAT/FLOAT special values per IEEE 11073-20601: NaN, NRes, +INF, -INF and
    the reserved value all decode to None (the test build missed -INF and reserved).
  * Weight 0xFFFF means "measurement unsuccessful" — no value (it decoded as 327.675 kg).
  * Optional fields are parsed in flag order: the device's own time stamp
    (stored readings from a cuff or scale are sent later — the capture time is
    not the measurement time), BP pulse rate, sensor-contact status.
  * Heart rate with sensor contact supported but not detected is flagged.

Decoders return what the packet says and nothing more. Units are as sent;
normalization to the app's units happens in collector.to_samples and is logged.
"""
from __future__ import annotations

import math
from datetime import datetime
from typing import List, Optional

BASE = "-0000-1000-8000-00805f9b34fb"
CHARACTERISTICS = {
    "00002a37" + BASE: "heart_rate",
    "00002a35" + BASE: "blood_pressure",
    "00002a1c" + BASE: "temperature",
    "00002a9d" + BASE: "weight",
    "00002a18" + BASE: "glucose",
    "00002a5e" + BASE: "pulse_ox_spot",
}
SERVICES = ["180d", "1810", "1809", "181d", "1808", "1822"]

_SFLOAT_SPECIAL = {0x07FF, 0x0800, 0x07FE, 0x0802, 0x0801}            # NaN, NRes, +INF, -INF, reserved
_FLOAT_SPECIAL = {0x7FFFFF, 0x800000, 0x7FFFFE, 0x800002, 0x800001}


def sfloat(raw: int) -> Optional[float]:
    m = raw & 0x0FFF
    if m in _SFLOAT_SPECIAL:
        return None
    if m >= 0x800:
        m -= 0x1000
    e = (raw >> 12) & 0x0F
    if e >= 8:
        e -= 16
    return round(m * 10 ** e, 6)


def float11073(raw: int) -> Optional[float]:
    m = raw & 0xFFFFFF
    if m in _FLOAT_SPECIAL:
        return None
    if m >= 0x800000:
        m -= 0x1000000
    e = (raw >> 24) & 0xFF
    if e >= 128:
        e -= 256
    v = m * 10 ** e
    return round(v, 6) if math.isfinite(v) else None


def date_time(b: bytes) -> Optional[datetime]:
    """GATT Date Time (7 bytes). Naive: the device clock carries no time zone. Zero fields = unknown."""
    year = int.from_bytes(b[0:2], "little")
    month, day, hh, mm, ss = b[2], b[3], b[4], b[5], b[6]
    if not year or not month or not day:
        return None
    try:
        return datetime(year, month, day, hh, mm, ss)
    except ValueError:
        return None


class Reader:
    def __init__(self, kind: str, b: bytes):
        self.kind, self.b, self.i = kind, b, 0

    def take(self, n: int) -> bytes:
        if self.i + n > len(self.b):
            raise ValueError(f"Truncated {self.kind} notification")
        out = self.b[self.i:self.i + n]
        self.i += n
        return out

    def u8(self) -> int:
        return self.take(1)[0]

    def u16(self) -> int:
        return int.from_bytes(self.take(2), "little")


def _row(measure, value, unit, device_time=None, **meta):
    r = {"measure": measure, "value": value, "unit": unit}
    if device_time:
        r["device_time"] = device_time.isoformat()
    if meta:
        r["meta"] = meta
    return r


def decode(uuid: str, payload: bytes) -> List[dict]:
    """Measured values only, no conclusions. ValueError for unsupported or malformed frames."""
    kind = CHARACTERISTICS.get(str(uuid).lower())
    if not kind:
        raise ValueError("Unsupported characteristic")
    r = Reader(kind, bytes(payload))
    flags = r.u8()

    if kind == "heart_rate":
        hr = r.u16() if flags & 0x01 else r.u8()
        contact_supported, contact = bool(flags & 0x04), bool(flags & 0x02)
        meta = {"sensor_contact": "NOT_DETECTED"} if contact_supported and not contact else {}
        return [_row("heart_rate", float(hr), "bpm", **meta)]

    if kind == "blood_pressure":
        unit = "kPa" if flags & 0x01 else "mmHg"
        sys_, dia, mean = (sfloat(r.u16()) for _ in range(3))
        t = date_time(r.take(7)) if flags & 0x02 else None
        pulse = sfloat(r.u16()) if flags & 0x04 else None
        rows = [_row(m, v, unit, t) for m, v in (("bp_systolic", sys_), ("bp_diastolic", dia), ("bp_mean", mean))
                if v is not None]
        if pulse is not None:
            rows.append(_row("heart_rate", pulse, "bpm", t, method="BP_CUFF"))
        return rows

    if kind == "temperature":
        v = float11073(int.from_bytes(r.take(4), "little"))
        t = date_time(r.take(7)) if flags & 0x02 else None
        return [] if v is None else [_row("body_temperature", v, "degF" if flags & 0x01 else "degC", t)]

    if kind == "weight":
        raw = r.u16()
        t = date_time(r.take(7)) if flags & 0x02 else None
        if raw == 0xFFFF:
            return []                                   # "measurement unsuccessful"
        return [_row("body_mass", round(raw * (0.01 if flags & 0x01 else 0.005), 3),
                     "lb" if flags & 0x01 else "kg", t)]

    if kind == "pulse_ox_spot":
        spo2, pr = sfloat(r.u16()), sfloat(r.u16())
        t = date_time(r.take(7)) if flags & 0x01 else None
        rows = []
        if spo2 is not None:
            rows.append(_row("spo2", spo2, "%", t))
        if pr is not None:
            rows.append(_row("heart_rate", pr, "bpm", t, method="PULSE_OXIMETER"))
        return rows

    # glucose: units, sample type and record sequencing need the Record Access Control
    # Point and per-meter validation. The raw packet is kept; no value is emitted.
    r.take(9)
    return []
