"""FHIR financial resources: Claim, ClaimResponse, ExplanationOfBenefit, Invoice,
PaymentNotice, PaymentReconciliation.

The profile is structural (resourceType), so no confirmation is asked. The FHIR
version is whatever the document declares in meta.profile; when nothing does,
the resource is read with R4 element names and says so. R5 renames are read
where they are unambiguous and flagged.

Each value: raw string exactly as in the JSON (numbers keep their printed
digits), a JSON pointer locator, a normalized value where the type is clear.
Adjudication and total amounts are labeled only by their own category coding;
a missing category is an issue, never guessed from position.
"""
from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation
from typing import Any, List, Optional

PROFILE_OF = {"Claim": "FHIR_Claim", "ClaimResponse": "FHIR_ClaimResponse",
              "ExplanationOfBenefit": "FHIR_ExplanationOfBenefit", "Invoice": "FHIR_Invoice",
              "PaymentNotice": "FHIR_PaymentNotice", "PaymentReconciliation": "FHIR_PaymentReconciliation"}
ASSERTION = {"Claim": "submitted", "ClaimResponse": "adjudicated", "ExplanationOfBenefit": "adjudicated",
             "Invoice": "billed", "PaymentNotice": "paid-as-reported", "PaymentReconciliation": "paid-as-reported"}
STATUS = "UNVERIFIED_AGAINST_REAL_EXPORT"


def _esc(k) -> str:
    return str(k).replace("~", "~0").replace("/", "~1")


def load(data: bytes) -> Any:
    """Parse keeping every number's printed text (parse_float/int=str)."""
    return json.loads(data.decode("utf-8-sig"), parse_float=str, parse_int=str)


def resolve(doc: Any, pointer: str) -> Any:
    cur = doc
    for part in [p.replace("~1", "/").replace("~0", "~") for p in pointer.split("/")[1:]] if pointer else []:
        cur = cur[int(part)] if isinstance(cur, list) else cur[part]
    return cur


def resources(doc: Any) -> List[tuple]:
    if not isinstance(doc, dict):
        return []
    if doc.get("resourceType") == "Bundle":
        out = []
        for i, e in enumerate(doc.get("entry") or []):
            r = (e or {}).get("resource") or {}
            if r.get("resourceType") in PROFILE_OF:
                out.append((r, f"/entry/{i}/resource"))
        return out
    return [(doc, "")] if doc.get("resourceType") in PROFILE_OF else []


class _Rec:
    def __init__(self, source_id: str, assertion: str):
        self.source_id, self.assertion = source_id, assertion
        self.header: List[dict] = []
        self.rows: List[dict] = []
        self.issues: List[dict] = []
        self.read: set = set()

    def f(self, name: str, ptr: str, raw: Any, kind: str = "text", label: Optional[str] = None,
          row: Optional[dict] = None, assertion: Optional[str] = None, issues: Optional[List[str]] = None,
          **extra) -> Optional[dict]:
        if raw is None or isinstance(raw, (dict, list)):
            return None
        raw_s = raw if isinstance(raw, str) else json.dumps(raw)
        norm, iss = raw_s, list(issues or [])
        if kind == "money":
            try:
                norm = str(Decimal(raw_s))
            except InvalidOperation:
                norm, iss = None, iss + ["TYPE_MISMATCH"]
        elif kind == "qty":
            try:
                norm = str(Decimal(raw_s))
            except InvalidOperation:
                norm, iss = None, iss + ["TYPE_MISMATCH"]
        fd = {"field": name, "label": label, "raw_value": raw_s, "normalized_value": norm, "source_id": self.source_id,
              "locator": {"json_pointer": ptr}, "row_id": row["row_id"] if row else None,
              "assertion_kind": assertion or self.assertion, "issues": iss}
        fd.update({k: v for k, v in extra.items() if v is not None})
        (row["fields"] if row else self.header).append(fd)
        return fd

    def money(self, name: str, obj: Any, ptr: str, label=None, row=None, assertion=None, issues=None, **extra):
        if not isinstance(obj, dict):
            return
        fd = self.f(name, ptr + "/value", obj.get("value"), "money", label, row, assertion, issues, **extra)
        if fd is not None:
            if obj.get("currency") is not None:
                fd["currency"] = obj["currency"]
                fd["currency_locator"] = {"json_pointer": ptr + "/currency"}
            else:
                fd["issues"].append("CURRENCY_NOT_STATED")

    def codeable(self, name: str, cc: Any, ptr: str, row=None, label=None):
        if not isinstance(cc, dict):
            return
        for i, c in enumerate(cc.get("coding") or []):
            p = f"{ptr}/coding/{i}"
            sysl = c.get("system")
            self.f(name, p + "/code", c.get("code"), "text", label or sysl, row, code_system=sysl)
            self.f(name + "_display", p + "/display", c.get("display"), "text", sysl, row)
        self.f(name + "_text", ptr + "/text", cc.get("text"), "text", label, row)

    def ref(self, name: str, r: Any, ptr: str, row=None):
        if not isinstance(r, dict):
            return
        self.f(name, ptr + "/reference", r.get("reference"), "text", "reference", row)
        self.f(name, ptr + "/display", r.get("display"), "text", "display", row)
        ident = r.get("identifier")
        if isinstance(ident, dict):
            self.f(name, ptr + "/identifier/value", ident.get("value"), "text", ident.get("system"), row)

    def period(self, name: str, p: Any, ptr: str, row=None):
        if isinstance(p, dict):
            self.f(name + "_start", ptr + "/start", p.get("start"), "text", None, row)
            self.f(name + "_end", ptr + "/end", p.get("end"), "text", None, row)

    def new_row(self, kind: str, ptr: str) -> dict:
        row = {"row_id": f"{kind}{len([r for r in self.rows if r['kind'] == kind]) + 1}", "kind": kind,
               "pointer": ptr, "fields": [], "issues": []}
        self.rows.append(row)
        return row


