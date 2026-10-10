"""Generate synthetic billing fixtures (invented people, providers and numbers).

    python tools/gen_billing_fixtures.py

Writes engine/tests/fixtures/billing/: column-aligned text for each paper
layout, a text-layer PDF and an OCR image of the same documents, and FHIR
financial resources. Nothing here comes from a real document.
"""
from __future__ import annotations

import json
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "engine" / "tests" / "fixtures" / "billing"


def row(*cells) -> str:
    """cells: (column, text) pairs placed at fixed character columns."""
    s = ""
    for col, text in cells:
        if not text:
            continue
        s = s.ljust(max(col, len(s) + 2) if s else col) + text
    return s.rstrip()


def eob() -> str:
    C = [0, 12, 36, 46, 58, 70, 81]
    return "\n".join([
        row((0, "ACME HEALTH PLAN"), (51, "EXPLANATION OF BENEFITS")),
        row((51, "THIS IS NOT A BILL")),
        row((0, "Member Name:  JANE Q SAMPLE"), (51, "Statement Date: 09/15/2026")),
        row((0, "Member ID:    XYZ123456789"), (51, "Group Number: 55501")),
        row((0, "Claim Number: CLM-2026-0004417")),
        row((0, "Provider:     Riverbend Family Clinic")),
        "",
        row(*zip(C, ["Date of", "Service", "Code", "Amount", "Plan", "Plan", "You"])),
        row(*zip(C, ["Service", "Description", "", "Billed", "Discount", "Paid", "Owe"])),
        row((0, "08/28/2026"), (12, "Office visit"), (36, "99213"), (46, "$210.00"), (58, "$64.00"), (70, "$116.80"), (81, "$29.20")),
        row((0, "08/28/2026"), (12, "Blood draw"), (36, "36415"), (47, "$35.00"), (58, "$22.00"), (71, "$10.40"), (81, "$2.60")),
        row((0, "08/28/2026"), (12, "Blood draw"), (36, "36415"), (47, "$35.00"), (58, "$22.00"), (71, "$10.40"), (81, "$2.60")),
        row((0, "Totals"), (46, "$280.00"), (57, "$108.00"), (70, "$137.60"), (81, "$34.40")),
        "",
        "If you disagree with this decision, you may file an appeal within 180 days of the statement date.",
    ]) + "\n"


def msn() -> str:
    C = [0, 13, 40, 52, 64, 76, 90]
    return "\n".join([
        "Medicare Summary Notice for Part B (Medical Insurance)",
        "THIS IS NOT A BILL",
        row((0, "Name: JOHN R EXAMPLE"), (52, "Notice Date: September 3, 2026")),
        row((0, "Medicare Number: 1EG4-TE5-MK72")),
        "",
        row((0, "Claim Number: 12026245012345"), (52, "Provider: Lakeside Cardiology Associates")),
        "",
        row(*zip(C, ["Service", "Service Provided &", "Amount", "Medicare-", "Amount", "Maximum You", "See"])),
        row(*zip(C, ["Date", "Billing Code", "Provider", "Approved", "Medicare", "May Be", "Notes"])),
        row((40, "Charged"), (52, "Amount"), (64, "Paid"), (76, "Billed")),
        row((0, "08/12/2026"), (13, "Echocardiogram (93306)"), (40, "$1,250.00"), (52, "$212.43"), (64, "$169.94"), (76, "$42.49"), (90, "A")),
        row((0, "08/12/2026"), (13, "Office visit (99214)"), (41, "$310.00"), (52, "$128.51"), (64, "$102.81"), (76, "$25.70")),
        row((0, "Total"), (40, "$1,560.00"), (52, "$340.94"), (64, "$272.75"), (76, "$68.19")),
        "",
        "If you disagree, you must file an appeal by March 2, 2027.",
    ]) + "\n"


