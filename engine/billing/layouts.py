"""Printed-label vocabularies for the paper billing layouts.

Labels locate values; they are never used to supply a value or a code's
meaning. Column labels are matched against a whole header cell (after
lower-casing, collapsing spaces and dropping a leading form-locator number such
as "24. A." or "47"), first match wins, so specific labels come before general
ones. A header cell that matches nothing keeps its cells as `unlabeled_column`;
"Amount" or "Total" alone are AMBIGUOUS and never mapped to a money field.

Every paper layout here is UNVERIFIED_AGAINST_REAL_FORM until a real document
of that kind has been run and checked by a person.
"""
from __future__ import annotations

import re

LOCALE = "en-US"
STATUS = "UNVERIFIED_AGAINST_REAL_FORM"

# Column vocabularies: (field, type, pattern)
_EOB_COLS = [
    ("service_date", "date", r"(?:date|dates|date\(s\))(?: of service)?|service dates?|svc dates?|dos|from date"),
    ("service_code", "code_mod", r"(?:procedure|service|billing|cpt|hcpcs|cpt/hcpcs)(?: codes?)?|codes?"),
    ("description", "text", r"(?:service|services)(?: description| provided)?|description(?: of services?)?|type of service"),
    ("units", "qty", r"units?|qty|quantity"),
    ("billed", "money", r"(?:amount )?billed(?: amount)?|(?:provider |amount )?charged|charges?|billed charges?|total charges?"),
    ("allowed", "money", r"(?:plan |network )?allowed(?: amount)?|allowable(?: amount)?|eligible(?: amount)?"),
    ("adjustments", "money", r"(?:plan |network |provider )?discounts?|savings|adjustments?|(?:amount )?not covered|non.?covered"),
    ("deductible", "money", r"(?:applied to )?deductible"),
    ("copay", "money", r"co.?pay(?:ment)?s?"),
    ("coinsurance", "money", r"co.?insurance"),
    ("paid", "money", r"(?:plan|insurance|health plan|we) paid|paid by (?:plan|insurance)|plan payments?|benefit paid|amount paid|paid"),
    ("patient_amount", "money", r"(?:you|patient|member) (?:owe|may owe)|(?:your|patient|member) (?:share|responsibility|cost)|what you (?:may )?owe|amount you (?:may )?owe"),
    ("remarks", "text", r"remarks?(?: codes?)?|notes?|see notes|reason(?: codes?)?|explanation(?: codes?)?"),
]
_MSN_COLS = [
    ("service_date", "date", r"service dates?|dates?(?: of service)?|dos"),
    ("description", "text", r"services? provided(?: (?:&|and) billing codes?)?|services?(?: description)?|description"),
    ("units", "qty", r"units?|qty"),
    ("billed", "money", r"(?:amount )?provider charged|amount charged|charged"),
    ("approved", "money", r"medicare[-\s]*approved(?: amount)?|approved(?: amount)?"),
    ("medicare_paid", "money", r"(?:amount )?medicare paid(?: (?:provider|you))?"),
    ("maximum_patient_amount", "money", r"maximum you (?:may|might) be billed|max(?:imum)? you may be billed|you may be billed"),
    ("remarks", "text", r"see notes?|notes?"),
]
_BILL_COLS = [
    ("posting_date", "date", r"post(?:ing|ed)? date|trans(?:action)? date"),
    ("service_date", "date", r"(?:service )?dates?(?: of service)?|dos|svc dates?"),
    ("revenue_code", "code", r"rev(?:enue)?\.? ?(?:code|cd)\.?"),
    ("service_code", "code_mod", r"(?:cpt|hcpcs|procedure|service|billing|charge)(?: codes?)?|codes?|cdm"),
    ("description", "text", r"description(?: of services?)?|services?(?: description)?|items?|charge description|procedure description"),
    ("units", "qty", r"qty|quantity|units?"),
    ("billed", "money", r"charges?|amount billed|billed(?: amount)?|gross charges?|total charges?"),
    ("insurance_pending", "money", r"(?:insurance|ins\.?) (?:pending|due|balance)"),
    ("payments", "money", r"(?:insurance |ins\.? |patient |pt\.? )?payments?|(?:insurance |patient )?paid"),
    ("adjustments", "money", r"(?:insurance |contractual )?adjustments?|adj\.?|credits?|discounts?|write.?offs?"),
    ("balance", "money", r"(?:patient |line |your )?balance(?: due)?|amount due|you owe|patient (?:responsibility|due)"),
]
_UB04_COLS = [
    ("revenue_code", "code", r"rev\.? ?cd\.?|rev(?:enue)? codes?"),
    ("description", "text", r"description"),
    ("service_code", "code_mod", r"hcpcs(?:\s*/\s*rate)?(?:\s*/\s*hipps)?(?: codes?)?|cpt(?:/hcpcs)?(?: codes?)?"),
    ("service_date", "date", r"serv\.? ?date|service dates?"),
    ("units", "qty", r"serv\.? ?units|service units|units"),
    ("noncovered", "money", r"non.?covered(?: charges?)?"),
    ("billed", "money", r"total charges?|charges?"),
]
_CMS1500_COLS = [
    ("service_date", "date", r"date\(?s\)? of service|dates? of service|from(?: to)?"),
    ("place_of_service", "code", r"place of service|pos"),
    ("emg", "text", r"emg"),
    ("service_code", "code_mod", r"procedures?,? services?,? or supplies.*|cpt/hcpcs(?: modifier)?|cpt(?: code)?"),
    ("diagnosis_pointer", "text", r"diagnosis pointer|dx (?:ptr|pointer)"),
    ("billed", "money", r"\$? ?charges?"),
    ("units", "qty", r"days or units|units"),
    ("epsdt", "text", r"epsdt(?: family plan)?"),
    ("id_qualifier", "text", r"id\.? ?qual\.?"),
    ("rendering_provider", "id", r"rendering provider id\.? ?#?"),
]
_AMBIGUOUS = r"amount|amounts|total|totals|\$|value"

