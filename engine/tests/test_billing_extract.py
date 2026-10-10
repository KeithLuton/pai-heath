"""Billing extraction: paper layouts (text, PDF word positions, OCR) and FHIR financial resources.

The rules under test come from ingest/billing_profiles.py: every value has a
locator that reproduces it exactly; money columns map only by explicit labels;
nothing is derived by arithmetic; repeated rows stay; conflicts stay unknown;
a paper profile is read only after a person confirms it. All fixtures are
synthetic (tools/gen_billing_fixtures.py).
"""
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import billing                                   # noqa: E402
from billing import fhir as FH                   # noqa: E402
from billing import values as V                  # noqa: E402
from ingest import ingest_bytes                  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures" / "billing"
PW = "a long test passphrase"


def load(name, data=None):
    data = data if data is not None else (FIX / name).read_bytes()
    docs, fails = ingest_bytes(data, name)
    assert docs, fails
    return docs[0], data


def read(name, profile=None, data=None):
    doc, data = load(name, data)
    return doc, data, billing.extract(doc, data, {"profile_id": profile, "by": "Test"} if profile else None)


def all_fields(rec):
    out = list(rec["header"])
    for t in rec["tables"]:
        for r in t["rows"] + ([t["totals"]] if t.get("totals") else []):
            out += r["fields"]
    return out


def rows(rec, table=0):
    return rec["tables"][table]["rows"]


def val(row, field):
    got = [f for f in row["fields"] if f["field"] == field]
    return got[0]["normalized_value"] if got else None


def head(rec, field):
    return [f for f in rec["header"] if f["field"] == field]


def tesseract():
    try:
        from ingest.formats_pdf_image import tesseract_available
        return tesseract_available()
    except Exception:
        return False


class TestValues(unittest.TestCase):
    def test_money(self):
        self.assertEqual(V.money("$1,250.00")[0], "1250.00")
        self.assertEqual(V.money("(10.00)")[0], "-10.00")
        self.assertEqual(V.money("(10.00)")[2]["sign_basis"], "parentheses")
        self.assertEqual(V.money("12.50CR")[0], "-12.50")
        self.assertEqual(V.money("-$5.00")[0], "-5.00")
        self.assertEqual(V.money("125 00")[:2], ("125.00", ["CENTS_IN_SEPARATE_BOX"]))
        self.assertIsNone(V.money("1.250,00")[0])          # not en-US: never re-read under another locale
        self.assertIsNone(V.money("(10.00")[0])
        self.assertIsNone(V.money("-(5.00)")[0])

    def test_dates(self):
        self.assertEqual(V.date_or_range("08/28/2026")[0], "2026-08-28")
        self.assertEqual(V.date_or_range("08 28 26 08 28 26")[0], "2026-08-28/2026-08-28")
        self.assertIn("TWO_DIGIT_YEAR", V.date_or_range("08/28/26")[1])
        self.assertEqual(V.date_or_range("September 3, 2026")[0], "2026-09-03")
        self.assertEqual(V.date_or_range("08/14/2026 - 08/16/2026")[0], "2026-08-14/2026-08-16")
        self.assertIsNone(V.date_or_range("02/30/2026")[0])
        self.assertIn("INVALID_DATE", V.date_or_range("02/30/2026")[1])


class TestClassify(unittest.TestCase):
    def test_markers_are_hints(self):
        for name, pid in [("eob.txt", "EOB"), ("msn.txt", "MSN"), ("patient_bill.txt", "PATIENT_BILL"),
                          ("ub04.txt", "CMS1450"), ("cms1500.txt", "CMS1500")]:
            doc, data = load(name)
            c = billing.classify(doc, data)
            self.assertEqual([x["profile_id"] for x in c["candidates"]], [pid], name)
            self.assertTrue(c["candidates"][0]["supported"])

    def test_paper_needs_confirmation(self):
        _, _, out = read("eob.txt")
        self.assertEqual(out["records"], [])

    def test_fhir_is_structural(self):
        doc, data = load("fhir_eob_bundle.json")
        c = billing.classify(doc, data)
        self.assertEqual([x["profile_id"] for x in c["fhir"]], ["FHIR_ExplanationOfBenefit", "FHIR_ClaimResponse"])
        self.assertEqual(c["candidates"], [])