def patient_bill() -> str:
    C = [0, 12, 37, 46, 57, 70, 85]
    return "\n".join([
        row((0, "Riverbend Family Clinic"), (47, "PATIENT STATEMENT")),
        row((0, "Statement Date: 09/20/2026"), (47, "Account Number: 448812")),
        row((0, "Patient: JANE Q SAMPLE"), (47, "Due Date: 10/15/2026")),
        row((0, "Guarantor: JANE Q SAMPLE")),
        "",
        row(*zip(C, ["Service", "Description", "Code", "Charges", "Insurance", "Adjustments", "Amount"])),
        row((0, "Date"), (57, "Payments")),
        row((0, "08/28/2026"), (12, "Office visit"), (37, "99213"), (47, "210.00"), (57, "116.80"), (72, "64.00"), (86, "29.20")),
        row((0, "08/28/2026"), (12, "Blood draw"), (37, "36415"), (48, "35.00"), (58, "10.40"), (72, "22.00"), (87, "2.60")),
        row((0, "09/02/2026"), (12, "Patient payment"), (85, "(10.00)")),
        "",
        "Balance Due: $21.80",
    ]) + "\n"


def ub04() -> str:
    C = [0, 13, 42, 72, 87, 103, 121]
    return "\n".join([
        "UB-04 CMS-1450",
        row((0, "1 Lakeview Regional Hospital"), (38, "3a PAT. CNTL #  LRH-2026-88213"), (75, "4 TYPE OF BILL")),
        row((38, "3b MED. REC. #  MR-0045521"), (75, "0131")),
        "6 STATEMENT COVERS PERIOD  08/14/2026 - 08/14/2026",
        "8 PATIENT NAME  SAMPLE, JANE Q",
        "",
        row(*zip(C, ["42 REV. CD.", "43 DESCRIPTION", "44 HCPCS / RATE / HIPPS CODE", "45 SERV. DATE",
                     "46 SERV. UNITS", "47 TOTAL CHARGES", "48 NON-COVERED CHARGES"])),
        row((0, "0300"), (13, "LABORATORY"), (42, "80053"), (72, "08/14/2026"), (87, "1"), (106, "145.00")),
        row((0, "0320"), (13, "DX X-RAY"), (42, "71046"), (72, "08/14/2026"), (87, "1"), (106, "310.00")),
        row((0, "0450"), (13, "EMERGENCY ROOM"), (42, "99284"), (72, "08/14/2026"), (87, "1"), (104, "1,825.00")),
        row((0, "0001"), (13, "TOTAL"), (104, "2,280.00")),
    ]) + "\n"


def cms1500() -> str:
    C = [0, 28, 50, 89, 112, 127, 146]
    return "\n".join([
        row((0, "HEALTH INSURANCE CLAIM FORM"), (70, "1500 (02-12)")),
        "1a. INSURED'S I.D. NUMBER",
        "XYZ123456789",
        row((0, "2. PATIENT'S NAME (Last Name, First Name, Middle Initial)"), (64, "4. INSURED'S NAME (Last Name, First Name, Middle Initial)")),
        row((0, "SAMPLE, JANE, Q"), (64, "SAMPLE, JANE, Q")),
        "21. DIAGNOSIS OR NATURE OF ILLNESS OR INJURY",
        "A. J45.909      B. R05.9",
        row(*zip(C, ["24. A. DATE(S) OF SERVICE", "B. PLACE OF SERVICE", "D. PROCEDURES, SERVICES, OR SUPPLIES",
                     "E. DIAGNOSIS POINTER", "F. $ CHARGES", "G. DAYS OR UNITS", "J. RENDERING PROVIDER ID. #"])),
        row(*zip(C, ["08 28 26 08 28 26", "11", "99213 25", "AB", "210 00", "1", "1234567893"])),
        row(*zip(C, ["08 28 26 08 28 26", "11", "94640", "A", "65 00", "1", "1234567893"])),
        row((0, "25. FEDERAL TAX I.D. NUMBER"), (30, "26. PATIENT'S ACCOUNT NO."), (58, "28. TOTAL CHARGE"), (77, "29. AMOUNT PAID")),
        row((0, "12-3456789"), (30, "448812"), (58, "275 00"), (77, "0 00")),
        "33. BILLING PROVIDER INFO & PH #",
        "Riverbend Family Clinic (555) 010-4477",
    ]) + "\n"


