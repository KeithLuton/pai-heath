"""Phone companion: pairing, sync handling and the rules bundle the phone runs.

The phone runs the same fixed rules the PC does (§21.3 dose prompts with the §21.4 safety
line, the severe-reading recheck, the lane-3 emergency text) so prompts arrive on time
even when the PC is off. The PC is the record: everything the phone logs or reads from
Health Connect comes back here into the encrypted vault on the next sync.

Pairings live in the user's vault (DEVICE_PAIRING). A pairing that hasn't said hello
within 10 minutes of the QR code being shown is dead.
"""
from __future__ import annotations

import ipaddress
import json
import os
import secrets
import socket
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from tracker import library as L
from tracker import logs as LG
from tracker.logs import LogError

from . import protocol as P

PAIR_TTL = timedelta(minutes=10)
PHONE_TYPES = {"DOSE", "BP", "GLUCOSE", "WEIGHT", "SYMPTOM", "ITEM"}
PHONE_MEASURES = {"heart_rate", "resting_heart_rate", "steps", "sleep_stage", "bp_systolic", "bp_diastolic",
                  "body_mass", "glucose", "spo2", "exercise_time"}
MAX_ENTRIES, MAX_ROWS = 2000, 200_000


def _now() -> datetime:
    return datetime.now(timezone.utc)


