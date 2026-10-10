"""Renderer and lint (§12).

Every sentence is assembled from computed structures with fixed templates. A
model may later rephrase the output; the lint runs on whatever is displayed.

Lint: no causal verbs, no diagnosis/clearance words, no "different lab" when
the lab is unknown, no sharpened dates — except inside the user's verbatim
question. A violation raises; it is never silently fixed.
"""
from __future__ import annotations

import re
from typing import Dict, List

DISCLAIMER = ("PAi Health organizes your records, tracks your data, and offers general wellness suggestions. "
              "It does not diagnose, prescribe, or replace your clinician. Decisions belong to you and your "
              "licensed clinicians.")

FORBIDDEN = [
    r"\bcaus(?:e|es|ed|ing)\b", r"\bled to\b", r"\bdue to\b", r"\btriggered\b", r"\bresult(?:s|ed)? in\b",
    r"\bruled out\b", r"\bconfirmed\b", r"\bdiagnosed with\b", r"\byou have\b", r"\bnever had\b", r"\bdid not have\b",
    r"\bno history of\b", r"\bwas never done\b", r"\bdifferent labs?\b", r"\bsame lab\b",
    r"\bstarted metformin\b", r"\btaking\b", r"\bprescribed\b(?! date)", r"\bproves?\b", r"\bsafe\b",
]


class LintError(ValueError):
    pass


def lint(text: str, question: str) -> List[str]:
    body = text.replace(question, "[QUESTION]")
    return [p for p in FORBIDDEN if re.search(p, body, re.I)]


def _join(items: List[str]) -> str:
    items = [i for i in items if i]
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + (", and " if len(items) > 2 else " and ") + items[-1]


def _month_word(iso_prefix: str) -> str:
    import calendar
    y, m = iso_prefix[:4], int(iso_prefix[5:7])
    return f"{calendar.month_name[m]} {y}"