def text_pdf(text: str, path: Path, size: float = 8.0):
    from reportlab.lib.pagesizes import landscape, letter
    from reportlab.pdfgen import canvas
    c = canvas.Canvas(str(path), pagesize=landscape(letter))
    c.setFont("Courier", size)
    c.setTitle("synthetic billing fixture")
    y = landscape(letter)[1] - 40
    for line in text.splitlines():
        c.drawString(30, y, line)
        y -= size * 1.6
    c.save()


def image(text: str, path: Path):
    from PIL import Image, ImageDraw, ImageFont
    font = None
    for cand in ("DejaVuSansMono.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", "cour.ttf", "consola.ttf"):
        try:
            font = ImageFont.truetype(cand, 28)
            break
        except OSError:
            continue
    font = font or ImageFont.load_default()
    lines = text.splitlines()
    w = max(len(l) for l in lines) * 18 + 120
    img = Image.new("RGB", (w, len(lines) * 48 + 120), "white")
    d = ImageDraw.Draw(img)
    for i, l in enumerate(lines):
        d.text((60, 60 + i * 48), l, fill="black", font=font)
    img.save(path)


FHIR_CLAIM = {
    "resourceType": "Claim", "id": "clm-1", "meta": {"profile": ["http://hl7.org/fhir/StructureDefinition/Claim"]},
    "identifier": [{"system": "urn:example:clinic:claims", "value": "C-77812"}],
    "status": "active", "type": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/claim-type",
                                             "code": "professional"}]},
    "use": "claim", "patient": {"reference": "Patient/p1", "display": "Jane Q Sample"},
    "created": "2026-08-29", "provider": {"reference": "Organization/riverbend", "display": "Riverbend Family Clinic"},
    "insurer": {"display": "Acme Health Plan"},
    "diagnosis": [{"sequence": 1, "diagnosisCodeableConcept": {"coding": [{"system": "http://hl7.org/fhir/sid/icd-10-cm",
                                                                           "code": "J45.909"}]}}],
    "item": [
        {"sequence": 1, "productOrService": {"coding": [{"system": "urn:example:procedure-codes", "code": "99213"}]},
         "modifier": [{"coding": [{"system": "urn:example:modifiers", "code": "25"}]}],
         "servicedDate": "2026-08-28", "quantity": {"value": 1}, "unitPrice": {"value": 210.00, "currency": "USD"},
         "net": {"value": 210.00, "currency": "USD"}, "diagnosisSequence": [1]},
        {"sequence": 2, "productOrService": {"coding": [{"system": "urn:example:procedure-codes", "code": "36415"}]},
         "servicedDate": "2026-08-28", "quantity": {"value": 1}, "net": {"value": 35.00}},
    ],
    "total": {"value": 245.00, "currency": "USD"},
}

FHIR_EOB_BUNDLE = {
    "resourceType": "Bundle", "type": "collection",
    "entry": [
        {"resource": {
            "resourceType": "ExplanationOfBenefit", "id": "eob-1", "status": "active", "use": "claim",
            "identifier": [{"system": "urn:example:plan:eob", "value": "EOB-2026-551"}],
            "patient": {"reference": "Patient/p1"}, "created": "2026-09-15", "outcome": "complete",
            "insurer": {"display": "Acme Health Plan"}, "provider": {"display": "Riverbend Family Clinic"},
            "claim": {"reference": "Claim/clm-1"},
            "item": [{"sequence": 1,
                      "productOrService": {"coding": [{"system": "urn:example:procedure-codes", "code": "99213"}]},
                      "servicedDate": "2026-08-28", "net": {"value": 210.00, "currency": "USD"},
                      "adjudication": [
                          {"category": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/adjudication",
                                                    "code": "submitted"}]}, "amount": {"value": 210.00, "currency": "USD"}},
                          {"category": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/adjudication",
                                                    "code": "eligible"}]}, "amount": {"value": 146.00, "currency": "USD"}},
                          {"category": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/adjudication",
                                                    "code": "benefit"}]}, "amount": {"value": 116.80, "currency": "USD"}},
                          {"amount": {"value": 29.20, "currency": "USD"}}]}],
            "total": [{"category": {"coding": [{"code": "submitted"}]}, "amount": {"value": 210.00, "currency": "USD"}},
                      {"category": {"coding": [{"code": "benefit"}]}, "amount": {"value": 116.80, "currency": "USD"}}],
            "payment": {"type": {"coding": [{"code": "complete"}]}, "amount": {"value": 116.80, "currency": "USD"},
                        "date": "2026-09-16", "identifier": {"value": "EFT-88123"}}}},
        {"resource": {
            "resourceType": "ClaimResponse", "id": "cr-1", "status": "active", "use": "claim", "outcome": "complete",
            "patient": {"reference": "Patient/p1"}, "created": "2026-09-15", "request": {"reference": "Claim/clm-1"},
            "item": [{"itemSequence": 2, "adjudication": [
                {"category": {"coding": [{"code": "benefit"}]}, "amount": {"value": 10.40, "currency": "USD"}}]}]}},
        {"resource": {"resourceType": "Patient", "id": "p1"}},
    ],
}