class TestProvenance(unittest.TestCase):
    """Every value can be found again exactly where its locator says."""

    CASES = [("eob.txt", "EOB"), ("msn.txt", "MSN"), ("patient_bill.txt", "PATIENT_BILL"), ("ub04.txt", "CMS1450"),
             ("cms1500.txt", "CMS1500"), ("eob.pdf", "EOB"), ("cms1500.pdf", "CMS1500")]

    def test_text_offsets_reproduce_raw(self):
        for name, pid in self.CASES:
            doc, data, out = read(name, pid)
            fields = all_fields(out["records"][0])
            self.assertGreater(len(fields), 10, name)
            for f in fields:
                loc = f["locator"]
                self.assertEqual(f["source_id"], doc.doc_id)
                self.assertIn("page", loc)
                if "text_start" in loc:
                    self.assertEqual(doc.canonical_text[loc["text_start"]:loc["text_end"]], f["raw_value"], (name, f))
                else:
                    self.assertIn("bbox", loc, (name, f))

    def test_pdf_bbox_holds_the_words(self):
        import pdfplumber
        for name, pid in [("eob.pdf", "EOB"), ("cms1500.pdf", "CMS1500")]:
            doc, data, out = read(name, pid)
            with pdfplumber.open(io.BytesIO(data)) as pdf:
                words = pdf.pages[0].extract_words()
            for f in all_fields(out["records"][0]):
                x0, top, x1, bottom = f["locator"]["bbox"]
                inside = " ".join(w["text"] for w in words if w["x0"] >= x0 - 0.2 and w["x1"] <= x1 + 0.2
                                  and w["top"] >= top - 0.2 and w["bottom"] <= bottom + 0.2)
                self.assertIn(f["raw_value"], inside, (name, f["field"]))

    def test_json_pointer_reproduces_raw(self):
        for name in ("fhir_claim.json", "fhir_eob_bundle.json", "fhir_invoice.json",
                     "fhir_payment_reconciliation.json", "fhir_payment_notice.json"):
            doc, data, out = read(name)
            parsed = FH.load(data)
            self.assertTrue(out["records"], name)
            for rec in out["records"]:
                for f in all_fields(rec):
                    self.assertEqual(str(FH.resolve(parsed, f["locator"]["json_pointer"])), f["raw_value"], (name, f))