def lan_ip() -> Optional[str]:
    """This computer's address on the local network (no packet is sent). Private ranges only."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            ip = s.getsockname()[0]
    except OSError:
        return None
    return ip if is_private_lan(ip) else None


# RFC 1918 home/office networks only. (Tests add a documentation range here; nothing else does.)
LAN_NETS = [ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")]


def is_private_lan(ip: str) -> bool:
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return a.version == 4 and any(a in n for n in LAN_NETS)


class Companion:
    def __init__(self, app):
        self.app = app

    # ---------------- pairings
    def _pairings(self) -> List[Tuple[object, dict]]:
        v = self.app.need_vault()
        return [(r, r.body) for r in v.iter("DEVICE_PAIRING")]

    def _find(self, pair_id: str):
        v = self.app.need_vault()
        r = next(v.iter("DEVICE_PAIRING", ref=v.tag("pairing", pair_id)), None)
        return (r, r.body) if r else (None, None)

    def _save(self, prior, body: dict) -> None:
        v = self.app.need_vault()
        v.put("DEVICE_PAIRING", body, ref=v.tag("pairing", body["pair_id"]), supersedes=prior.id if prior else None)

    def start_pairing(self, host: str, port: int) -> dict:
        if not is_private_lan(host):
            raise ValueError("The phone companion only works on a private home network address")
        for r, b in self._pairings():           # one pending code at a time
            if not b["confirmed"] and not b["revoked"]:
                self._save(r, dict(b, revoked=True))
        secret = secrets.token_bytes(32)
        pair_id = "PAIR-" + secrets.token_hex(6)
        body = {"pair_id": pair_id, "secret": P.b64e(secret), "created_at": _now().isoformat(timespec="seconds"),
                "expires_at": (_now() + PAIR_TTL).isoformat(timespec="seconds"), "confirmed": False,
                "revoked": False, "device": None, "last_seq": 0, "last_sync": None}
        self._save(None, body)
        self.app.need_vault().audit.append({"event": "COMPANION_PAIRING_STARTED", "pair_id": pair_id,
                                            "at": body["created_at"]})
        uri = f"paihealth://pair?v=1&h={host}&p={port}&id={pair_id}&k={P.b64e(secret)}"
        return {"pair_id": pair_id, "uri": uri, "expires_at": body["expires_at"], "qr_svg": qr_svg(uri)}

    def devices(self) -> List[dict]:
        out = []
        for _, b in self._pairings():
            if b["revoked"] or (not b["confirmed"] and datetime.fromisoformat(b["expires_at"]) < _now()):
                continue
            out.append({"pair_id": b["pair_id"], "confirmed": b["confirmed"], "device": b["device"],
                        "last_sync": b["last_sync"], "expires_at": None if b["confirmed"] else b["expires_at"]})
        return out

    def revoke(self, pair_id: str) -> None:
        r, b = self._find(pair_id)
        if not b:
            raise ValueError("No such phone")
        self._save(r, dict(b, revoked=True))
        self.app.need_vault().audit.append({"event": "COMPANION_REVOKED", "pair_id": pair_id,
                                            "at": _now().isoformat(timespec="seconds")})

    def any_confirmed(self) -> bool:
        try:
            return any(b["confirmed"] and not b["revoked"] for _, b in self._pairings())
        except Exception:
            return False

    # ---------------- requests from the phone
    def handle(self, path: str, body: bytes) -> bytes:
        """Authenticate, decrypt, act, encrypt the reply. Raises PermissionError / ProtocolError."""
        pair_id = P.envelope_id(body)
        r, b = self._find(pair_id)
        if not b or b["revoked"]:
            raise PermissionError("unknown phone")
        if not b["confirmed"] and datetime.fromisoformat(b["expires_at"]) < _now():
            raise PermissionError("pairing code expired")
        k_up, k_down = P.keys(P.b64d(b["secret"]), pair_id)
        req = P.open_(k_up, pair_id, "up", path, body)
        seq = req.get("seq")
        if not isinstance(seq, int) or seq <= b["last_seq"]:
            raise PermissionError("replayed or out-of-order request")
        now = _now().isoformat(timespec="seconds")
        device = req.get("device") if isinstance(req.get("device"), dict) else {}
        device = {k: str(device.get(k, ""))[:60] for k in ("name", "model", "app_version")}
        if path == "/v1/hello":
            reply = {"paired": True}
            if not b["confirmed"]:
                self.app.need_vault().audit.append({"event": "COMPANION_PAIRED", "pair_id": pair_id, "at": now,
                                                    "device_model": device["model"]})
        elif path == "/v1/sync":
            reply = self._sync(req)
        else:
            raise PermissionError("unknown path")
        self._save(r, dict(b, confirmed=True, device=device, last_seq=seq, last_sync=now))
        reply.update(seq=seq, server_time=now, rules=self.rules())
        return P.seal(k_down, pair_id, "down", path, reply)

    def _sync(self, req: dict) -> dict:
        v = self.app.need_vault()
        accepted, duplicates, rejected = 0, 0, []
        entries = req.get("entries") or []
        if not isinstance(entries, list) or len(entries) > MAX_ENTRIES:
            raise P.ProtocolError("too many entries")
        for item in entries:
            uid = str((item or {}).get("uid", ""))[:64]
            e = (item or {}).get("entry") or {}
            if not uid or str(e.get("type", "")).upper() not in PHONE_TYPES:
                rejected.append({"uid": uid, "reason": "not an entry the phone can log"})
                continue
            ref = v.tag("phone-entry", uid)
            if next(v.iter("USER_LOG", ref=ref), None):
                duplicates += 1
                continue
            try:
                norm = LG.normalize(e)
            except LogError as exc:
                rejected.append({"uid": uid, "reason": str(exc)})
                continue
            norm["via"] = "phone"
            v.put("USER_LOG", norm, ref=ref)
            accepted += 1
        samples = req.get("samples") or {}
        saved = {}
        if isinstance(samples, dict) and samples:
            from tracker import device_store as DS
            clean: Dict[str, List[list]] = {}
            total = 0
            for measure, rows in samples.items():
                if measure not in PHONE_MEASURES or not isinstance(rows, list):
                    continue
                for r in rows:
                    row = _row(r)
                    if row:
                        clean.setdefault(measure, []).append(row)
                        total += 1
                if total > MAX_ROWS:
                    raise P.ProtocolError("too many samples in one sync")
            if clean:
                saved, _ = DS.store_rows(v, clean, "phone-" + datetime.now().strftime("%Y%m%d"))
        self.app._scenario = None
        return {"accepted": accepted, "duplicates": duplicates, "rejected": rejected[:50], "samples_saved": saved}

    # ---------------- what the phone runs
    def rules(self) -> dict:
        """The fixed rules and current medication schedule, so the phone prompts exactly as the PC would."""
        t = self.app.tracker({}, now=None)
        meds = [{"med_id": m["id"], "name": m["name"], "dose": m["dose"], "schedule": m.get("schedule", []),
                 "start": m.get("start"),
                 "pattern": {"state": m["verification"]["state"], "delta": m.get("pattern_delta")}}
                for m in t["medications"] if m.get("origin") == "LOG" and m.get("schedule")]
        return {"version": f"{L.DOSE_RULE['id']}v{L.DOSE_RULE['version']}+{L.BP_RULE['id']}v{L.BP_RULE['version']}",
                "data_path": t["settings"].get("data_path", "REVIEW"),
                "dose_rule": L.DOSE_RULE, "safety_line": L.DOSE_SAFETY, "lane3_text": L.LANE3_TEXT,
                "urgent_symptoms": list(LG.URGENT_SYMPTOMS), "bp_severe": list(L.BP_RULE["severe"]),
                "recheck_text": "Your reading is very high. Sit quietly for 5 minutes and measure again; if it stays "
                                "this high, contact your clinician today.",
                "meds": meds}


def _row(r) -> Optional[list]:
    """A Health Connect (Android) or HealthKit (iPhone) sample as sent by the phone, checked before it touches the vault."""
    if not isinstance(r, list) or len(r) != 12:
        return None
    try:
        a, b = datetime.fromisoformat(str(r[0])), datetime.fromisoformat(str(r[1]))
        if a.tzinfo is None or b.tzinfo is None or b < a:
            return None
        val = None if r[2] is None else float(r[2])
    except (TypeError, ValueError):
        return None
    s = lambda x, n=60: None if x is None else str(x)[:n]  # noqa: E731
    app = r[5] if r[5] in ("health_connect", "healthkit") else "health_connect"     # Android or iPhone
    return [a.isoformat(), b.isoformat(), val, s(r[3], 20), s(r[4]), app, s(r[6], 30) or "OTHER_SOURCE",
            s(r[7]), s(r[8], 30), None, s(r[10]), "DEVICE_CLOCK"]


def qr_svg(text: str) -> str:
    import segno
    import io
    buf = io.BytesIO()
    segno.make(text, error="m").save(buf, kind="svg", scale=6, border=2, dark="#0B1B33")
    return buf.getvalue().decode()