# Header labels: field -> (type, [pattern, ...]). A label must be followed by ':' / '#',
# a column gap, or the end of the line (value below), unless marked loose.
_EOB_HEAD = {
    "patient": ("text", [r"patient(?: name)?", r"member(?: name)?", r"subscriber name"]),
    "subscriber_id": ("id", [r"member id", r"subscriber id", r"member (?:number|no\.?|#)", r"id (?:number|#)",
                             r"identification number"]),
    "group": ("id", [r"group (?:number|no\.?|#)", r"group"]),
    "claim_id": ("id", [r"claim (?:number|no\.?|#|id)"]),
    "provider": ("text", [r"(?:rendering )?provider(?: name)?", r"provider of service", r"facility"]),
    "statement_date": ("date", [r"statement date", r"date processed", r"processed (?:date|on)", r"(?:notice|eob) date"]),
    "service_date": ("date", [r"date\(?s\)? of service", r"service dates?"]),
    "appeal": ("sentence", [r"appeal"]),
}
_MSN_HEAD = {
    "patient": ("text", [r"(?:beneficiary |patient )?name"]),
    "subscriber_id": ("id", [r"medicare (?:number|#|id)", r"beneficiary id"]),
    "claim_id": ("id", [r"claim (?:number|no\.?|#)"]),
    "provider": ("text", [r"provider(?: name)?", r"supplier(?: name)?"]),
    "statement_date": ("date", [r"(?:notice|statement) date", r"date of (?:this )?notice"]),
    "service_date": ("date", [r"service dates?", r"date\(?s\)? of service"]),
    "appeal_deadline": ("date", [r"(?:file|submit|request)? ?(?:an |your )?appeal[^.\n]{0,60}?\bby"], ),
    "appeal": ("sentence", [r"appeal"]),
}
_BILL_HEAD = {
    "patient": ("text", [r"patient(?: name)?"]),
    "guarantor": ("text", [r"guarantor(?: name)?", r"responsible party"]),
    "claim_id": ("id", [r"account (?:number|no\.?|#)", r"acct\.?(?: no\.?| #| number)?", r"account",
                        r"statement (?:number|no\.?|#)", r"invoice (?:number|no\.?|#)"]),
    "statement_date": ("date", [r"statement date", r"bill(?:ing)? date", r"date of statement"]),
    "due_date": ("date", [r"(?:payment )?due date", r"payment due", r"please pay by", r"pay by"]),
    "balance": ("money", [r"(?:total )?balance due", r"(?:total )?amount due", r"total due", r"new balance",
                          r"patient balance", r"please pay", r"you owe"]),
    "provider": ("text", [r"provider", r"facility", r"physician"]),
    "payer": ("text", [r"(?:primary )?insurance(?: company)?", r"payer"]),
    "service_date": ("date", [r"date\(?s\)? of service", r"service dates?"]),
}
_UB04_HEAD = {
    "patient": ("text", [r"(?:8b?\.? ?)?patient name", r"pat\.? name"]),
    "claim_id": ("id", [r"(?:3a\.? ?)?pat\.? ?cntl\.? ?#", r"patient control (?:number|no\.?|#)"]),
    "medical_record": ("id", [r"(?:3b\.? ?)?med\.? ?rec\.? ?#", r"medical record (?:number|no\.?|#)"]),
    "bill_type": ("code", [r"(?:4\.? ?)?type of bill", r"bill type"]),
    "statement_period": ("date", [r"(?:6\.? ?)?statement covers period(?: from through)?", r"statement period"]),
    "subscriber": ("id", [r"(?:60\.? ?)?insured'?s unique id", r"subscriber id"]),
    "payer": ("text", [r"(?:50\.? ?)?payer(?: name)?"]),
    "provider": ("text", [r"billing provider(?: name)?", r"provider name"]),
    "diagnosis": ("code", [r"(?:67\.? ?)?prin(?:cipal|\.)? ?diag(?:nosis)?\.?(?: (?:code|cd)\.?)?"]),
}
_CMS1500_HEAD = {
    "patient": ("text", [r"(?:2\.? ?)?patient'?s name(?: ?\([^)]*\))?"]),
    "subscriber": ("text", [r"(?:4\.? ?)?insured'?s name(?: ?\([^)]*\))?"]),
    "subscriber_id": ("id", [r"(?:1a\.? ?)?insured'?s i\.? ?d\.? number(?: ?\([^)]*\))?"]),
    "claim_id": ("id", [r"(?:26\.? ?)?patient'?s account no\.?"]),
    "tax_id": ("id", [r"(?:25\.? ?)?federal tax i\.? ?d\.? number"]),
    "provider": ("text", [r"(?:33\.? ?)?billing provider info(?: ?& ?ph ?#)?"]),
    "total": ("money", [r"(?:28\.? ?)?total charge"]),
    "amount_paid": ("money", [r"(?:29\.? ?)?amount paid"]),
    "diagnosis": ("diag_list", [r"(?:21\.? ?)?diagnosis or nature of illness or injury(?: ?\([^)]*\))?"]),
}

