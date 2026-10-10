"""Mix builder — MEDICAL_PRIMITIVES.md §7.

Fact-to-fact relations only: sequence and quantity, never cause. Every
temporal Mix carries its resolution (§7.2); a relation whose order can't be
established is not created, and the refusal is recorded with its reason so the
Stir and the user can see why something is absent.

Copy-forward: Floats sharing a root are one source. Only the root's earliest
Float takes part in temporal relations, so a copied sentence never doubles a
sequence.
"""
from __future__ import annotations

import hashlib
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

from spigot.epifloat import EpiFloat

from . import dates, lexicon


def _mid(relation: str, *ends: str) -> str:
    return "M-" + hashlib.sha256(("|".join((relation,) + ends)).encode()).hexdigest()[:10]


def _date_basis(f: dict) -> str:
    e = f["event_date"]
    if e["source"] == "DOCUMENT_DATE_FALLBACK":
        return f"{f['id']} fallback {e['value']}"
    if e["precision"] == "APPROXIMATE":
        return f"{f['id']} APPROXIMATE {e.get('as_stated') or e['value']}"
    return f"{f['id']} {e['source']} {e['value']}"


def _floor(*fs: dict) -> str:
    order = ["UNVERIFIED", "SPECULATIVE", "EXPLAINED", "VALIDATED"]
    return min((f["content_status"] for f in fs), key=order.index)


def _is_symptom(f: dict) -> bool:
    return (f["kind"] in ("REPORT", "OBSERVATION") and f["polarity"] == "AFFIRMED"
            and f["experiencer"] == "PATIENT" and f["certainty"] == "DEFINITE")


def build(floats: List[dict]) -> Tuple[List[dict], List[dict]]:
    mixes: List[dict] = []
    refused: List[dict] = []
    by_root: Dict[str, List[dict]] = defaultdict(list)
    for f in floats:
        by_root[f["root"]].append(f)
    reps = {r: sorted(fs, key=lambda x: (x["_doc_date"] or "", x["id"]))[0] for r, fs in by_root.items()}
    canonical = [f for f in floats if reps[f["root"]] is f]

    # 1. SAME_ROOT
    for root, fs in by_root.items():
        if len(fs) > 1:
            first = reps[root]
            for other in fs:
                if other is not first:
                    mixes.append({"id": _mid("SAME_ROOT", first["id"], other["id"]), "relation": "SAME_ROOT",
                                  "endpoints": [first["id"], other["id"]],
                                  "evidence": {"span_tokens": len(first["original_text"].split()),
                                               "min_span_tokens": 8},
                                  "content_status": _floor(first, other),
                                  "rendered": "The same text appears in a later document; it counts as one source."})

    # 2. LAB_CHANGED_FROM_PRIOR (consecutive results of the same analyte)
    meas = [f for f in canonical if f["kind"] == "MEASUREMENT"]
    by_analyte: Dict[str, List[dict]] = defaultdict(list)
    for f in meas:
        by_analyte[f["value"]["analyte"]].append(f)
    for analyte, fs in by_analyte.items():
        fs.sort(key=lambda x: (x["_doc_date"] or "", x["id"]))
        for a, b in zip(fs, fs[1:]):
            mixes_or_ref = _lab_change(analyte, a, b)
            (mixes if mixes_or_ref.get("relation") else refused).append(mixes_or_ref)
            refused.append({"relation": "INDEPENDENT_AGREEMENT", "endpoints": [a["id"], b["id"]],
                            "reason": "Separate measurements, not corroboration (§4.3)."})

    # 3. DIAGNOSIS_RECORDED_AFTER_SYMPTOM
    symptoms = [f for f in canonical if _is_symptom(f) and f["kind"] == "REPORT"]
    assessments = [f for f in canonical if f["kind"] in ("ASSESSMENT", "CODE") and f["polarity"] == "AFFIRMED"]
    for s in symptoms:
        for d in assessments:
            res = dates.resolve(s["_date"], d["_date"])
            if res["temporal_resolution"] == "INDETERMINATE":
                continue          # unknown or same-encounter order: no relation (not every pair is worth a refusal entry)
            mixes.append(_temporal_mix("DIAGNOSIS_RECORDED_AFTER_SYMPTOM", s, d, res,
                                       f"{d['concept'].capitalize()} was documented after {s['concept']}"))

    # 4. SYMPTOM_REPORTED_AFTER_EXPOSURE (medication/action -> symptom)
    exposures = [f for f in canonical if f["kind"] in ("MEDICATION_STATUS", "ACTION") and f["temporality"] != "PLANNED"]
    for e in exposures:
        for s in symptoms:
            res = dates.resolve(e["_date"], s["_date"])
            if res["temporal_resolution"] == "INDETERMINATE":
                refused.append({"relation": "SYMPTOM_REPORTED_AFTER_EXPOSURE", "endpoints": [e["id"], s["id"]],
                                "reason": f"INDETERMINATE: {_date_basis(e)}; {_date_basis(s)}. Order cannot be established (§7.2)."})
            else:
                mixes.append(_temporal_mix("SYMPTOM_REPORTED_AFTER_EXPOSURE", e, s, res,
                                           f"{s['concept'].capitalize()} was documented after {e['concept']}"))
        for m in meas:
            if m["provenance"]["document_id"] == e["provenance"]["document_id"]:
                res = dates.resolve(e["_date"], m["_date"])
                if res["temporal_resolution"] == "INDETERMINATE":
                    refused.append({"relation": "TEMPORAL_PRECEDES", "endpoints": [e["id"], m["id"]],
                                    "reason": "Both dated only by the same document; their order cannot be established."})

    # 5. TREATMENT_FOLLOWED_DIAGNOSIS
    plans = [f for f in canonical if f["kind"] in ("MEDICATION_STATUS", "ACTION")]
    for d in assessments:
        for p in plans:
            if p is d:
                continue
            res = dates.resolve(d["_date"], p["_date"])
            if res["temporal_resolution"] != "INDETERMINATE":
                mixes.append(_temporal_mix("TREATMENT_FOLLOWED_DIAGNOSIS", d, p, res,
                                           f"{p['concept'].capitalize()} was documented after {d['concept']}"))
            elif d["provenance"]["document_id"] == p["provenance"]["document_id"] and d["_date"].source == "ENCOUNTER_DATE":
                refused.append({"relation": "TREATMENT_FOLLOWED_DIAGNOSIS", "endpoints": [d["id"], p["id"]],
                                "reason": "Same encounter; position within a note is not temporal order."})

    # 6. Agreement between independent sources (same referent only)
    _agreement(canonical, mixes, refused)
    return mixes, refused