def _adjudication(rec: _Rec, adj: Any, ptr: str, row: dict, paid_cats=("benefit", "paid", "payment")):
    for k, a in enumerate(adj or []):
        p = f"{ptr}/{k}"
        cat = (a or {}).get("category") or {}
        codings = cat.get("coding") or []
        code = codings[0].get("code") if codings else None
        sysl = codings[0].get("system") if codings else None
        label = code or cat.get("text")
        iss = [] if label else ["CATEGORY_MISSING"]
        if codings:
            rec.f("adjudication_category", p + "/category/coding/0/code", code, "text", sysl, row)
        rec.money("adjudication_amount", a.get("amount"), p + "/amount", label, row, issues=iss,
                  category_system=sysl)
        rec.f("adjudication_value", p + "/value", a.get("value"), "qty", label, row, issues=iss)
        rec.codeable("adjudication_reason", a.get("reason"), p + "/reason", row)
        # R5: reason is CodeableReference { concept }
        if isinstance(a.get("reason"), dict) and "concept" in a["reason"]:
            rec.codeable("adjudication_reason", a["reason"]["concept"], p + "/reason/concept", row)


def _items(rec: _Rec, res: dict, ptr: str, rt: str):
    for i, it in enumerate(res.get("item") or []):
        p = f"{ptr}/item/{i}"
        row = rec.new_row("item", p)
        rec.f("sequence", p + "/sequence", it.get("sequence"), "text", None, row)
        rec.f("item_sequence", p + "/itemSequence", it.get("itemSequence"), "text", None, row)
        rec.codeable("service_code", it.get("productOrService"), p + "/productOrService", row)
        for m, mod in enumerate(it.get("modifier") or []):
            rec.codeable("modifier", mod, f"{p}/modifier/{m}", row)
        rec.f("service_date", p + "/servicedDate", it.get("servicedDate"), "text", None, row)
        rec.period("service_period", it.get("servicedPeriod"), p + "/servicedPeriod", row)
        q = it.get("quantity")
        if isinstance(q, dict):
            rec.f("units", p + "/quantity/value", q.get("value"), "qty", q.get("unit"), row)
        rec.money("unit_price", it.get("unitPrice"), p + "/unitPrice", None, row)
        rec.f("factor", p + "/factor", it.get("factor"), "qty", None, row)
        rec.money("billed" if rt == "Claim" else "net", it.get("net"), p + "/net", "net", row)
        rec.codeable("place_of_service", it.get("locationCodeableConcept"), p + "/locationCodeableConcept", row)
        for k, d in enumerate(it.get("diagnosisSequence") or it.get("diagnosisLinkId") or []):
            rec.f("diagnosis_pointer", f"{p}/diagnosisSequence/{k}", d, "text", None, row)
        if "adjudication" in it:
            _adjudication(rec, it["adjudication"], p + "/adjudication", row)
        if it.get("detail"):
            row["issues"].append("ITEM_DETAIL_NOT_READ")


