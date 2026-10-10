"""Record evidence pipeline: documents + question -> the UI contract's Scenario shape."""
from __future__ import annotations

from typing import Callable, List, Optional

from . import floats as F
from . import gaps as G
from . import mixes as M
from . import parliament as P
from . import render as R


def run(docs: List[F.RecordDoc], question: Optional[str] = None, opener: str = "USER",
        authorities: Optional[List[dict]] = None,
        retrieve: Optional[Callable[[List[dict], Optional[str]], List[dict]]] = None) -> dict:
    """retrieve(floats, question) -> authority inputs, e.g. KnowledgeBase lookups (knowledge/kb.py)."""
    authorities = list(authorities or [])
    fl, unextracted = F.extract(docs)
    if retrieve is not None and question:
        authorities += retrieve(fl, question)
    mx, refused = M.build(fl)
    plan_gaps, flags = G.planned_item_gaps(fl, docs)
    rflags, not_raised = G.result_flags(fl, mx, docs)
    flags += rflags
    out = {
        "fixture_id": "LIVE_RECORD_SET",
        "verification": {"authored_by": "PAi Health engine (deterministic rules)", "verified_by": None,
                         "rule": "Engine output from your documents; statuses never exceed what the records support."},
        "constitution_version": "MEDICAL_PRIMITIVES v0.4.4",
        "documents": {d.doc_id: {"date": d.date, "title": d.title, "text": d.text, "sha256": d.sha256} for d in docs},
        "authority_inputs": authorities,
        "expected": {
            "floats": [F.public(f) for f in fl], "mixes": mx, "mixes_not_created": refused,
            "gaps": plan_gaps, "flags": flags, "flags_not_raised": not_raised, "unextracted": unextracted,
        },
    }
    if question:
        stir, stir_gaps = P.evaluate(question, opener, fl, mx, refused, docs, authorities)
        out["expected"]["gaps"] = plan_gaps + stir_gaps
        out["expected"]["stir"] = stir
        out["expected"]["rendering"] = {"reference_text": R.render(stir, fl, mx, plan_gaps + stir_gaps, authorities, docs)}
    return out
