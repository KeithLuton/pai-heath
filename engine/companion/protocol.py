"""Phone companion protocol v1 — shared by the PC (this file) and the phone (android/.../core/Envelope.java).

Pairing (out of band, by QR code on the PC screen):
    paihealth://pair?v=1&h=<lan ip>&p=<port>&id=<pair id>&k=<base64url 32-byte secret>

Keys (HKDF-SHA256, salt = pair id):
    k_up   = HKDF(secret, info="paihealth/companion/v1/up")     phone -> PC
    k_down = HKDF(secret, info="paihealth/companion/v1/down")   PC -> phone

Every request and response body is an envelope:
    {"v": 1, "id": <pair id>, "n": <base64url 12-byte nonce>, "c": <base64url AES-256-GCM ciphertext+tag>}
    AAD = "paihealth/v1|<pair id>|<up|down>|<path>"

So a body can't be read off the Wi-Fi, can't be altered, can't be moved to another path or
direction, and can't come from a phone that never scanned the QR code. Replays are refused by a
strictly increasing `seq` in each request; the response echoes the request's seq.
"""
from __future__ import annotations

import base64
import json
import os
from typing import Tuple

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

VERSION = 1


class ProtocolError(ValueError):
    pass


def b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def b64d(s: str) -> bytes:
    s = str(s)
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def keys(secret: bytes, pair_id: str) -> Tuple[bytes, bytes]:
    def k(info: str) -> bytes:
        return HKDF(hashes.SHA256(), 32, pair_id.encode(), info.encode()).derive(secret)
    return k("paihealth/companion/v1/up"), k("paihealth/companion/v1/down")


def aad(pair_id: str, direction: str, path: str) -> bytes:
    return f"paihealth/v{VERSION}|{pair_id}|{direction}|{path}".encode()


def seal(key: bytes, pair_id: str, direction: str, path: str, payload: dict) -> bytes:
    nonce = os.urandom(12)
    ct = AESGCM(key).encrypt(nonce, json.dumps(payload, separators=(",", ":")).encode(), aad(pair_id, direction, path))
    return json.dumps({"v": VERSION, "id": pair_id, "n": b64e(nonce), "c": b64e(ct)}).encode()


def envelope_id(body: bytes) -> str:
    try:
        env = json.loads(body)
        if env.get("v") != VERSION or not isinstance(env.get("id"), str):
            raise ValueError
        return env["id"]
    except (ValueError, TypeError, AttributeError) as exc:
        raise ProtocolError("not a companion envelope") from exc


def open_(key: bytes, pair_id: str, direction: str, path: str, body: bytes) -> dict:
    try:
        env = json.loads(body)
        if env.get("v") != VERSION or env.get("id") != pair_id:
            raise ProtocolError("wrong envelope")
        plain = AESGCM(key).decrypt(b64d(env["n"]), b64d(env["c"]), aad(pair_id, direction, path))
        out = json.loads(plain)
        if not isinstance(out, dict):
            raise ProtocolError("payload is not an object")
        return out
    except InvalidTag as exc:
        raise ProtocolError("authentication failed") from exc
    except (KeyError, ValueError, TypeError) as exc:
        raise ProtocolError("malformed envelope") from exc
