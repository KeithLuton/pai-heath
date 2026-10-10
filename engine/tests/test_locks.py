"""Lock integrity: locked files unchanged, contract versions agree everywhere."""
import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import lock  # noqa: E402


class Locks(unittest.TestCase):
    def test_locked_files_intact(self):
        problems = lock.verify(lock.load())
        self.assertEqual(problems, [], "\n" + "\n".join(problems) +
                         "\nRe-lock deliberately: python tools/lock.py relock <path> --reason \"...\"")

    def test_contract_version_consistent(self):
        want = lock.load()["contract_version"]
        ts = (ROOT / "src/contract/source.ts").read_text(encoding="utf-8")
        server = (ROOT / "engine/health_server.py").read_text(encoding="utf-8")
        pipeline = (ROOT / "engine/records/pipeline.py").read_text(encoding="utf-8")
        self.assertIn(f"CONTRACT_VERSION = '{want}'", ts)
        self.assertIn(f'CONTRACT_VERSION = "{want}"', server)
        self.assertIn(f"MEDICAL_PRIMITIVES v{want}", pipeline)

    def test_history_records_every_lock(self):
        m = lock.load()
        for path, entry in m["locked"].items():
            versions = [h["version"] for h in m["history"] if h["path"] == path]
            self.assertIn(entry["version"], versions, f"{path} has no history entry for v{entry['version']}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