LAYOUTS = {
    "CMS1500": {"columns": _CMS1500_COLS, "header": _CMS1500_HEAD, "assertion": "submitted",
                "required_header": ["patient", "total"], "required_line": ["service_date", "service_code", "billed"],
                "header_total": ("total", "billed"),
                "notes": ["Amount paid (box 29) is claim-level; it is never spread across lines."]},
    "CMS1450": {"columns": _UB04_COLS, "header": _UB04_HEAD, "assertion": "submitted",
                "required_header": ["patient"], "required_line": ["revenue_code", "billed"],
                "notes": ["Revenue and bill-type codes are kept as printed identifiers; no meaning is supplied."]},
    "EOB": {"columns": _EOB_COLS, "header": _EOB_HEAD, "assertion": "adjudicated",
            "required_header": [], "required_line": ["service_date", "billed"],
            "notes": ["An EOB is not a bill. Patient amounts are the plan's statement, not a provider's demand."]},
    "MSN": {"columns": _MSN_COLS, "header": _MSN_HEAD, "assertion": "adjudicated",
            "required_header": [], "required_line": ["billed"],
            "notes": ["A Medicare Summary Notice is not a bill."]},
    "PATIENT_BILL": {"columns": _BILL_COLS, "header": _BILL_HEAD, "assertion": "billed",
                     "required_header": [], "required_line": ["service_date"],
                     "notes": ["Posted payments and adjustments are the provider's statement of them, not proof "
                               "of settled funds."]},
}

# Fields that report money as paid, whatever the document's own assertion kind.
PAID_FIELDS = {"paid", "amount_paid", "medicare_paid", "payments"}

_LEAD = re.compile(r"^(?:\d{1,2}[a-z]?\.?\s+)?(?:[a-j]\.\s*)?", re.I)


def norm_header(tok: str) -> str:
    t = re.sub(r"\s+", " ", tok.strip().lower()).rstrip(":").strip()
    return _LEAD.sub("", t).strip()


def match_column(profile: str, tok: str):
    """(field, type) for a header cell, ('amount_unqualified', 'money?') if ambiguous, else None."""
    t = norm_header(tok)
    if not t:
        return None
    for field, kind, pat in LAYOUTS[profile]["columns"]:
        if re.fullmatch(pat, t, re.I):
            return field, kind
    if re.fullmatch(_AMBIGUOUS, t, re.I):
        return "amount_unqualified", "ambiguous"
    return None


def header_patterns(profile: str):
    out = []
    for field, spec in LAYOUTS[profile]["header"].items():
        kind, pats = spec[0], spec[1]
        for p in pats:
            out.append((field, kind, re.compile(rf"(?<![\w']){p}", re.I)))
    return out
