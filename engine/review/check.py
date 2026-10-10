"""Ingest double-check: deterministic gate, then the AI checks the extraction.

Three passes, each advisory except the first:
  1. GATE (always, no AI): page quality flags from ingest — empty, garbled,
     low OCR confidence.
  2. PAGES (vision model): each page image beside the text pulled off it —
     "do these agree?" Catches misread figures, scrambled columns, stamps and
     handwriting that never reached the text.
  3. VALUES (text model): each extracted Float beside its source line —
     "does the line say exactly this?" Catches parse errors: wrong unit, wrong
     column, a dropped "not".

What the AI may do, and what it may not:
  * It may raise a discrepancy. A discrepancy must quote the extracted text
    exactly; a quote that isn't in the text is discarded (the model can't
    invent a problem with words that aren't there).
  * It never changes a value, a unit, a date, or a content status (§3.2: model
    output never promotes). It only moves the provenance lock (§3.1):
      DISAGREE                       -> CHECKER_DISCREPANCY
      page AGREES on an OCR page     -> VERIFIED_SPAN (verified_by CHECKER)
    and a person settles every discrepancy (resolutions below).

External AI (PSi, §17) sees document content only when the user turned on the
matching permission for that provider; otherwise those passes are NOT_CHECKED
and say why.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional, Protocol

from .pages import render_png

PAGE_PROMPT = """You check a scanned medical page against the text a computer extracted from it.
Answer ONLY with JSON, no other words:
{"verdict": "AGREES" | "DISAGREES" | "UNREADABLE",
 "differences": [{"text_says": "<words copied exactly from the extracted text>", "page_shows": "<what the page shows there>"}]}
Report a difference only when the page clearly shows something different: a number, unit, date, name,
or a word that changes meaning (for example "no" or "not"), or a line or stamp missing from the text.
Ignore spacing, line breaks and formatting. Do not interpret, diagnose or summarize."""

VALUE_PROMPT = """You double-check values a computer extracted from medical text.
Each item has the extracted fields and the source line it came from.
Say whether the source line supports exactly those fields.
Answer ONLY with JSON, no other words:
{"items": [{"id": "<item id>", "verdict": "AGREE" | "DISAGREE" | "CANNOT_TELL",
            "source_says": "<words copied exactly from the source line>", "reason": "<one short sentence>"}]}
DISAGREE when the value, unit, date, test name, medication, or negation differs from the source line.
Never correct anything yourself and never add medical advice; copy the source words."""

BATCH = 15
MAX_PAGE_TEXT = 6000


class ChatModel(Protocol):
    label: str

    def chat(self, messages: list, max_tokens: int = 800, temperature: float = 0.0) -> str: ...


@dataclass
class Policy:
    """What a model may be shown. Local models: everything. External: the user's switches."""
    pages: bool = True          # page images + page text
    values: bool = True         # source lines of extracted values
    reason: str = ""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _json(reply: str) -> Optional[dict]:
    m = re.search(r"\{.*\}", reply or "", re.S)
    if not m:
        return None
    try:
        v = json.loads(m.group(0))
        return v if isinstance(v, dict) else None
    except ValueError:
        return None


def _norm(s: str) -> str:
    return " ".join(str(s).split()).lower()


