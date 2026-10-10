"""Vault keys.

One random 256-bit data key encrypts everything. It is stored only wrapped
(AES-256-GCM) under a key derived from the user's passphrase with scrypt.
Changing the passphrase rewraps the data key; no data is re-encrypted.

Sub-keys are derived from the data key with HKDF so no key is used for two
purposes:
  record  — AES-256-GCM over every stored payload
  audit   — HMAC-SHA256 for the audit chain
  index   — HMAC-SHA256 for lookup tags and content digests (dedupe without
            storing a plain hash of a health document)

Lose the passphrase and the data is gone. There is no recovery key by design;
an encrypted backup with its own passphrase is the recovery path.
"""
from __future__ import annotations

import base64
import json
import os
import secrets
import shutil
from dataclasses import dataclass
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

FORMAT = "PAi Health vault"
VERSION = 1
MIN_PASSPHRASE = 12
SCRYPT = {"n": 2 ** 15, "r": 8, "p": 1}
WRAP_AAD = b"pai-health/vault-key/v1"


class VaultLocked(Exception):
    """Wrong passphrase or altered key file."""


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def _unb64(s: str) -> bytes:
    return base64.b64decode(s.encode("ascii"), validate=True)


def _kek(passphrase: str, salt: bytes, params: dict) -> bytes:
    if len(passphrase) < MIN_PASSPHRASE:
        raise ValueError(f"Passphrase must be at least {MIN_PASSPHRASE} characters")
    return Scrypt(salt=salt, length=32, n=int(params["n"]), r=int(params["r"]),
                  p=int(params["p"])).derive(passphrase.encode("utf-8"))


def _sub(data_key: bytes, purpose: str) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None,
                info=f"pai-health/{purpose}/v1".encode()).derive(data_key)


@dataclass(frozen=True)
class VaultKeys:
    vault_id: str
    record: bytes
    audit: bytes
    index: bytes


def _write_atomic(path: Path, obj: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as fh:
        json.dump(obj, fh, indent=2, sort_keys=True)
        fh.flush()
        os.fsync(fh.fileno())
    tmp.replace(path)


def _wrap(data_key: bytes, passphrase: str, vault_id: str) -> dict:
    salt, nonce = os.urandom(16), os.urandom(12)
    ct = AESGCM(_kek(passphrase, salt, SCRYPT)).encrypt(nonce, data_key, WRAP_AAD + vault_id.encode())
    return {"format": FORMAT, "version": VERSION, "vault_id": vault_id,
            "kdf": {"name": "scrypt", **SCRYPT, "salt": _b64(salt)},
            "wrap": {"alg": "AES-256-GCM", "nonce": _b64(nonce), "ciphertext": _b64(ct)}}


def _derive(vault_id: str, data_key: bytes) -> VaultKeys:
    return VaultKeys(vault_id, _sub(data_key, "record"), _sub(data_key, "audit"), _sub(data_key, "index"))


def create(key_file: Path, passphrase: str) -> VaultKeys:
    key_file = Path(key_file)
    if key_file.exists():
        raise FileExistsError(f"A vault key already exists at {key_file}")
    vault_id = "VAULT-" + secrets.token_hex(8)
    data_key = AESGCM.generate_key(bit_length=256)
    key_file.parent.mkdir(parents=True, exist_ok=True)
    _write_atomic(key_file, _wrap(data_key, passphrase, vault_id))
    return _derive(vault_id, data_key)


def _unwrap(key_file: Path, passphrase: str) -> tuple[str, bytes]:
    try:
        meta = json.loads(Path(key_file).read_text(encoding="utf-8"))
        if meta.get("format") != FORMAT or meta.get("version") != VERSION:
            raise VaultLocked("Not a PAi Health vault key file, or an unsupported version")
        kdf, wrap = meta["kdf"], meta["wrap"]
        if kdf.get("name") != "scrypt" or int(kdf["n"]) < SCRYPT["n"]:
            raise VaultLocked("Key file declares a weaker KDF than this build accepts")
        vault_id = str(meta["vault_id"])
        kek = _kek(passphrase, _unb64(kdf["salt"]), kdf)
        data_key = AESGCM(kek).decrypt(_unb64(wrap["nonce"]), _unb64(wrap["ciphertext"]),
                                       WRAP_AAD + vault_id.encode())
    except InvalidTag as exc:
        raise VaultLocked("Wrong passphrase, or the key file was altered") from exc
    except (KeyError, ValueError, TypeError) as exc:
        if isinstance(exc, ValueError) and "Passphrase must" in str(exc):
            raise
        raise VaultLocked("Key file is damaged") from exc
    return vault_id, data_key


def unlock(key_file: Path, passphrase: str) -> VaultKeys:
    return _derive(*_unwrap(key_file, passphrase))


def change_passphrase(key_file: Path, old: str, new: str) -> None:
    """Rewrap only the data key; never modify encrypted record data.

    Until the atomic replace commits, vault.key remains usable with `old`.
    After replace, it is usable with `new`. An interrupted cleanup may
    leave vault.key.prev, which must be removed before further key changes.
    """
    key_file = Path(key_file)
    vault_id, data_key = _unwrap(key_file, old)
    wrapped = _wrap(data_key, new, vault_id)  # validates minimum length before touching disk
    previous = key_file.with_name(key_file.name + ".prev")
    staged = key_file.with_name(key_file.name + ".tmp")
    if previous.exists():
        # Left by an earlier attempt that failed before the swap: identical to the active key, so safe to drop.
        if previous.read_bytes() == key_file.read_bytes():
            previous.unlink()
        else:
            raise RuntimeError("Unresolved previous key file; inspect vault.key.prev before retrying")
    # Backup old metadata without moving/removing the active key. Do not put
    # secrets in a plaintext log. The wrapped key file is the only copied data.
    try:
        with key_file.open("rb") as source, previous.open("xb") as dest:
            shutil.copyfileobj(source, dest)
            dest.flush()
            os.fsync(dest.fileno())
        with staged.open("x", encoding="utf-8", newline="\n") as dest:
            json.dump(wrapped, dest, indent=2, sort_keys=True)
            dest.flush()
            os.fsync(dest.fileno())
        os.replace(staged, key_file)
    except BaseException:
        staged.unlink(missing_ok=True)
        # The active key stays untouched if os.replace fails.
        # Preserve .prev for manual recovery from any ambiguous filesystem error.
        raise
    previous.unlink(missing_ok=True)