def _totals(rec: _Rec, res: dict, ptr: str):
    tot = res.get("total")
    if isinstance(tot, dict):                                  # Claim.total (Money)
        rec.money("total", tot, ptr + "/total", "total")
    elif isinstance(tot, list):                                # ClaimResponse/EOB.total[] (category, amount)
        for k, t in enumerate(tot):
            cat = (t or {}).get("category") or {}
            codings = cat.get("coding") or []
            label = (codings[0].get("code") if codings else None) or cat.get("text")
            rec.money("total", t.get("amount"), f"{ptr}/total/{k}/amount", label,
                      issues=[] if label else ["CATEGORY_MISSING"])


def _payment(rec: _Rec, pay: Any, ptr: str):
    if not isinstance(pay, dict):
        return
    rec.codeable("payment_type", pay.get("type"), ptr + "/type")
    rec.money("paid", pay.get("amount"), ptr + "/amount", "payment amount", assertion="paid-as-reported")
    rec.money("payment_adjustment", pay.get("adjustment"), ptr + "/adjustment", "adjustment")
    rec.f("payment_date", ptr + "/date", pay.get("date"), "text", None, assertion="paid-as-reported")
    ident = pay.get("identifier")
    if isinstance(ident, dict):
        rec.f("payment_identifier", ptr + "/identifier/value", ident.get("value"), "text", ident.get("system"))


HANDLED = {"resourceType", "id", "meta", "text", "identifier", "status", "use", "type", "subType", "patient",
           "subject", "created", "provider", "insurer", "issuer", "billablePeriod", "item", "total", "payment",
           "outcome", "disposition", "request", "claim", "claimResponse", "diagnosis", "lineItem", "totalNet",
           "totalGross", "paymentTerms", "date", "recipient", "response", "paymentDate", "payee", "amount",
           "paymentStatus", "paymentAmount", "paymentIdentifier", "detail", "allocation", "period",
           "paymentIssuer", "insurance", "related", "priority", "referral", "facility", "contained"}


