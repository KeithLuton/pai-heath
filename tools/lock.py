"""Lock manager for PAi Health.

A locked file is frozen by SHA-256. Changing it fails the test suite
(engine/tests/test_locks.py) until it is re-locked with a stated reason, which
bumps its version and appends to the history. Locked test files guard behavior:
code may be improved freely as long as those tests stay green, but the tests
themselves can't be weakened without a recorded unlock.

    python tools/lock.py verify
    python tools/lock.py relock <path> --reason "why this change was approved"
    python tools/lock.py add <path> --tier LOCKED|TEST_GUARD --reason "..."
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "locks.json"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def load() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def save(m: dict) -> None:
    MANIFEST.write_text(json.dumps(m, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def verify(m: dict) -> list:
    problems = []
    for rel, entry in sorted(m["locked"].items()):
        p = ROOT / rel
        if not p.exists():
            problems.append(f"MISSING  {rel}")
        elif sha(p) != entry["sha256"]:
            problems.append(f"CHANGED  {rel} (locked v{entry['version']}; re-lock with a reason or revert)")
    return problems


def bump(v: str) -> str:
    major, minor = (v.split(".") + ["0"])[:2]
    return f"{major}.{int(minor) + 1}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["verify", "relock", "add"])
    ap.add_argument("path", nargs="?")
    ap.add_argument("--reason")
    ap.add_argument("--tier", default="LOCKED", choices=["LOCKED", "TEST_GUARD"])
    a = ap.parse_args()
    m = load()
    if a.cmd == "verify":
        problems = verify(m)
        print("\n".join(problems) if problems else f"All {len(m['locked'])} locked files intact.")
        return 1 if problems else 0
    if not a.path or not a.reason:
        print("path and --reason are required")
        return 2
    rel = Path(a.path).resolve().relative_to(ROOT).as_posix()
    today = date.today().isoformat()
    if a.cmd == "add":
        if rel in m["locked"]:
            print(f"{rel} is already locked; use relock")
            return 2
        m["locked"][rel] = {"tier": a.tier, "version": "1.0", "sha256": sha(ROOT / rel), "locked_on": today}
        m["history"].append({"date": today, "path": rel, "action": "add", "version": "1.0", "reason": a.reason})
    else:
        e = m["locked"][rel]
        e["version"] = bump(e["version"])
        e["sha256"] = sha(ROOT / rel)
        e["locked_on"] = today
        m["history"].append({"date": today, "path": rel, "action": "relock", "version": e["version"], "reason": a.reason})
    save(m)
    print(f"{a.cmd}: {rel}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