FHIR_INVOICE = {
    "resourceType": "Invoice", "id": "inv-1", "status": "issued",
    "identifier": [{"system": "urn:example:clinic:invoices", "value": "INV-448812-09"}],
    "subject": {"reference": "Patient/p1"}, "date": "2026-09-20", "issuer": {"display": "Riverbend Family Clinic"},
    "lineItem": [{"sequence": 1, "chargeItemCodeableConcept": {"coding": [{"system": "urn:example:procedure-codes",
                                                                           "code": "99213"}]},
                  "priceComponent": [{"type": "base", "amount": {"value": 210.00, "currency": "USD"}},
                                     {"type": "discount", "amount": {"value": -64.00, "currency": "USD"}}]}],
    "totalNet": {"value": 146.00, "currency": "USD"}, "totalGross": {"value": 210.00, "currency": "USD"},
    "paymentTerms": "Due within 30 days",
}

FHIR_PAYREC = {
    "resourceType": "PaymentReconciliation", "id": "pr-1", "status": "active", "created": "2026-09-16",
    "paymentIssuer": {"display": "Acme Health Plan"}, "outcome": "complete", "paymentDate": "2026-09-16",
    "paymentAmount": {"value": 127.20, "currency": "USD"}, "paymentIdentifier": {"value": "EFT-88123"},
    "detail": [{"type": {"coding": [{"code": "payment"}]}, "request": {"reference": "Claim/clm-1"},
                "date": "2026-09-16", "amount": {"value": 127.20, "currency": "USD"}}],
}

FHIR_NOTICE = {
    "resourceType": "PaymentNotice", "id": "pn-1", "status": "active", "created": "2026-09-16",
    "request": {"reference": "Claim/clm-1"}, "payment": {"reference": "PaymentReconciliation/pr-1"},
    "paymentDate": "2026-09-16", "recipient": {"display": "Riverbend Family Clinic"},
    "amount": {"value": 127.20, "currency": "USD"},
}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    docs = {"eob": eob(), "msn": msn(), "patient_bill": patient_bill(), "ub04": ub04(), "cms1500": cms1500()}
    for name, text in docs.items():
        (OUT / f"{name}.txt").write_text(text, encoding="utf-8")
    text_pdf(docs["eob"], OUT / "eob.pdf")
    text_pdf(docs["cms1500"], OUT / "cms1500.pdf", size=6.5)
    image(docs["patient_bill"], OUT / "patient_bill.png")
    for name, obj in {"claim": FHIR_CLAIM, "eob_bundle": FHIR_EOB_BUNDLE, "invoice": FHIR_INVOICE,
                      "payment_reconciliation": FHIR_PAYREC, "payment_notice": FHIR_NOTICE}.items():
        (OUT / f"fhir_{name}.json").write_text(json.dumps(obj, indent=1), encoding="utf-8")
    print("wrote", sorted(p.name for p in OUT.iterdir()))


if __name__ == "__main__":
    main()