def _line_of(text: str, start: int, end: int, cap: int = 400) -> tuple:
    """The line around a span, never crossing a page break (\f) or more than `cap` characters."""
    a = max(text.rfind("\n", 0, start), text.rfind("\f", 0, start)) + 1
    ends = [i for i in (text.find("\n", end), text.find("\f", end)) if i >= 0]
    b = min(ends) if ends else len(text)
    a = max(a, start - cap // 2)
    b = min(b, end + cap // 2)
    return a, b, text[a:b]


def gate(doc) -> List[dict]:
    return [{"page": p.number, "method": p.method, "flags": list(p.quality.flags),
             "ocr_confidence": p.quality.ocr_mean_confidence} for p in doc.pages if p.quality.flags]


def check_pages(model: Optional[ChatModel], policy: Policy, doc, original: bytes,
                image_url: Callable[[bytes], str], progress=None) -> List[dict]:
    out = []
    for p in doc.pages:
        base = {"page": p.number, "method": p.method}
        png = render_png(original, doc.media_type, p.number) if doc.media_type == "application/pdf" \
            or doc.media_type.startswith("image") else None
        if png is None:
            out.append(dict(base, verdict="NO_PAGE_IMAGE", differences=[],
                            note="Text format; there is no page image to compare."))
            continue
        if model is None or not policy.pages:
            out.append(dict(base, verdict="NOT_CHECKED", differences=[],
                            note=policy.reason or "No page checker is set up."))
            continue
        page_text = p.text[:MAX_PAGE_TEXT]
        try:
            reply = model.chat([{"role": "system", "content": PAGE_PROMPT},
                                {"role": "user", "content": [
                                    {"type": "image_url", "image_url": {"url": image_url(png)}},
                                    {"type": "text", "text": "TEXT EXTRACTED FROM THIS PAGE\n" + (page_text or "[empty]")}]}],
                               max_tokens=700)
        except Exception as exc:
            out.append(dict(base, verdict="CHECK_FAILED", differences=[], note=str(exc)[:200]))
            continue
        data = _json(reply)
        if not data or data.get("verdict") not in ("AGREES", "DISAGREES", "UNREADABLE"):
            out.append(dict(base, verdict="CHECK_FAILED", differences=[], note="The checker's reply wasn't usable."))
            continue
        kept, dropped = [], 0
        for d in data.get("differences") or []:
            said = str(d.get("text_says", "")).strip()
            if said and _norm(said) in _norm(p.text):
                kept.append({"text_says": said, "page_shows": str(d.get("page_shows", ""))[:300]})
            else:
                dropped += 1
        verdict = data["verdict"]
        if verdict == "DISAGREES" and not kept:
            verdict = "CHECK_FAILED"     # a disagreement that quotes nothing real can't be acted on
        out.append(dict(base, verdict=verdict, differences=kept,
                        note=f"{dropped} difference(s) discarded: quoted words not in the extracted text." if dropped else ""))
        if progress:
            progress()
    return out


def _item(f: dict, text: str) -> dict:
    pv = f["provenance"]
    a, b, line = _line_of(text, pv["char_start"], pv["char_end"])
    fields = {"concept": f.get("concept"), "kind": f.get("kind"), "polarity": f.get("polarity"),
              "date": (f.get("event_date") or {}).get("value")}
    if f.get("value"):
        v = f["value"]
        fields.update({k: v.get(k) for k in ("analyte", "numeric", "qualitative", "comparator", "unit",
                                             "reference_low", "reference_high", "reference_qualitative",
                                             "reference_unit", "reported_flag") if v.get(k) is not None})
    if f.get("medication"):
        fields["medication"] = f["medication"]
    return {"id": f["id"], "page": pv.get("page"), "span": [pv["char_start"], pv["char_end"]],
            "line_span": [a, b], "fields": fields, "source_line": line}


def check_values(model: Optional[ChatModel], policy: Policy, doc, floats: List[dict], progress=None) -> List[dict]:
    items = [_item(f, doc.canonical_text) for f in floats]
    if model is None or not policy.values:
        note = policy.reason or "No AI model is set up."
        return [dict(i, verdict="NOT_CHECKED", source_says="", reason=note) for i in items]
    results: Dict[str, dict] = {}
    for k in range(0, len(items), BATCH):
        chunk = items[k:k + BATCH]
        payload = [{"id": i["id"], "extracted": i["fields"], "source_line": i["source_line"]} for i in chunk]
        try:
            reply = model.chat([{"role": "system", "content": VALUE_PROMPT},
                                {"role": "user", "content": json.dumps({"items": payload}, ensure_ascii=False)}],
                               max_tokens=200 + 120 * len(chunk))
            data = _json(reply) or {}
        except Exception as exc:
            data = {"_error": str(exc)[:200]}
        by_id = {str(r.get("id")): r for r in data.get("items") or [] if isinstance(r, dict)}
        for i in chunk:
            r = by_id.get(i["id"])
            if r is None or r.get("verdict") not in ("AGREE", "DISAGREE", "CANNOT_TELL"):
                results[i["id"]] = dict(i, verdict="CHECK_FAILED", source_says="",
                                        reason=data.get("_error") or "No usable answer for this item.")
                continue
            says = str(r.get("source_says", "")).strip()
            verdict, guard = r["verdict"], None
            if verdict == "DISAGREE" and (not says or _norm(says) not in _norm(i["source_line"])):
                verdict, guard = "CANNOT_TELL", "QUOTE_NOT_IN_SOURCE"
            results[i["id"]] = dict(i, verdict=verdict, source_says=says[:300],
                                    reason=str(r.get("reason", ""))[:300], guard=guard)
        if progress:
            progress()
    return [results[i["id"]] for i in items]


def run_check(doc, original: bytes, floats: List[dict], page_model: Optional[ChatModel], page_policy: Policy,
              value_model: Optional[ChatModel], value_policy: Policy, image_url: Callable[[bytes], str],
              progress=None) -> dict:
    mine = [f for f in floats if f["provenance"]["document_id"] == doc.doc_id]
    pages = check_pages(page_model, page_policy, doc, original, image_url, progress)
    items = check_values(value_model, value_policy, doc, mine, progress)
    count = lambda xs, v: sum(1 for x in xs if x["verdict"] == v)  # noqa: E731
    return {
        "doc_id": doc.doc_id, "checked_at": _now(),
        "checkers": {"pages": getattr(page_model, "label", None) if page_policy.pages else None,
                     "values": getattr(value_model, "label", None) if value_policy.values else None},
        "gate": gate(doc), "pages": pages, "items": items,
        "summary": {"pages_agree": count(pages, "AGREES"), "pages_disagree": count(pages, "DISAGREES"),
                    "items_agree": count(items, "AGREE"), "items_disagree": count(items, "DISAGREE"),
                    "items_cannot_tell": count(items, "CANNOT_TELL"),
                    "not_checked": count(pages, "NOT_CHECKED") + count(items, "NOT_CHECKED"),
                    "failed": count(pages, "CHECK_FAILED") + count(items, "CHECK_FAILED"),
                    "gate_flags": len(gate(doc))},
    }


# ------------------------------------------------------------------ locks
RESOLUTIONS = ("EXTRACTION_CORRECT", "EXTRACTION_WRONG")


def open_issues(report: dict, resolutions: Dict[str, str]) -> List[dict]:
    """Everything a person still needs to look at."""
    issues = []
    for p in report["pages"]:
        key = f"page:{p['page']}"
        if p["verdict"] in ("DISAGREES", "UNREADABLE") and key not in resolutions:
            issues.append({"target": key, "kind": "PAGE", **p})
    for g in report["gate"]:
        key = f"page:{g['page']}"
        if key not in resolutions and not any(i["target"] == key for i in issues):
            issues.append({"target": key, "kind": "GATE", **g})
    for i in report["items"]:
        key = f"item:{i['id']}"
        if i["verdict"] == "DISAGREE" and key not in resolutions:
            issues.append({"target": key, "kind": "ITEM", **i})
    return issues


def apply_locks(floats: List[dict], reports: Dict[str, dict], resolutions: Dict[str, Dict[str, str]]) -> None:
    """Move provenance locks (§3.1) from check results and a person's resolutions. Never touches content status."""
    for f in floats:
        pv = f["provenance"]
        doc = pv["document_id"]
        rep, res = reports.get(doc), resolutions.get(doc, {})
        if rep is None and not res:
            continue
        page = pv.get("page")
        item_res, page_res = res.get(f"item:{f['id']}"), res.get(f"page:{page}")
        decided = item_res or page_res
        if decided == "EXTRACTION_CORRECT":
            f["provenance_lock"], f["lock_basis"] = "VERIFIED_SPAN", "CONFIRMED_BY_USER"
            continue
        if decided == "EXTRACTION_WRONG":
            f["provenance_lock"], f["lock_basis"] = "FAILED", "REJECTED_BY_USER"
            continue
        if rep is None:
            continue
        item = next((i for i in rep["items"] if i["id"] == f["id"]), None)
        pg = next((p for p in rep["pages"] if p["page"] == page), None)
        span_text = _norm(f.get("original_text", ""))
        if item and item["verdict"] == "DISAGREE":
            f["provenance_lock"], f["lock_basis"] = "CHECKER_DISCREPANCY", "VALUE_CHECK"
        elif pg and pg["verdict"] == "DISAGREES" and span_text and any(
                _norm(d["text_says"]) in span_text or span_text in _norm(d["text_says"]) for d in pg["differences"]):
            f["provenance_lock"], f["lock_basis"] = "CHECKER_DISCREPANCY", "PAGE_CHECK"
        elif pg and pg["verdict"] == "AGREES" and f["provenance_lock"] == "UNVERIFIED_EXTRACTION":
            f["provenance_lock"], f["lock_basis"] = "VERIFIED_SPAN", "PAGE_CHECKER_AGREES"