class TestEOB(unittest.TestCase):
    def setUp(self):
        _, _, out = read("eob.txt", "EOB")
        self.rec = out["records"][0]

    def test_lines_and_labels(self):
        r = rows(self.rec)
        self.assertEqual(len(r), 3)
        self.assertEqual([val(x, "billed") for x in r], ["210.00", "35.00", "35.00"])
        self.assertEqual(val(r[0], "paid"), "116.80")
        self.assertEqual(val(r[0], "patient_amount"), "29.20")
        self.assertEqual(val(r[0], "adjustments"), "64.00")
        self.assertEqual(head(self.rec, "claim_id")[0]["raw_value"], "CLM-2026-0004417")
        self.assertEqual(head(self.rec, "patient")[0]["raw_value"], "JANE Q SAMPLE")

    def test_assertion_kinds(self):
        r = rows(self.rec)[0]
        kinds = {f["field"]: f["assertion_kind"] for f in r["fields"]}
        self.assertEqual(kinds["billed"], "adjudicated")
        self.assertEqual(kinds["paid"], "paid-as-reported")

    def test_repeated_rows_kept_and_flagged(self):
        r = rows(self.rec)
        self.assertIn("REPEATED_ROW", r[1]["issues"])
        self.assertIn("REPEATED_ROW", r[2]["issues"])
        self.assertNotIn("REPEATED_ROW", r[0]["issues"])
        self.assertTrue(any(i["code"] == "REPEATED_ROW" for i in self.rec["issues"]))

    def test_nothing_derived(self):
        names = {f["field"] for f in all_fields(self.rec)}
        self.assertNotIn("allowed", names)                  # no allowed column: never computed
        self.assertIn("allowed", self.rec["not_found"])
        self.assertFalse(any(i["code"] == "TOTAL_DIFFERS_FROM_LINES" for i in self.rec["issues"]))

    def test_total_mismatch_flagged_not_fixed(self):
        text = (FIX / "eob.txt").read_text().replace("$280.00", "$290.00")
        _, _, out = read("eob.txt", "EOB", text.encode())
        rec = out["records"][0]
        tot = [f for f in rec["tables"][0]["totals"]["fields"] if f["field"] == "billed"][0]
        self.assertEqual(tot["raw_value"], "$290.00")
        self.assertEqual(tot["normalized_value"], "290.00")
        self.assertIn("TOTAL_DIFFERS_FROM_LINES", tot["issues"])

    def test_conflict_stays_unknown(self):
        text = (FIX / "eob.txt").read_text() + "\nClaim Number: CLM-2026-9999999\n"
        _, _, out = read("eob.txt", "EOB", text.encode())
        cl = head(out["records"][0], "claim_id")
        self.assertEqual(len(cl), 2)
        self.assertTrue(all(f["normalized_value"] is None and "SOURCE_CONFLICT" in f["issues"] for f in cl))
        self.assertEqual({f["raw_value"] for f in cl}, {"CLM-2026-0004417", "CLM-2026-9999999"})

    def test_pdf_matches_text(self):
        _, _, out = read("eob.pdf", "EOB")
        pdf = out["records"][0]
        self.assertIn("word positions from the PDF text layer", " ".join(out["read_notes"]))
        for a, b in zip(rows(self.rec), rows(pdf)):
            self.assertEqual([(f["field"], f["normalized_value"]) for f in a["fields"]],
                             [(f["field"], f["normalized_value"]) for f in b["fields"]])


class TestOtherLayouts(unittest.TestCase):
    def test_msn_stacked_headers(self):
        _, _, out = read("msn.txt", "MSN")
        rec = out["records"][0]
        self.assertEqual([c["field"] for c in rec["tables"][0]["columns"]],
                         ["service_date", "description", "billed", "approved", "medicare_paid",
                          "maximum_patient_amount", "remarks"])
        self.assertEqual(val(rows(rec)[0], "billed"), "1250.00")
        self.assertEqual(head(rec, "appeal_deadline")[0]["normalized_value"], "2027-03-02")
        self.assertEqual(rows(rec)[0]["fields"][1]["raw_value"], "Echocardiogram (93306)")   # code not split out or expanded

    def test_patient_bill_ambiguous_amount_never_mapped(self):
        _, _, out = read("patient_bill.txt", "PATIENT_BILL")
        rec = out["records"][0]
        cols = {c["label"]: c["field"] for c in rec["tables"][0]["columns"]}
        self.assertEqual(cols["Amount"], "amount_unqualified")
        for r in rows(rec):
            amt = [f for f in r["fields"] if f["field"] == "amount_unqualified"]
            self.assertTrue(amt and "AMBIGUOUS_COLUMN_LABEL" in amt[0]["issues"])
            self.assertFalse({"balance", "paid", "patient_amount"} & {f["field"] for f in r["fields"]})
        self.assertEqual(val(rows(rec)[2], "amount_unqualified"), "-10.00")
        self.assertEqual(head(rec, "balance")[0]["normalized_value"], "21.80")

    def test_ub04_printed_total_and_opaque_codes(self):
        _, _, out = read("ub04.txt", "CMS1450")
        rec = out["records"][0]
        self.assertEqual(len(rows(rec)), 3)
        self.assertEqual([val(r, "revenue_code") for r in rows(rec)], ["0300", "0320", "0450"])
        self.assertEqual([f["normalized_value"] for f in rec["tables"][0]["totals"]["fields"] if f["field"] == "billed"],
                         ["2280.00"])
        self.assertEqual(head(rec, "statement_period")[0]["normalized_value"], "2026-08-14/2026-08-14")
        self.assertEqual(head(rec, "bill_type")[0]["raw_value"], "0131")
        self.assertFalse(rec["issues"])

    def test_cms1500_boxes(self):
        _, _, out = read("cms1500.txt", "CMS1500")
        rec = out["records"][0]
        r = rows(rec)
        self.assertEqual(len(r), 2)
        self.assertEqual(val(r[0], "service_code"), "99213")
        mod = [f for f in r[0]["fields"] if f["field"] == "modifier"][0]
        self.assertEqual(mod["raw_value"], "25")
        self.assertEqual(val(r[0], "billed"), "210.00")
        self.assertEqual([f["raw_value"] for f in head(rec, "diagnosis")], ["J45.909", "R05.9"])
        self.assertEqual(head(rec, "total")[0]["normalized_value"], "275.00")
        self.assertEqual(head(rec, "amount_paid")[0]["assertion_kind"], "paid-as-reported")
        self.assertEqual(head(rec, "patient")[0]["raw_value"], "SAMPLE, JANE, Q")
        self.assertFalse([i for i in rec["issues"] if i["code"] != "LABEL_WITHOUT_VALUE"])

    def test_missing_label_stays_unknown(self):
        text = (FIX / "cms1500.txt").read_text().replace("28. TOTAL CHARGE", "28.             ")
        _, _, out = read("cms1500.txt", "CMS1500", text.encode())
        rec = out["records"][0]
        self.assertFalse(head(rec, "total"))
        self.assertTrue(any(i["code"] == "MISSING_FIELD" and i["field"] == "total" for i in rec["issues"]))

    def test_no_labeled_table(self):
        _, _, out = read("x.txt", "EOB", b"EXPLANATION OF BENEFITS\nClaim Number: A-1234\nSome prose about a visit.\n")
        rec = out["records"][0]
        self.assertTrue(any(i["code"] == "NO_LINE_TABLE_FOUND" for i in rec["issues"]))
        self.assertEqual(rec["tables"], [])

    @unittest.skipUnless(tesseract(), "Tesseract not installed")
    def test_ocr_image(self):
        _, _, out = read("patient_bill.png", "PATIENT_BILL")
        rec = out["records"][0]
        self.assertIn("OCR", " ".join(out["read_notes"]))
        r = rows(rec)
        self.assertEqual([val(x, "billed") for x in r[:2]], ["210.00", "35.00"])
        for f in all_fields(rec):
            self.assertEqual(f["locator"]["bbox_unit"], "px")
            self.assertIn("ocr_word_confidence_min", f["locator"])