def _temporal_mix(relation: str, a: dict, b: dict, res: dict, sentence: str) -> dict:
    t = dict(res)
    t["date_basis"] = [_date_basis(a), _date_basis(b)]
    if res["temporal_resolution"] == "EXACT":
        rendered = f"{sentence} ({res['delta_days']} days later)."
    elif res["temporal_resolution"] == "RANGE":
        rendered = f"{sentence}, between {res['delta_days_min']} and {res['delta_days_max']} days later."
    else:
        rendered = f"{sentence}; the interval cannot be determined from the records."
    return {"id": _mid(relation, a["id"], b["id"]), "relation": relation, "endpoints": [a["id"], b["id"]],
            "temporal": t, "rendered": rendered, "content_status": _floor(a, b)}


def _lab_change(analyte: str, a: dict, b: dict) -> dict:
    va, vb = a["value"], b["value"]
    res = dates.resolve(a["_date"], b["_date"], (a["_doc_date"] or "", b["_doc_date"] or ""))
    if res["temporal_resolution"] == "INDETERMINATE":
        return {"relation": "LAB_CHANGED_FROM_PRIOR", "endpoints": [a["id"], b["id"]],
                "reason": "Order of the two results cannot be established (§7.2)."}
    conv = lexicon.conversion(analyte, va["unit"] or "", vb["unit"] or "")
    if conv is None:
        return {"relation": "LAB_CHANGED_FROM_PRIOR", "endpoints": [a["id"], b["id"]],
                "reason": f"NOT_COMPARABLE: no declared conversion from {va['unit']} to {vb['unit']} for {analyte} (§5.2)."}
    factor, basis = conv
    k = EpiFloat.structural(factor)
    prior = EpiFloat.unverified(va["numeric"], a["id"]) * k           # status floors, never launders
    current = EpiFloat.unverified(vb["numeric"], b["id"])
    delta = current - prior
    reasons = []
    if va["unit"] != vb["unit"]:
        reasons.append("units differ (declared structural conversion applied)")
    if not va["performing_lab"] or not vb["performing_lab"] or va["performing_lab"] != vb["performing_lab"]:
        reasons.append("performing laboratory not stated for either result" if not (va["performing_lab"] or vb["performing_lab"])
                       else "performing laboratory differs or is not stated")
    ref_prior = None
    if va["reference_low"] is not None and va["reference_high"] is not None:
        ref_prior = [round(va["reference_low"] * factor, 1), round(va["reference_high"] * factor, 1)]
        if vb["reference_low"] is not None and [vb["reference_low"], vb["reference_high"]] != ref_prior:
            reasons.append("reference ranges differ after conversion")
    cls = "CAUTION" if any("laboratory" in r for r in reasons) else ("CONVERTIBLE" if va["unit"] != vb["unit"] else "COMPARABLE")
    pv, cv, dv = round(float(prior), 1), round(float(current), 1), round(float(delta), 1)
    direction = "INCREASE" if dv > 0 else "DECREASE" if dv < 0 else "NO_CHANGE"
    t = dict(res)
    t["date_basis"] = [_date_basis(a), _date_basis(b)]
    word = {"INCREASE": "higher", "DECREASE": "lower", "NO_CHANGE": "the same"}[direction]
    basis_text = "based on the order of the reports" if res.get("order_basis") == "REPORT_SEQUENCE" else "based on their dates"
    return {
        "id": _mid("LAB_CHANGED_FROM_PRIOR", a["id"], b["id"]), "relation": "LAB_CHANGED_FROM_PRIOR",
        "endpoints": [a["id"], b["id"]], "temporal": t,
        "comparability": {"class": cls, "reasons": reasons},
        "conversion": {"factor": factor, "unit": f"{vb['unit']} per {va['unit']}", "basis": basis,
                       "entered_as": "EpiFloat.structural", "result_floor": prior.floor.name} if va["unit"] != vb["unit"] else None,
        "values": {"unit": vb["unit"], "prior_converted": pv, "current": cv},
        "reference_ranges": {"prior_converted": ref_prior,
                             "current": [vb["reference_low"], vb["reference_high"]]},
        "direction": direction, "delta": dv, "delta_percent": round(dv / pv * 100, 1) if pv else None,
        "rendered": (f"{analyte[0].upper() + analyte[1:]} was documented after the earlier result ({basis_text}); "
                     f"after unit conversion the later value is numerically {word}." if va["unit"] != vb["unit"] else
                     f"{analyte[0].upper() + analyte[1:]} was documented after the earlier result ({basis_text}); "
                     f"the later value is numerically {word}.") + (" Comparability is limited." if cls == "CAUTION" else ""),
        "content_status": _floor(a, b),
    }


