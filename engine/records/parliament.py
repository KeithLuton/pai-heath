"""Parliament — Stir evaluation (§9, §10). An evidence map, never a verdict.

Seats (each a deterministic rule set; no model takes a seat):
  CLERK            records the opening verbatim and fixes the subject
  PRECEDENT JUDGE  builds the mechanism chain; every link needs a source
                   (attached authority, or the clinician's own words) — §10.5
  ADVOCATE         SUPPORT: authority links and record evidence for chain links;
                   never the subject itself (§10.1 rule 2)
  OPPOSITION       CHALLENGE: the 7-point checklist, each answered or recorded
                   as unaddressed (§10.2)
  TIMEKEEPER       timing: what the dates can and cannot order
  GAP FINDER       declared searches for what's missing (§6)
  RECORDER         what every seat cited; nothing uncited survives (§10.1 rule 1)

Authorities arrive with declared links ({"from", "to"}) from the retrieval
layer; this module never reads authority prose to infer what it says.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from . import lexicon
from .gaps import day_before, period_start, search


def _overlap(a: str, b: str) -> bool:
    ta, tb = lexicon.core_terms(a), lexicon.core_terms(b)
    return bool(ta & tb)


def _canonical(floats: List[dict]) -> List[dict]:
    first: Dict[str, dict] = {}
    for f in sorted(floats, key=lambda x: (x["_doc_date"] or "", x["id"])):
        first.setdefault(f["root"], f)
    return [f for f in floats if first[f["root"]] is f]


def evaluate(question: str, opener: str, floats: List[dict], mixes: List[dict], refused: List[dict],
             docs, authorities: List[dict]) -> Tuple[dict, List[dict]]:
    """Return (stir map, gaps created by the Parliament)."""
    canon = _canonical(floats)
    by_id = {f["id"]: f for f in floats}
    qterms = lexicon.core_terms(question)

    # ---------------- CLERK
    exposures = [f for f in floats if f["kind"] in ("MEDICATION_STATUS", "ACTION")
                 and lexicon.core_terms(f["concept"]) & qterms]
    findings = [f for f in floats if f["kind"] in ("REPORT", "OBSERVATION") and f["polarity"] == "AFFIRMED"
                and lexicon.core_terms(f["concept"]) & qterms]
    exposure_name = exposures[0]["concept"] if exposures else None
    finding_terms = sorted(set.union(*[lexicon.core_terms(f["concept"]) & qterms for f in findings])) if findings else []
    finding_name = " ".join(finding_terms) or None
    subject_ids = {f["id"] for f in exposures + findings}
    opening = {"opener": opener, "form": "QUESTION" if question.rstrip().endswith("?") else "STATEMENT",
               "verbatim": question}

    # ---------------- PRECEDENT JUDGE: chain
    chain: List[dict] = []
    used = set()
    current = exposure_name
    while current:
        nxt = next((a for a in authorities for l in a.get("links", []) if (a["id"], l["from"]) not in used
                    and _overlap(l["from"], current)), None)
        if not nxt:
            break
        link = next(l for l in nxt["links"] if _overlap(l["from"], current) and (nxt["id"], l["from"]) not in used)
        used.add((nxt["id"], link["from"]))
        chain.append({"from": link["from"], "to": link["to"], "sources": [{"type": "AUTHORITY", "id": nxt["id"]}]})
        current = link["to"]
        if finding_name and _overlap(current, finding_name):
            break
    # Clinician statements that name an intermediate already on the chain co-source the next link.
    assessments = [f for f in canon if f["kind"] == "ASSESSMENT" and f["polarity"] == "AFFIRMED"
                   and f["id"] not in subject_ids]
    clinician_sources = set()
    for link in chain:
        for f in assessments:
            if f["certainty"] in ("HYPOTHETICAL", "HEDGED") and _overlap(f["concept"], link["from"]) and \
                    not (exposure_name and _overlap(link["from"], exposure_name)):
                link["sources"].insert(0, {"type": "CLINICIAN", "id": f["id"]})
                clinician_sources.add(f["id"])
    for i, link in enumerate(chain):
        link["link"] = "L1" if exposure_name and _overlap(link["from"], exposure_name) else f"L{i + 1}"
        primary = link["sources"][0]
        link["source"] = dict(primary, **({"also": link["sources"][1]["id"]} if len(link["sources"]) > 1 else {}))
        link["record_evidence"] = []
        link["patient_specific_notes"] = []
        if primary["type"] == "CLINICIAN":
            link["patient_specific_notes"].append(
                f"Clinician lists {by_id[primary['id']]['concept']} as {by_id[primary['id']]['certainty'].lower()}; "
                "this is the link's source, not evidence it holds.")

    intermediates = [l["to"] for l in chain if not (finding_name and _overlap(l["to"], finding_name))]
    evidence: List[dict] = []
    for f in canon:
        if f["kind"] != "MEASUREMENT" or f["id"] in subject_ids:
            continue
        for link in chain:
            if lexicon.same_concept(f["value"]["analyte"], link["to"]) and f.get("range_comparison"):
                low_word = any(w in link["to"].lower() for w in ("low", "reduced", "decreased", "deficien"))
                high_word = any(w in link["to"].lower() for w in ("high", "elevated", "increased", "excess"))
                result = f["range_comparison"]["result"]
                if (low_word and result == "BELOW") or (high_word and result == "ABOVE"):
                    ref = f"{f['id']}.range_comparison"
                    link["record_evidence"].append(ref)
                    evidence.append({"cites": ref, "link": link["link"], "basis": "ENGINE_COMPUTED", "_float": f["id"]})
    for m in mixes:
        if m["relation"] == "LAB_CHANGED_FROM_PRIOR":
            for link in chain:
                if any(f"{e}.range_comparison" in link["record_evidence"] for e in m["endpoints"]):
                    link["record_evidence"].append(m["id"])
    for link in chain:
        if exposure_name and _overlap(link["from"], exposure_name):
            for r in refused:
                if r["relation"] == "TEMPORAL_PRECEDES" and r["endpoints"][0] in {f["id"] for f in exposures}:
                    link["patient_specific_notes"].append(
                        f"Order of {exposure_name} listing vs the first {by_id[r['endpoints'][1]]['concept']} result "
                        "cannot be established from the records.")
                    break
        link.pop("sources", None)
    # If an intermediate state is both an L1 target and an L2 source, record evidence serves both.
    if len(chain) >= 2:
        for e in evidence:
            e["link"] = "L1/L2 intermediate state"

    direct = next((a for a in authorities for l in a.get("links", [])
                   if exposure_name and finding_name and _overlap(l["from"], exposure_name) and _overlap(l["to"], finding_name)), None)
    direct_link = {"from": exposure_name, "to": finding_name, "source": direct["id"] if direct else None,
                   "note": ("An attached source addresses this directly." if direct else
                            f"No attached source addresses {exposure_name} and {finding_name} directly. "
                            "Chain links must not be combined into a patient-specific conclusion (§10.5).")}

    # ---------------- ADVOCATE
    support: List[dict] = []
    for link in chain:
        src = link["source"]
        if src["type"] == "AUTHORITY":
            support.append({"cites": src["id"], "link": link["link"], "level": "POPULATION"})
        elif src.get("also"):
            support.append({"cites": src["also"], "link": link["link"], "level": "POPULATION"})
    support += [{k: v for k, v in e.items() if not k.startswith("_")} for e in evidence]
    supported_floats = {e["_float"] for e in evidence}

    # ---------------- GAP FINDER + TIMEKEEPER
    gaps: List[dict] = []
    onset = next((f for f in sorted(findings, key=lambda x: x["_doc_date"] or "") if f["event_date"]["source"] == "DOCUMENTED"), None)
    pre_gap = None
    if onset and period_start(onset["event_date"]):
        alt_heads = [a["concept"].split()[-1] for a in assessments if a["id"] not in clinician_sources]
        terms = finding_terms + [lexicon.analyte_of(i) or i for i in intermediates] + alt_heads
        pre_gap = search(docs, terms, None, day_before(period_start(onset["event_date"])),
                         basis="before the stated symptom onset")
        if pre_gap["result"] != "FOUND":
            gaps.append(pre_gap)
    start_gap = None
    if exposure_name:
        started = [f for f in floats if f.get("medication", {}) and f["medication"]["drug"] == exposure_name
                   and f["medication"]["med_status"] in ("START_PLANNED", "PRESCRIBED", "ADMINISTERED")]
        latest = max((d.date for d in docs if d.date), default=None)
        start_gap = search(docs, [f"{exposure_name} started", f"started {exposure_name}", f"start {exposure_name}",
                                  f"began {exposure_name}", f"{exposure_name} prescribed", f"prescribed {exposure_name}"],
                           None, latest, basis=f"any record of when {exposure_name} began")
        if started:
            start_gap = None
        elif start_gap["result"] != "FOUND":
            start_gap["sought"] = [f"{exposure_name} start date", f"{exposure_name} prescribed", f"{exposure_name} started"]
            gaps.append(start_gap)

    # ---------------- OPPOSITION
    alternatives = [f for f in assessments if f["id"] not in clinician_sources]
    alternatives.sort(key=lambda f: (f["temporality"] != "CURRENT", f["id"]))
    chronic = [f for f in assessments if f["temporality"] in ("UNSPECIFIED", "HISTORICAL")]
    variability = [m["id"] for m in mixes if m["relation"] == "LAB_CHANGED_FROM_PRIOR"
                   and (m.get("comparability") or {}).get("class") != "COMPARABLE"]
    exposure_refusal = next((r for r in refused if r["relation"] == "SYMPTOM_REPORTED_AFTER_EXPOSURE"
                             and r["endpoints"][0] in {f["id"] for f in exposures}), None)
    negatives = [f for f in canon if f["kind"] in ("REPORT", "OBSERVATION") and f["polarity"] == "NEGATED"
                 and f["experiencer"] == "PATIENT"]
    natural = next((a["id"] for a in authorities if a.get("addresses") == "natural_history"), None)

    def item(n, name, cites=None, **extra):
        d = {"checklist": n, "item": name}
        if cites:
            d["cites"] = cites
        d.update(extra)
        return d

    challenge = [
        item(1, "Alternative explanations", [f["id"] for f in alternatives]) if alternatives
        else item(1, "Alternative explanations", status="NONE_FOUND_IN_RECORDS"),
        item(2, "Pre-existing state", [pre_gap["id"]]) if pre_gap and pre_gap["result"] != "FOUND"
        else item(2, "Pre-existing state", status="NOT_ADDRESSED_NO_RECORD" if not pre_gap else "RECORDS_EXIST",
                  **({"cites": [h[0] for h in pre_gap["hits"]]} if pre_gap else {})),
        item(3, "Measurement variability", variability) if variability
        else item(3, "Measurement variability", status="NOT_APPLICABLE_NO_REPEATED_MEASURES"),
        item(4, "Natural history / regression to the mean", [natural]) if natural
        else item(4, "Natural history / regression to the mean", status="NOT_ADDRESSED_NO_SOURCE"),
        item(5, "Concurrent conditions", [f["id"] for f in chronic]) if chronic
        else item(5, "Concurrent conditions", status="NONE_FOUND_IN_RECORDS"),
        (item(6, "Timing", [start_gap["id"]],
              note=("Records do not establish that the exposure preceded the symptom (order indeterminate)."
                    if exposure_refusal else None)) if start_gap
         else item(6, "Timing", status="ORDER_INDETERMINATE" if exposure_refusal else "SEE_TEMPORAL_MIXES",
                   note="Exposure start is documented; see temporal relations." if exposures else None)),
        item(7, "Negative findings", [f["id"] for f in negatives], bearing="UNDETERMINED",
             status="NOT_ADDRESSED_NO_SOURCE",
             note="Listed; no attached source establishes their relevance to the question.") if negatives
        else item(7, "Negative findings", status="NONE_FOUND_IN_RECORDS"),
    ]
    for c in challenge:
        if c.get("note") is None:
            c.pop("note", None)

    # ---------------- RECORDER: unplaced and the citation rule
    cited = {c for i in support for c in ([i["cites"]] if isinstance(i["cites"], str) else i["cites"])}
    cited |= {c for i in challenge for c in i.get("cites", [])}
    cited_floats = {c.split(".")[0] for c in cited} | supported_floats | clinician_sources
    unplaced = []
    for f in canon:
        if f["id"] in subject_ids or f["id"] in cited_floats:
            continue
        if f["kind"] in ("OBSERVATION", "REPORT", "MEASUREMENT") and f["polarity"] == "AFFIRMED":
            what = f"{f['id']}.range_comparison" if f["kind"] == "MEASUREMENT" and f.get("range_comparison") else f["id"]
            unplaced.append({"cites": what, "reason": (
                "No attached source establishes whether this bears on the question or its alternatives; "
                "placing it would require uncited knowledge (§10.1).")})
    for side in (support, challenge):
        for i in side:
            assert i.get("cites") or i.get("status"), f"uncited item {i}"   # §10.1 rule 1

    statuses = [by_id[c.split(".")[0]]["content_status"] for c in cited if c.split(".")[0] in by_id]
    order = ["UNVERIFIED", "SPECULATIVE", "EXPLAINED", "VALIDATED"]
    stir = {
        "opening": opening,
        "subject": {"exposure": sorted(f["id"] for f in exposures), "finding": sorted(f["id"] for f in findings),
                    "rule": "Subject Floats never appear in SUPPORT (§10.1)."},
        "chain": chain, "direct_link": direct_link,
        "support": support, "challenge": challenge, "non_discriminating": [], "unplaced": unplaced,
        "content_status": min(statuses, key=order.index) if statuses else "UNVERIFIED",
        "seats": {
            "CLERK": {"opening": "verbatim", "subject_floats": len(subject_ids)},
            "PRECEDENT_JUDGE": {"links": [l["link"] for l in chain], "unsourced_links_added": 0},
            "ADVOCATE": {"items": len(support)},
            "OPPOSITION": {"checklist_items": len(challenge)},
            "TIMEKEEPER": {"exposure_before_symptom": "INDETERMINATE" if exposure_refusal else "SEE_MIXES"},
            "GAP_FINDER": {"gaps": [g["id"] for g in gaps]},
            "RECORDER": {"cited": sorted(cited), "verdict": None},
        },
    }
    return stir, gaps