class TestFHIR(unittest.TestCase):
    def test_claim(self):
        _, _, out = read("fhir_claim.json")
        rec = out["records"][0]
        self.assertEqual(rec["confirmation"]["basis"], "STRUCTURAL")
        self.assertEqual(rec["assertion_kind"], "submitted")
        it = rows(rec)
        self.assertEqual([val(r, "billed") for r in it], ["210.0", "35.0"])
        self.assertEqual(val(it[0], "modifier"), "25")
        self.assertIn("CURRENCY_NOT_STATED", [f for f in it[1]["fields"] if f["field"] == "billed"][0]["issues"])
        rid = head(rec, "resource_id")[0]
        ident = head(rec, "identifier")[0]
        self.assertNotEqual(rid["raw_value"], ident["raw_value"])           # business id kept apart from resource id

    def test_eob_bundle(self):
        _, _, out = read("fhir_eob_bundle.json")
        eob, cr = out["records"]
        self.assertEqual(eob["resource_type"], "ExplanationOfBenefit")
        amounts = [f for f in rows(eob)[0]["fields"] if f["field"] == "adjudication_amount"]
        self.assertEqual([(f["label"], f["normalized_value"]) for f in amounts],
                         [("submitted", "210.0"), ("eligible", "146.0"), ("benefit", "116.8"), (None, "29.2")])
        self.assertIn("CATEGORY_MISSING", amounts[3]["issues"])              # never guessed from position
        paid = head(eob, "paid")[0]
        self.assertEqual(paid["assertion_kind"], "paid-as-reported")
        self.assertTrue(any(i["code"] == "FHIR_VERSION_UNDECLARED" for i in eob["issues"]))
        self.assertEqual(cr["assertion_kind"], "adjudicated")

    def test_number_text_preserved(self):
        data = b'{"resourceType":"Invoice","id":"i","status":"issued","totalNet":{"value":146.00,"currency":"USD"}}'
        _, _, out = read("inv.json", data=data)
        f = head(out["records"][0], "total_net")[0]
        self.assertEqual((f["raw_value"], f["normalized_value"], f["currency"]), ("146.00", "146.00", "USD"))

    def test_claim_use(self):
        base = json.loads((FIX / "fhir_claim.json").read_text())
        base["use"] = "predetermination"
        _, _, out = read("c.json", data=json.dumps(base).encode())
        self.assertEqual(out["records"][0]["assertion_kind"], "estimated")
        base["use"] = "preauthorization"
        _, _, out = read("c.json", data=json.dumps(base).encode())
        self.assertTrue(any(i["code"] == "USE_PREAUTHORIZATION" for i in out["records"][0]["issues"]))

    def test_r5_payment_reconciliation(self):
        r5 = {"resourceType": "PaymentReconciliation", "id": "x", "status": "active", "date": "2026-09-16",
              "amount": {"value": 50, "currency": "USD"}, "allocation": [{"amount": {"value": 50, "currency": "USD"}}]}
        _, _, out = read("r5.json", data=json.dumps(r5).encode())
        rec = out["records"][0]
        self.assertTrue(any(i["code"] == "R5_ELEMENT_NAMES" for i in rec["issues"]))
        self.assertEqual(head(rec, "paid")[0]["locator"]["json_pointer"], "/amount/value")
        self.assertEqual(val(rows(rec)[0], "paid"), "50")

    def test_non_financial_fhir_ignored(self):
        _, _, out = read("p.json", data=b'{"resourceType":"Patient","id":"p"}')
        self.assertEqual(out["records"], [])