def render(stir: dict, floats: List[dict], mixes: List[dict], gaps: List[dict], authorities: List[dict],
           docs) -> str:
    by_id: Dict[str, dict] = {f["id"]: f for f in floats}
    gap_by_id = {g["id"]: g for g in gaps}
    mix_by_id = {m["id"]: m for m in mixes}
    q = stir["opening"]["verbatim"]
    out = [f'You asked: "{q}"', ""]

    exp = [by_id[i] for i in stir["subject"]["exposure"]]
    fnd = sorted((by_id[i] for i in stir["subject"]["finding"]), key=lambda f: f["_doc_date"] or "")
    record_bits = []
    listed = next((f for f in exp if f.get("medication", {}).get("med_status") == "LISTED"), None)
    if listed:
        record_bits.append(f"{listed['concept']} on your medication list")
    elif exp:
        record_bits.append(f"{exp[0]['concept']} in your records")
    if fnd:
        f = fnd[0]
        e = f["event_date"]
        when = f" reported as starting {e['as_stated']}" if e.get("as_stated") else ""
        record_bits.append(f"{f['concept']}{when}")
    ev_floats = [by_id[s["cites"].split(".")[0]] for s in stir["support"] if s.get("basis") == "ENGINE_COMPUTED"]
    if ev_floats:
        analyte = ev_floats[0]["value"]["analyte"]
        n = len(ev_floats)
        where = {"BELOW": "below", "ABOVE": "above"}[ev_floats[0]["range_comparison"]["result"]]
        count = "two" if n == 2 else str(n) if n > 2 else "a"
        noun = "results" if n > 1 else "result"
        record_bits.append(f"{count} {analyte} {noun} {where} the reference ranges reported with them" if n > 1
                           else f"a {analyte} result {where} the reference range reported with it")
    para = [f"Your records document {_join(record_bits)}."] if record_bits else []
    for m in mixes:
        if m["relation"] == "LAB_CHANGED_FROM_PRIOR" and set(m["endpoints"]) <= {f["id"] for f in ev_floats}:
            comp = m["comparability"]
            if "units differ (declared structural conversion applied)" in comp["reasons"]:
                para.append("The two results use different units; whether the laboratory or method was the same "
                            "could not be established from the records provided.")
            word = {"INCREASE": "slightly higher", "DECREASE": "slightly lower", "NO_CHANGE": "about the same"}[m["direction"]]
            para.append(f"After unit conversion, the later value is {word}, but comparability is limited."
                        if comp["class"] == "CAUTION" else f"The later value is {word}.")
    out += [" ".join(para), ""] if para else []

    timing = []
    six = next(c for c in stir["challenge"] if c["checklist"] == 6)
    if six.get("cites") and exp and fnd:
        timing.append(f"The records do not show when {exp[0]['concept']} was started, so whether it began before "
                      f"the {stir['direct_link']['to'] or fnd[0]['concept']} cannot be determined from these documents.")
    two = next(c for c in stir["challenge"] if c["checklist"] == 2)
    for gid in two.get("cites", []):
        g = gap_by_id.get(gid)
        if g and g["result"] == "NO_RECORDS_IN_WINDOW" and fnd and fnd[0]["event_date"].get("value"):
            timing.append(f"No records from before {_month_word(fnd[0]['event_date']['value'])} were provided.")
        elif g and g["result"] == "NOT_FOUND":
            timing.append("Nothing about this was found in earlier records provided.")
    if timing:
        out += [" ".join(timing), ""]

    auth_links = [l for l in stir["chain"] if l["source"]["type"] == "AUTHORITY" or l["source"].get("also")]
    if auth_links:
        rel = _join([f"between {l['from']} and {l['to']}" for l in auth_links])
        out += [f"Attached medical sources address proposed relationships {rel}. These sources describe groups of "
                "people and do not by themselves establish what is behind your symptoms.", ""]
    elif exp and fnd:
        out += [f"No medical reference sources are attached to this question, so PAi Health cannot show any "
                f"published relationship between {exp[0]['concept']} and {stir['direct_link']['to']}.", ""]

    clin = []
    one = next(c for c in stir["challenge"] if c["checklist"] == 1)
    alts = [by_id[i] for i in one.get("cites", [])]
    hedged = [a for a in alts if a["certainty"] == "HEDGED" and a["temporality"] == "CURRENT"]
    chain_clin = [by_id[l["source"]["id"]] for l in stir["chain"] if l["source"]["type"] == "CLINICIAN"]
    hyp = chain_clin + [a for a in alts if a["certainty"] == "HYPOTHETICAL"]
    if hedged or hyp:
        doc_title = next((d.title for d in docs if d.doc_id == (hedged or hyp)[0]["provenance"]["document_id"]), "A note")
        s = f"The {doc_title.lower()} lists {_join([h['concept'] for h in hedged])} as likely" if hedged else f"The {doc_title.lower()} lists"
        if hyp:
            s += (", with " if hedged else " ") + f"{_join([h['concept'] for h in hyp])} still on the differential"
        clin.append(s + ".")
    for gid, g in gap_by_id.items():
        if g.get("date_window_basis", "").startswith("from plan date") and g["result"] == "NOT_FOUND":
            clin.append(f"A planned {g['sought'][-1] if len(g['sought']) > 1 else g['sought'][0]} test was not found in the records provided.")
    if clin:
        out += [" ".join(clin), ""]

    qs = []
    for g in gaps:
        if g.get("date_window_basis", "").startswith("from plan date"):
            qs.append(f'"Was the {g["sought"][-1] if len(g["sought"]) > 1 else g["sought"][0]} test done, and what did it show?"')
    if six.get("cites") and exp and fnd:
        qs.append(f'"When did I start {exp[0]["concept"]}, compared with when the {stir["direct_link"]["to"]} began?"')
    if hedged and hyp:
        qs.append(f'"How would we tell {hedged[0]["concept"]} apart from {hyp[0]["concept"]} in my case?"')
    elif not auth_links and exp:
        qs.append(f'"{q}"' if q.endswith("?") else f'"{q}?"')
    if qs:
        out += ["Questions to bring to your clinician:"] + [f"- {x}" for x in qs] + [""]
    out.append(DISCLAIMER)
    text = "\n".join(out)
    bad = lint(text, q)
    if bad:
        raise LintError(f"rendering violates §12: {bad}")
    return text
