"""Gaps (§6) and Pattern Flags (§8).

A Gap is a declared search that found nothing. It records what was sought,
where, over which window, and how many documents actually fell in the window;
an empty window renders differently from a search that found nothing.

Flags name structure only; no disease or mechanism unless it is the literal
concept of a cited Float.
"""
from __future__ import annotations

import hashlib
import re
from datetime import date, timedelta
from typing import Iterable, List, Optional, Tuple

from . import lexicon
from .floats import RecordDoc


def _gid(*parts: object) -> str:
    return "GAP-" + hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:10]


def _fid(*parts: object) -> str:
    return "FLAG-" + hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:10]


def search(docs: List[RecordDoc], terms: Iterable[str], start: Optional[str], end: Optional[str],
           exclude: Optional[Tuple[str, int, int]] = None, basis: str = "") -> dict:
    """Search canonical text of documents dated in [start, end] (None = open) for any term."""
    terms = [t for t in dict.fromkeys(terms) if t]
    in_window = [d for d in docs if d.date and (start is None or d.date >= start) and (end is None or d.date <= end)]
    hits = []
    for d in in_window:
        for t in terms:
            for m in re.finditer(rf"(?<![A-Za-z0-9]){re.escape(t)}(?![A-Za-z0-9])", d.text, re.I):
                if exclude and d.doc_id == exclude[0] and exclude[1] <= m.start() < exclude[2]:
                    continue
                hits.append((d.doc_id, m.start(), m.end()))
    window = [start or "open", end or "open"]
    n_all, n_in = len(docs), len(in_window)
    gap = {
        "id": _gid(sorted(terms), window), "sought": terms, "search_scope": sorted(d.doc_id for d in docs),
        "date_window": window, "date_window_basis": basis,
        "coverage": {"documents_searched": n_all, "documents_in_window": n_in},
        "method": "case-insensitive whole-word text search, v1",
    }
    if hits:
        gap["result"] = "FOUND"
        gap["hits"] = hits
    elif n_in == 0:
        gap["result"] = "NO_RECORDS_IN_WINDOW"
        gap["rendered"] = f"No records from that period were provided ({n_all} documents searched, 0 in window)."
    else:
        span = f"{window[0]} to {window[1]}" if start and end else "the window"
        gap["result"] = "NOT_FOUND"
        gap["rendered"] = (f"Not found in the provided records searched ({n_all} documents searched, "
                           f"{n_in} dated within {span}).")
    return gap


def planned_item_gaps(floats: List[dict], docs: List[RecordDoc]) -> Tuple[List[dict], List[dict]]:
    """For each planned test/action, look for its result after the plan date."""
    gaps, flags = [], []
    latest = max((d.date for d in docs if d.date), default=None)
    for f in floats:
        if f["kind"] != "ACTION" or f.get("action_state") != "PLANNED":
            continue
        canon = lexicon.analyte_of(f["concept"]) or f["concept"]
        terms = [canon] + [a.upper() if len(a) <= 4 else a for a in lexicon.aliases_of(canon)]
        terms = list(dict.fromkeys(t for t in terms if t))
        p = f["provenance"]
        g = search(docs, terms, f["event_date"]["value"], latest,
                   exclude=(p["document_id"], p["char_start"], p["char_end"]),
                   basis="from plan date to latest record date")
        g["sought"] = [t for t in dict.fromkeys([canon] + [a for a in lexicon.aliases_of(canon) if a != canon.lower()])]
        g["sought"] = [g["sought"][0]] + [s.upper() if len(s) <= 4 else s for s in g["sought"][1:]]
        if g["result"] == "FOUND":
            continue
        gaps.append(g)
        flags.append({"id": _fid("EXPECTED_ITEM_NOT_FOUND", f["id"]), "type": "EXPECTED_ITEM_NOT_FOUND",
                      "cites": [f["id"], g["id"]],
                      "rendered": (f"A plan to '{f['original_text']}' is documented ({f['event_date']['value']}); "
                                   "no corresponding result was found in the records searched.")})
    return gaps, flags


def result_flags(floats: List[dict], mixes: List[dict], docs: List[RecordDoc]) -> Tuple[List[dict], List[dict]]:
    flags, not_raised = [], []
    by_id = {f["id"]: f for f in floats}
    for f in floats:
        if f["kind"] not in ("MEASUREMENT", "INTERPRETATION"):
            continue
        own = f["provenance"]["document_id"]
        own_date = f["_doc_date"] or ""
        later = [d for d in docs if d.doc_id != own and (d.date or "") > own_date]
        aliases = lexicon.aliases_of(f["value"]["analyte"]) if f.get("value") else [f["concept"]]
        referenced = any(re.search(rf"(?<![A-Za-z0-9]){re.escape(a)}(?![A-Za-z0-9])", d.text, re.I)
                         for d in later for a in aliases)
        if not referenced:
            flags.append({"id": _fid("UNREVIEWED_RESULT", f["id"]), "type": "UNREVIEWED_RESULT", "cites": [f["id"]],
                          "rendered": (f"{f['value']['analyte'] if f.get('value') else f['concept']} result "
                                       f"(report dated {own_date}) is not referenced by any later document in the records provided."),
                          "rule": "referenced = a later-dated document names the analyte or an alias (v1)"})
    for m in mixes:
        if m["relation"] != "LAB_CHANGED_FROM_PRIOR":
            continue
        a, b = by_id[m["endpoints"][0]], by_id[m["endpoints"][1]]
        ra, rb = a.get("range_comparison", {}).get("result"), b.get("range_comparison", {}).get("result")
        if ra and rb and (ra == "WITHIN") != (rb == "WITHIN"):
            flags.append({"id": _fid("UNEXPLAINED_MEASUREMENT_CHANGE", m["id"]), "type": "UNEXPLAINED_MEASUREMENT_CHANGE",
                          "cites": [m["id"]], "rendered": "A result moved across its reference range; check whether a later note addresses it."})
        else:
            not_raised.append({"type": "UNEXPLAINED_MEASUREMENT_CHANGE", "cites": [m["id"]],
                               "reason": f"Does not cross a reference range: results are {ra} and {rb} their reported ranges."})
    return flags, not_raised


def period_start(event_date: dict) -> Optional[str]:
    """First day of the stated period ('2022-12 (early)' -> 2022-12-01)."""
    v = event_date.get("value")
    if not v:
        return None
    m = re.match(r"(\d{4})(?:-(\d{2}))?(?:-(\d{2}))?", v)
    y, mo, d = int(m.group(1)), int(m.group(2) or 1), int(m.group(3) or 1)
    return date(y, mo, d).isoformat()


def day_before(iso: str) -> str:
    return (date.fromisoformat(iso) - timedelta(days=1)).isoformat()
