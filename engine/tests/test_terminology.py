"""Offline map integrity checks."""
import csv
import unittest
from pathlib import Path
from terminology.codes import load_loinc, load_rxnorm, lookup, loinc_check_digit
from records.lexicon import ANALYTES, DRUGS


class TerminologyTests(unittest.TestCase):
    def test_loinc_rows_and_coverage(self):
        rows = load_loinc()
        self.assertEqual(set(rows), set(ANALYTES))
        self.assertTrue(any(r["match_confidence"] == "BROAD" for r in rows.values()))
        for row in rows.values():
            if row["loinc_code"]:
                self.assertTrue(loinc_check_digit(row["loinc_code"]))
                self.assertTrue(all(row[c] for c in ("loinc_long_name", "component", "property", "system", "scale", "typical_ucum_unit")))
            else:
                self.assertEqual(row["match_confidence"], "NONE")
                self.assertTrue(row["note"])

    def test_check_digit(self):
        for code in ("2345-7", "4548-4", "2160-0", "2823-3"):
            self.assertTrue(loinc_check_digit(code))
        for code in ("2345-8", "4548-3", "garbage", "2345"):
            self.assertFalse(loinc_check_digit(code))

    def test_rxnorm_rows_and_coverage(self):
        rows = load_rxnorm()
        self.assertEqual(set(rows), set(DRUGS))
        for row in rows.values():
            if row["rxcui"]:
                self.assertTrue(row["rxcui"].isdigit())
                self.assertTrue(row["name"])
                self.assertTrue(row["source_date"])

    def test_offline_lookup(self):
        self.assertEqual(lookup("glucose")["loinc"]["loinc_code"], "2345-7")
        self.assertEqual(lookup("metformin")["rxnorm"]["rxcui"], "")
        self.assertIsNone(lookup("not in lexicon")["loinc"])
        self.assertIsNone(lookup("not in lexicon")["rxnorm"])
