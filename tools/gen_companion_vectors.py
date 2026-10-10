"""Known-answer vectors for phone companion cores (iPhone Swift core; usable by any port).

    python tools/gen_companion_vectors.py

Crypto vectors come from the PC protocol (engine/companion/protocol.py); rule vectors from
the Android Java core (android/.../core/Rules.java), which test_companion_java.py holds to
the PC's own prompts. Writes ios/CompanionCore/Tests/CompanionCoreTests/Resources/vectors.json.
Needs a JDK for the rule vectors.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "engine"))
from companion import protocol as P               # noqa: E402
from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: E402
from tracker import library as L                  # noqa: E402
from tracker import logs as LG                    # noqa: E402

OUT = ROOT / "ios" / "CompanionCore" / "Tests" / "CompanionCoreTests" / "Resources" / "vectors.json"
CORE = ROOT / "android" / "app" / "src" / "main" / "java" / "com" / "pailuton" / "health" / "core"


def crypto():
    secret = bytes(range(32))
    pair_id = "PAIR-0a1b2c3d4e5f"
    up, down = P.keys(secret, pair_id)
    nonce = bytes(range(100, 112))
    plain = b'{"paired":true,"seq":7}'
    raw = AESGCM(up).encrypt(nonce, plain, P.aad(pair_id, "up", "/v1/hello"))
    envelopes = []
    for direction, key, path, payload in [("down", down, "/v1/hello", {"paired": True, "seq": 1, "rules": {"x": [1, 2.5, None]}}),
                                          ("up", up, "/v1/sync", {"seq": 2, "entries": [], "device": {"name": "é ✓"}})]:
        envelopes.append({"direction": direction, "path": path, "body": P.seal(key, pair_id, direction, path, payload).decode(),
                          "payload": payload})
    return {"secret_b64": P.b64e(secret), "pair_id": pair_id, "k_up_hex": up.hex(), "k_down_hex": down.hex(),
            "aad_up_hello": P.aad(pair_id, "up", "/v1/hello").decode(),
            "raw": {"nonce_hex": nonce.hex(), "plaintext": plain.decode(), "ciphertext_and_tag_hex": raw.hex()},
            "envelopes": envelopes,
            "tamper": {"note": "same envelope opened with the wrong path or direction must fail",
                       "body": envelopes[0]["body"], "wrong_path": "/v1/sync", "wrong_direction": "up"}}


def pairing():
    k = P.b64e(bytes(range(32)))
    ok = lambda h, p=47110: f"paihealth://pair?v=1&h={h}&p={p}&id=PAIR-abc123&k={k}"  # noqa: E731
    return [
        {"uri": ok("192.168.1.20"), "valid": True, "host": "192.168.1.20", "port": 47110, "pair_id": "PAIR-abc123"},
        {"uri": ok("10.0.0.5", 47111), "valid": True, "host": "10.0.0.5", "port": 47111, "pair_id": "PAIR-abc123"},
        {"uri": ok("172.20.3.4"), "valid": True, "host": "172.20.3.4", "port": 47110, "pair_id": "PAIR-abc123"},
        {"uri": "  " + ok("192.168.0.2") + "\n", "valid": True, "host": "192.168.0.2", "port": 47110, "pair_id": "PAIR-abc123"},
        {"uri": ok("172.32.0.1"), "valid": False},
        {"uri": ok("8.8.8.8"), "valid": False},
        {"uri": ok("192.0.2.1"), "valid": False, "valid_in_test_net": True},
        {"uri": ok("192.168.1.20").replace("v=1", "v=2"), "valid": False},
        {"uri": ok("192.168.1.20").replace(k, P.b64e(bytes(16))), "valid": False},
        {"uri": "https://example.com/pair?v=1", "valid": False},
    ]


def rules_cases():
    bundle = {"version": "test", "data_path": "LIVE", "dose_rule": L.DOSE_RULE, "safety_line": L.DOSE_SAFETY,
              "lane3_text": L.LANE3_TEXT, "urgent_symptoms": list(LG.URGENT_SYMPTOMS),
              "bp_severe": list(L.BP_RULE["severe"]),
              "recheck_text": "Your reading is very high. Sit quietly for 5 minutes and measure again; if it stays "
                              "this high, contact your clinician today.",
              "meds": [
                  {"med_id": "MED-a", "name": "Lisinopril", "dose": "10 mg", "schedule": ["08:00", "20:00"], "start": None,
                   "pattern": {"state": "NOT_ENOUGH_DATA", "delta": None}},
                  {"med_id": "MED-b", "name": "Metoprolol", "dose": "25 mg", "schedule": ["07:30"], "start": None,
                   "pattern": {"state": "QUALIFIED", "delta": -6.0}},
                  {"med_id": "MED-c", "name": "Atorvastatin", "dose": "20 mg", "schedule": ["21:00"], "start": "2026-10-09",
                   "pattern": {"state": "NOT_ENOUGH_DATA", "delta": None}},
                  {"med_id": "MED-d", "name": "As needed", "dose": "", "schedule": [], "start": None, "pattern": {}},
              ]}
    from datetime import datetime, timedelta, timezone
    tz = timezone(timedelta(hours=-4))
    due_b = datetime(2026, 10, 10, 7, 30, tzinfo=tz)
    t = int(due_b.timestamp())
    hr_drop = [[t - 50 * 60, 72], [t - 30 * 60, 74], [t - 10 * 60, 73], [t + 40 * 60, 66], [t + 80 * 60, 65], [t + 120 * 60, 64]]
    hr_flat = [[t - 50 * 60, 72], [t - 30 * 60, 74], [t - 10 * 60, 73], [t + 40 * 60, 73], [t + 80 * 60, 72], [t + 120 * 60, 74]]
    return bundle, [
        {"name": "morning_nothing_logged", "now": "2026-10-10T11:00-04:00", "answered": [], "doses": {}, "hr": []},
        {"name": "evening_all_due", "now": "2026-10-10T23:45-04:00", "answered": [], "doses": {}, "hr": []},
        {"name": "some_answered", "now": "2026-10-10T23:45-04:00",
         "answered": ["MED-a|2026-10-09T08:00-04:00", "MED-b|2026-10-10T07:30-04:00"],
         "doses": {"MED-a@2026-10-10": 2}, "hr": []},
        {"name": "pattern_seen", "now": "2026-10-10T11:00-04:00", "answered": [], "doses": {}, "hr": hr_drop},
        {"name": "pattern_not_seen", "now": "2026-10-10T11:00-04:00", "answered": [], "doses": {}, "hr": hr_flat},
        {"name": "utc_zone", "now": "2026-10-10T12:00Z", "answered": [], "doses": {}, "hr": []},
        {"name": "east_zone_day_edge", "now": "2026-10-10T00:10+09:00", "answered": [], "doses": {}, "hr": []},
    ], [
        {"sys": 186, "dia": 100, "urgent": ["chest pain"]},
        {"sys": 186, "dia": 100, "urgent": []},
        {"sys": 150, "dia": 121, "urgent": []},
        {"sys": 179, "dia": 119, "urgent": ["confusion"]},
        {"sys": 130, "dia": 80, "urgent": ["chest pain"]},
    ]


def java_rules(bundle, dose_cases, bp_cases):
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        cases = d / "cases.json"
        cases.write_text(json.dumps({"bundle": bundle, "dose_cases": dose_cases, "bp_cases": bp_cases}))
        subprocess.run(["javac", "-d", str(d / "c"), *map(str, CORE.glob("*.java")),
                        str(ROOT / "android" / "core-test" / "VectorGen.java")], check=True)
        out = subprocess.run(["java", "-cp", str(d / "c"), "VectorGen", str(cases)], check=True,
                             capture_output=True, text=True).stdout
    return json.loads(out)


def main():
    bundle, dose_cases, bp_cases = rules_cases()
    expected = java_rules(bundle, dose_cases, bp_cases)
    vectors = {"generated_by": "tools/gen_companion_vectors.py", "crypto": crypto(), "pairing": pairing(),
               "rules": {"bundle": bundle, "dose_cases": dose_cases, "dose_expected": expected["dose"],
                         "bp": expected["bp"]}}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(vectors, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    n = sum(len(c["prompts"]) for c in expected["dose"])
    print(f"wrote {OUT.relative_to(ROOT)}: {len(dose_cases)} dose cases ({n} prompts), {len(bp_cases)} bp cases")


if __name__ == "__main__":
    main()
