"""Run a device importer on a real export and print the import report only.

The report contains counts, measures, source names, date range, rejected
records and unmapped types — no health values — so it can be shared to
verify an importer against a real file without sharing the data.

    python engine\\import_device.py apple  path\\to\\export.zip
    python engine\\import_device.py fitbit path\\to\\takeout.zip America/New_York
    python engine\\import_device.py garmin-activities path\\to\\Activities.csv statute America/New_York
    python engine\\import_device.py csv path\\to\\file.csv path\\to\\mapping.json
"""
from __future__ import annotations

import json
import sys
import time
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from devices import apple_health, fitbit, garmin_activities, generic_csv  # noqa: E402
from devices.dedupe import drop_exact_duplicates     # noqa: E402

IMPORTERS = {"apple": apple_health, "fitbit": fitbit, "garmin-activities": garmin_activities, "csv": generic_csv}


def main(argv: list[str]) -> int:
    need = {"apple": 3, "fitbit": 4, "garmin-activities": 5, "csv": 4}
    if len(argv) < 3 or argv[1] not in IMPORTERS or len(argv) != need[argv[1]]:
        print(__doc__)
        return 2
    mod, path = IMPORTERS[argv[1]], Path(argv[2])
    t0 = time.time()
    report = mod.new_report(path)
    stream = mod.iter_export(path, report, *argv[3:])
    kept, dropped = drop_exact_duplicates(stream)
    out = asdict(report)
    out["exact_duplicates_dropped"] = dropped
    out["rejected_count"] = len(report.rejected)
    out["rejected"] = report.rejected[:25]          # first 25 reasons only
    out["seconds"] = round(time.time() - t0, 1)
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
