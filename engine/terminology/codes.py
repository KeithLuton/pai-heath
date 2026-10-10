"""Offline terminology lookups for canonical seed keys. No automatic inference.

BROAD mappings are candidates, never evidence of a unique LOINC identity.
Empty RxCUI values represent unmapped seeds, not code zero.
"""
from __future__ import annotations
import csv
from functools import lru_cache
from pathlib import Path

_DIR = Path(__file__).resolve().parent


def loinc_check_digit(code: str) -> bool:
    """Validate LOINC's Mod-10 (Luhn) check digit (digits before the hyphen)."""
    stem, separator, check = code.partition("-")
    if not separator or not stem.isascii() or not stem.isdigit() or len(check) != 1 or not check.isascii() or not check.isdigit():
        return False
    digits = [int(d) for d in stem + check]
    total = 0
    for offset, digit in enumerate(reversed(digits)):
        if offset % 2:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


@lru_cache(maxsize=1)
def load_loinc() -> dict[str, dict[str, str]]:
    with (_DIR / "map_loinc.csv").open(encoding="utf-8", newline="") as file:
        rows = list(csv.DictReader(file))
    result = {}
    for row in rows:
        key = row["analyte_key"]
        if not key or key in result:
            raise ValueError("Missing or duplicate LOINC seed key")
        if row["match_confidence"] not in ("EXACT", "BROAD", "NONE"):
            raise ValueError("Invalid confidence")
        if bool(row["loinc_code"]) != (row["match_confidence"] != "NONE"):
            raise ValueError("Mapping status inconsistent")
        if row["loinc_code"] and not loinc_check_digit(row["loinc_code"]):
            raise ValueError("Invalid LOINC check digit: " + row["loinc_code"])
        result[key] = row
    return result


@lru_cache(maxsize=1)
def load_rxnorm() -> dict[str, dict[str, str]]:
    with (_DIR / "map_rxnorm.csv").open(encoding="utf-8", newline="") as file:
        rows = list(csv.DictReader(file))
    result = {}
    for row in rows:
        key = row["medication_key"]
        if not key or key in result:
            raise ValueError("Missing or duplicate RxNorm seed key")
        if row["rxcui"] and (not row["rxcui"].isascii() or not row["rxcui"].isdigit() or not row["name"] or not row["source_date"]):
            raise ValueError("Invalid RxNorm ingredient mapping")
        result[key] = row
    return result


def lookup(key: str) -> dict[str, dict[str, str] | None]:
    """Return mappings for exact canonical seed key; no fuzzy matches."""
    if not isinstance(key, str):
        raise TypeError("key must be str")
    return {"loinc": load_loinc().get(key), "rxnorm": load_rxnorm().get(key)}