def extract_resource(res: dict, ptr: str, source_id: str) -> dict:
    rt = res["resourceType"]
    assertion = ASSERTION[rt]
    issues: List[dict] = []
    if rt == "Claim":
        use = res.get("use")
        if use == "predetermination":
            assertion = "estimated"
        elif use == "preauthorization":
            issues.append({"code": "USE_PREAUTHORIZATION", "message": "This Claim asks for authorization; it is "
                           "not a record of charges incurred."})
    rec = _Rec(source_id, assertion)
    rec.f("resource_id", ptr + "/id", res.get("id"), "text", "resource id (not a business identifier)")
    for i, ident in enumerate(res.get("identifier") or ([res["identifier"]] if isinstance(res.get("identifier"), dict) else [])):
        rec.f("identifier", f"{ptr}/identifier/{i}/value" if isinstance(res.get("identifier"), list) else ptr + "/identifier/value",
              (ident or {}).get("value"), "text", (ident or {}).get("system"))
    for k in ("status", "use", "outcome", "disposition", "created", "date", "paymentStatus"):
        if k == "paymentStatus":
            rec.codeable("payment_status", res.get(k), f"{ptr}/{k}")
        else:
            rec.f(k, f"{ptr}/{k}", res.get(k), "text")
    rec.codeable("claim_type", res.get("type"), ptr + "/type")
    for k in ("patient", "subject", "provider", "insurer", "issuer", "recipient", "payee", "paymentIssuer",
              "request", "response", "claim", "claimResponse", "facility"):
        rec.ref(re.sub(r"(?<!^)(?=[A-Z])", "_", k).lower(), res.get(k), f"{ptr}/{k}")
    rec.period("billable_period", res.get("billablePeriod"), ptr + "/billablePeriod")
    rec.period("period", res.get("period"), ptr + "/period")
    for i, rel in enumerate(res.get("related") or []):
        rec.ref("related_claim", (rel or {}).get("claim"), f"{ptr}/related/{i}/claim")
    for i, ins in enumerate(res.get("insurance") or []):
        rec.ref("coverage", (ins or {}).get("coverage"), f"{ptr}/insurance/{i}/coverage")
    for i, dx in enumerate(res.get("diagnosis") or []):
        rec.f("diagnosis_sequence", f"{ptr}/diagnosis/{i}/sequence", (dx or {}).get("sequence"), "text")
        rec.codeable("diagnosis", (dx or {}).get("diagnosisCodeableConcept"), f"{ptr}/diagnosis/{i}/diagnosisCodeableConcept")
    _items(rec, res, ptr, rt)
    _totals(rec, res, ptr)
    _payment(rec, res.get("payment"), ptr + "/payment")
    if rt == "Invoice":
        for i, li in enumerate(res.get("lineItem") or []):
            p = f"{ptr}/lineItem/{i}"
            row = rec.new_row("line", p)
            rec.f("sequence", p + "/sequence", li.get("sequence"), "text", None, row)
            rec.codeable("service_code", li.get("chargeItemCodeableConcept"), p + "/chargeItemCodeableConcept", row)
            rec.ref("charge_item", li.get("chargeItemReference"), p + "/chargeItemReference", row)
            for k, pc in enumerate(li.get("priceComponent") or []):
                q = f"{p}/priceComponent/{k}"
                ptype = (pc or {}).get("type")
                rec.f("price_component_type", q + "/type", ptype, "text", None, row)
                rec.codeable("price_component_code", pc.get("code"), q + "/code", row)
                rec.f("price_component_factor", q + "/factor", pc.get("factor"), "qty", ptype, row)
                rec.money("price_component_amount", pc.get("amount"), q + "/amount", ptype, row,
                          issues=[] if ptype else ["CATEGORY_MISSING"])
        rec.money("total_net", res.get("totalNet"), ptr + "/totalNet", "totalNet")
        rec.money("total_gross", res.get("totalGross"), ptr + "/totalGross", "totalGross")
        rec.f("payment_terms", ptr + "/paymentTerms", res.get("paymentTerms"), "text")
    if rt == "PaymentNotice":
        rec.ref("payment", res.get("payment"), ptr + "/payment")
        rec.f("payment_date", ptr + "/paymentDate", res.get("paymentDate"), "text")
        rec.money("paid", res.get("amount"), ptr + "/amount", "amount")
    if rt == "PaymentReconciliation":
        r5 = "allocation" in res or ("amount" in res and "paymentAmount" not in res)
        if r5:
            issues.append({"code": "R5_ELEMENT_NAMES", "message": "Element names match FHIR R5 (amount/date/"
                           "allocation); read under those names."})
        rec.f("payment_date", ptr + ("/date" if r5 and "date" in res else "/paymentDate"),
              res.get("date") if r5 and "date" in res else res.get("paymentDate"), "text")
        rec.money("paid", res.get("amount") if r5 else res.get("paymentAmount"), ptr + ("/amount" if r5 else "/paymentAmount"),
                  "payment amount")
        pid = res.get("paymentIdentifier")
        if isinstance(pid, dict):
            rec.f("payment_identifier", ptr + "/paymentIdentifier/value", pid.get("value"), "text", pid.get("system"))
        key = "allocation" if "allocation" in res else "detail"
        for i, d in enumerate(res.get(key) or []):
            p = f"{ptr}/{key}/{i}"
            row = rec.new_row("allocation", p)
            ident = (d or {}).get("identifier")
            if isinstance(ident, dict):
                rec.f("identifier", p + "/identifier/value", ident.get("value"), "text", ident.get("system"), row)
            rec.codeable("allocation_type", d.get("type"), p + "/type", row)
            for k in ("request", "response", "submitter", "payee", "responsible", "target"):
                rec.ref(k, d.get(k), f"{p}/{k}", row)
            rec.f("date", p + "/date", d.get("date"), "text", None, row)
            rec.money("paid", d.get("amount"), p + "/amount", "allocated amount", row)
    profiles = ((res.get("meta") or {}).get("profile") or [])
    version = "declared by meta.profile" if profiles else "not declared; read with FHIR R4 element names"
    if not profiles:
        issues.append({"code": "FHIR_VERSION_UNDECLARED", "message": "No meta.profile; read with FHIR R4 element names."})
    unread = sorted(k for k in res if k not in HANDLED and not k.startswith("_"))
    if unread:
        issues.append({"code": "ELEMENTS_NOT_READ", "message": "Elements kept in the source but not read: "
                       + ", ".join(unread) + "."})
    return {"adapter": "fhir-financial-v1", "adapter_status": STATUS, "locale": None, "assertion_kind": assertion,
            "resource_type": rt, "pointer": ptr, "fhir_version": version, "meta_profile": profiles,
            "header": rec.header, "tables": [{"table_id": "items", "columns": [], "rows": rec.rows, "totals": None}]
            if rec.rows else [], "issues": issues,
            "notes": ["An adjudication response does not by itself prove funds were paid."]
            if rt in ("ClaimResponse", "ExplanationOfBenefit") else []}
