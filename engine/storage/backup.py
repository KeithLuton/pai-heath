"""Encrypted, manifest-verified vault backup (format ported from PAi Legal backup.py).

The backup is a zip of {vault.key, health.db snapshot, audit.log} plus a
manifest of sizes and SHA-256s, encrypted whole with AES-256-GCM under a key
derived from a *backup* passphrase (scrypt). The database inside is still
encrypted under the vault key, so restoring needs both passphrases: the backup
one to unpack, the vault one to open.

Restore refuses an existing target, unsafe paths, symlinks, unmanifested
files, size/hash mismatches, and then opens and fully verifies the restored
vault before reporting success.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from .store import AUDIT_FILE, DB_FILE, KEY_FILE, Vault

MAGIC = b"PAIHBKP1"
SALT_BYTES, NONCE_BYTES, TAG_BYTES = 16, 12, 16
FILES = (KEY_FILE, DB_FILE, AUDIT_FILE)


def _hash_file(path: Path) -> str:
    d = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            d.update(block)
    return d.hexdigest()


def _key(passphrase: str, salt: bytes) -> bytes:
    if len(str(passphrase)) < 12:
        raise ValueError("Backup passphrases must contain at least 12 characters")
    return Scrypt(salt=salt, length=32, n=2 ** 15, r=8, p=1).derive(passphrase.encode("utf-8"))


def create_backup(vault: Vault, destination: Path, passphrase: str) -> Path:
    destination = Path(destination).resolve()
    try:
        destination.relative_to(vault.dir.resolve())
    except ValueError:
        pass
    else:
        raise ValueError("Write encrypted backups outside the vault directory")
    state = vault.verify()
    if not state["valid"]:
        raise RuntimeError("Vault failed verification; refusing to back up: "
                           + "; ".join((state["problems"] or state["audit"]["errors"])[:3]))
    destination.parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="pai-health-backup-"))
    tmp_out = destination.with_suffix(destination.suffix + ".tmp")
    try:
        staged = work / "vault"
        staged.mkdir()
        shutil.copy2(vault.dir / KEY_FILE, staged / KEY_FILE)
        with vault._lock:  # snapshot db and audit log at the same instant
            vault.backup_snapshot(staged / DB_FILE)
            shutil.copy2(vault.dir / AUDIT_FILE, staged / AUDIT_FILE)
        manifest = {"schema_version": 1, "format": "PAi Health encrypted vault backup",
                    "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "vault_id": vault.vault_id,
                    "files": [{"path": n, "size": (staged / n).stat().st_size,
                               "sha256": _hash_file(staged / n)} for n in FILES]}
        zip_path = work / "backup.zip"
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as z:
            for n in FILES:
                z.write(staged / n, "vault/" + n)
            z.writestr("backup_manifest.json", json.dumps(manifest, sort_keys=True, indent=2))
        salt, nonce = os.urandom(SALT_BYTES), os.urandom(NONCE_BYTES)
        enc = Cipher(algorithms.AES(_key(passphrase, salt)), modes.GCM(nonce)).encryptor()
        enc.authenticate_additional_data(MAGIC)
        with zip_path.open("rb") as src, tmp_out.open("wb") as dst:
            dst.write(MAGIC + salt + nonce)
            for block in iter(lambda: src.read(1024 * 1024), b""):
                dst.write(enc.update(block))
            dst.write(enc.finalize())
            dst.write(enc.tag)
            dst.flush()
            os.fsync(dst.fileno())
        tmp_out.replace(destination)
        vault.audit.append({"event": "BACKUP_CREATED", "at": manifest["created_at"],
                            "backup_sha256": _hash_file(destination)})
        return destination
    finally:
        shutil.rmtree(work, ignore_errors=True)
        tmp_out.unlink(missing_ok=True)


def _decrypt(source: Path, passphrase: str, zip_path: Path) -> None:
    size = source.stat().st_size
    if size < len(MAGIC) + SALT_BYTES + NONCE_BYTES + TAG_BYTES:
        raise ValueError("The backup is truncated")
    with source.open("rb") as fh:
        if fh.read(len(MAGIC)) != MAGIC:
            raise ValueError("This is not a PAi Health encrypted backup")
        salt, nonce = fh.read(SALT_BYTES), fh.read(NONCE_BYTES)
        end = size - TAG_BYTES
        fh.seek(end)
        tag = fh.read(TAG_BYTES)
        fh.seek(len(MAGIC) + SALT_BYTES + NONCE_BYTES)
        dec = Cipher(algorithms.AES(_key(passphrase, salt)), modes.GCM(nonce, tag)).decryptor()
        dec.authenticate_additional_data(MAGIC)
        with zip_path.open("wb") as out:
            remaining = end - fh.tell()
            while remaining:
                block = fh.read(min(1024 * 1024, remaining))
                if not block:
                    raise ValueError("The backup ciphertext is truncated")
                out.write(dec.update(block))
                remaining -= len(block)
            try:
                out.write(dec.finalize())
            except Exception as exc:
                raise ValueError("Backup authentication failed (wrong passphrase or altered file)") from exc


def _safe(name: str) -> bool:
    p = PurePosixPath(name)
    return not p.is_absolute() and ".." not in p.parts and "\\" not in name


def restore_backup(source: Path, target_dir: Path, backup_passphrase: str, vault_passphrase: str) -> dict:
    """Restore into a new directory. Returns the restored vault's verify() result."""
    source, target_dir = Path(source).resolve(), Path(target_dir).resolve()
    if target_dir.exists() and any(target_dir.iterdir()):
        raise FileExistsError(f"{target_dir} is not empty")
    work = Path(tempfile.mkdtemp(prefix="pai-health-restore-"))
    staging = target_dir.parent / f".{target_dir.name}.restore-{os.getpid()}"
    try:
        zip_path = work / "backup.zip"
        _decrypt(source, backup_passphrase, zip_path)
        with zipfile.ZipFile(zip_path) as z:
            members = z.infolist()
            names = [m.filename for m in members]
            if len(names) != len(set(names)):
                raise ValueError("The backup contains duplicate archive paths")
            for m in members:
                if not _safe(m.filename) or (m.external_attr >> 16) & 0o170000 == 0o120000:
                    raise ValueError("The backup contains an unsafe path or symbolic link")
            manifest = json.loads(z.read("backup_manifest.json"))
            expected = {f["path"]: f for f in manifest.get("files", [])}
            if set(expected) != set(FILES):
                raise ValueError("The backup manifest does not list exactly the vault files")
            if {n for n in names if n != "backup_manifest.json"} != {"vault/" + n for n in FILES}:
                raise ValueError("The backup contains unmanifested files")
            declared = sum(int(f["size"]) for f in expected.values())
            if declared > int(shutil.disk_usage(target_dir.parent if target_dir.parent.exists() else work).free * 0.9):
                raise ValueError("The restored vault would exceed safe available storage")
            staging.mkdir(parents=True)
            for n, f in expected.items():
                out = staging / n
                with z.open("vault/" + n) as src, out.open("wb") as dst:
                    shutil.copyfileobj(src, dst, length=1024 * 1024)
                if out.stat().st_size != int(f["size"]) or _hash_file(out) != f["sha256"]:
                    raise ValueError(f"Backup verification failed for {n}")
        with Vault.open(staging, vault_passphrase) as v:
            if v.vault_id != manifest.get("vault_id"):
                raise ValueError("Backup manifest names a different vault")
            state = v.verify()
            if not state["valid"]:
                raise ValueError("Restored vault failed verification: " + "; ".join(state["problems"][:3]))
        if target_dir.exists():
            target_dir.rmdir()
        staging.replace(target_dir)
        with Vault.open(target_dir, vault_passphrase) as v:
            v.audit.append({"event": "RESTORED_FROM_BACKUP", "at": datetime.now(timezone.utc).isoformat(
                timespec="seconds"), "backup_created_at": manifest.get("created_at")})
            return v.verify()
    finally:
        shutil.rmtree(work, ignore_errors=True)
        shutil.rmtree(staging, ignore_errors=True)