def _agreement(canonical: List[dict], mixes: List[dict], refused: List[dict]) -> None:
    groups: Dict[Tuple[str, str], List[dict]] = defaultdict(list)
    for f in canonical:
        if f["kind"] in ("REPORT", "OBSERVATION", "ASSESSMENT"):
            groups[(f["concept"], f["experiencer"])].append(f)
    for (_, _), fs in groups.items():
        for i, a in enumerate(fs):
            for b in fs[i + 1:]:
                if a["provenance"]["document_id"] == b["provenance"]["document_id"]:
                    continue
                same_referent = (a["temporality"] != "CURRENT" or a["_date"].as_dict() == b["_date"].as_dict())
                if not same_referent:
                    refused.append({"relation": "INDEPENDENT_AGREEMENT", "endpoints": [a["id"], b["id"]],
                                    "reason": "Different referents: current-state statements at two separate visits are two facts, not corroboration (§4.3)."})
                    continue
                rel = "INDEPENDENT_AGREEMENT" if a["polarity"] == b["polarity"] else "INDEPENDENT_CONFLICT"
                mixes.append({"id": _mid(rel, a["id"], b["id"]), "relation": rel, "endpoints": [a["id"], b["id"]],
                              "content_status": _floor(a, b),
                              "rendered": ("Two independent records say the same thing." if rel == "INDEPENDENT_AGREEMENT"
                                           else "Two independent records disagree.")})