class TestAppBilling(unittest.TestCase):
    def setUp(self):
        from app_state import App
        self.tmp = Path(tempfile.mkdtemp(prefix="pai-bill-test-"))
        os.environ["PAI_HEALTH_HOME"] = str(self.tmp)
        self.app = App(root=self.tmp)
        self.app.create_user("Test Person", PW)

    def tearDown(self):
        self.app.lock_user()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_confirm_flow(self):
        from app_state import AppError
        res = self.app.upload("eob.txt", (FIX / "eob.txt").read_bytes())
        did = res["documents"][0]["doc_id"]
        row = [d for d in self.app.documents() if d["doc_id"] == did][0]
        self.assertEqual(row["billing"], {"profile_id": None, "candidates": ["EOB"]})
        doc = self.app.document(did)
        self.assertIsNone(doc["billing"]["extraction"])
        self.assertTrue(doc["floats"])                                          # not yet known to be billing
        with self.assertRaises(AppError):
            self.app.billing_confirm(did, "GFE")                               # known profile, no reader yet
        with self.assertRaises(AppError):
            self.app.billing_confirm(did, "NOPE")
        doc = self.app.billing_confirm(did, "EOB")
        rec = doc["billing"]["extraction"]["records"][0]
        self.assertEqual(rec["confirmation"]["by"], "Test Person")
        self.assertEqual(len(rec["tables"][0]["rows"]), 3)
        self.assertEqual(doc["floats"], [])                                     # billing is not a clinical record
        self.assertNotIn(did, {f["provenance"]["document_id"] for f in self.app.scenario()["expected"]["floats"]})
        doc = self.app.billing_confirm(did, None)                               # undo: superseded, not deleted
        self.assertIsNone(doc["billing"]["confirmed"])
        self.assertEqual(len(list(self.app.vault.iter("BILLING_PROFILE", current_only=False))), 2)

    def test_fhir_reads_without_confirmation(self):
        from app_state import AppError
        res = self.app.upload("claim.json", (FIX / "fhir_claim.json").read_bytes())
        did = res["documents"][0]["doc_id"]
        doc = self.app.document(did)
        self.assertEqual(doc["billing"]["extraction"]["records"][0]["profile_id"], "FHIR_Claim")
        with self.assertRaises(AppError):
            self.app.billing_confirm(did, "EOB")


if __name__ == "__main__":
    unittest.main()
