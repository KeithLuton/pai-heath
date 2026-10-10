"""Context classifier — MEDICAL_PRIMITIVES.md §2.4 rule 5, §2.8.

Assigns the four context axes to a target span inside its clause:

    polarity     AFFIRMED | NEGATED
    certainty    DEFINITE | HEDGED | HYPOTHETICAL
    temporality  CURRENT | HISTORICAL | PLANNED | UNSPECIFIED
    experiencer  PATIENT | FAMILY | OTHER

ConText-style: trigger phrases act forward (pre) or backward (post) on the
target within the clause; scope ends at a termination term. Pseudo-triggers
("no improvement", "gram negative", "than prior") are matched first and
neutralize the trigger words inside them.

Axes come from words in the text, never from a section label (§2.4 rule 5),
with three explicit exceptions where the label itself says it in words:
"Plan:" (planned), "FH:"/"Family history:" (family), and history-list sections
(PMH/PSH/FH) defaulting temporality to UNSPECIFIED rather than CURRENT — a
default of "not known", never HISTORICAL.

Every axis value carries the trigger that produced it, so a classification
can be audited; conflicting evidence goes to `review`, never silently resolved.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from .segment import Statement

# --- trigger lexicons: (pattern, direction) where direction is "pre", "post" or "any"
NEGATION = [
    (r"\bdenies\b|\bdenied\b", "pre"),
    (r"\bnegative for\b", "pre"),
    (r"\bno evidence of\b|\bwithout evidence of\b|\bno signs? of\b|\bfree of\b|\babsence of\b", "pre"),
    (r"\bnot consistent with\b|\bnot suggestive of\b|\bnot seen\b(?= )", "pre"),
    (r"\bnever had\b|\bnever\b", "pre"),
    (r"\bno\b|\bnot\b|\bwithout\b|\bnone\b", "pre"),
    (r"\bnot drawn\b|\bnot done\b|\bnot performed\b|\bnot obtained\b", "any"),
    (r"\babsent\b|\bnot present\b|\bnegative\b|\bnot seen\b|\bruled out\b|\bresolved negative\b|\bdenied\b", "post"),
    (r"\bunlikely\b", "any"),
]
PSEUDO = [
    r"\bno (?:significant |further |interval )?(?:improvement|change|increase|decrease|worsening)\b",
    r"\bnot only\b", r"\bnot necessarily\b", r"\bgram[- ]negative\b", r"\bno longer\b(?= improving)",
    r"\bthan prior\b", r"\bthan before\b", r"\bnot certain\b(?= whether)",
]
HYPOTHETICAL = [
    (r"\brule out\b|\br/o\b|\bto rule out\b", "pre"),
    (r"\bconcern for\b|\bconcerning for\b|\bevaluate for\b|\beval for\b|\bassess for\b|\bscreen for\b", "pre"),
    (r"\bcannot exclude\b|\bcan't exclude\b|\bcannot rule out\b", "pre"),
    (r"\bif\b|\bshould\b(?=.*\b(?:develop|occur|recur|return)s?\b)", "pre"),
    (r"\bversus\b|\bvs\.?\b", "any"),
    (r"\bon (?:the )?differential\b|\bremains on (?:the )?differential\b|\bin the differential\b|\bdifferential includes\b", "any"),
    (r"\?", "any"),
]
HEDGED = [
    (r"\bunlikely\b", "any"),
    (r"\blikely\b|\bprobable\b|\bprobably\b|\bpossible\b|\bpossibly\b|\bsuspected\b|\bsuspect\b", "any"),
    (r"\bsuggestive of\b|\bconsistent with\b|\bc/w\b|\bmay represent\b|\bfavor(?:s|ed)?\b|\bpresumed\b", "pre"),
]
HISTORICAL = [
    (r"\bhx of\b|\bh/o\b|\bhistory of\b|\bs/p\b|\bstatus post\b|\bremote\b", "pre"),
    (r"\bprior\b|\bprevious(?:ly)?\b|\bin the past\b|\bformerly\b", "pre"),
    (r"\bresolved\b|\b\d+\s+(?:days?|weeks?|months?|years?)\s+ago\b|\bin (?:19|20)\d\d\b", "any"),
]
PLANNED = [
    (r"\bwill\b|\bplan(?:s|ned)? to\b|\bto start\b|\bto begin\b|\bschedule(?:d)?\b", "pre"),
]
FAMILY = r"\b(?:mother|father|mom|dad|brother|sister|sibling|son|daughter|aunt|uncle|grandmother|grandfather|grandparent|cousin|maternal|paternal|family history|fhx)\b"
OTHER_PERSON = r"\b(?:wife|husband|spouse|partner|roommate|coworker|friend)\b"
AFFIRM_LIST = r"\bpositive for\b|\bendorses\b|\breports\b|\bcomplains of\b|\bc/o\b"

TERMINATION = re.compile(r"\b(?:but|however|though|although|except|aside from|apart from|other than|which|who|presents|presenting|reports|states|endorses)\b", re.I)
FAMILY_PAST = re.compile(r"\b(?:had|died of|passed away from|history of)\b", re.I)


@dataclass(frozen=True)
class Trigger:
    axis: str
    value: str
    text: str
    start: int
    end: int
    direction: str


@dataclass
class ContextResult:
    polarity: str = "AFFIRMED"
    certainty: str = "DEFINITE"
    temporality: str = "CURRENT"
    experiencer: str = "PATIENT"
    triggers: List[Trigger] = field(default_factory=list)
    review: List[str] = field(default_factory=list)


def _pseudo_spans(doc: str, a: int, b: int) -> List[Tuple[int, int]]:
    return [(a + m.start(), a + m.end()) for p in PSEUDO for m in re.finditer(p, doc[a:b], re.I)]


def _in(spans: List[Tuple[int, int]], s: int, e: int) -> bool:
    return any(x <= s and e <= y for x, y in spans)


def _scoped(doc: str, clause_start: int, clause_end: int, t_start: int, t_end: int, trig_start: int,
            trig_end: int, direction: str) -> bool:
    """True if a trigger at [trig_start, trig_end) applies to target [t_start, t_end)."""
    if trig_end <= t_start and direction in ("pre", "any"):
        between = doc[trig_end:t_start]
        return not TERMINATION.search(between)
    if trig_start >= t_end and direction in ("post", "any"):
        between = doc[t_end:trig_start]
        return not TERMINATION.search(between)
    if trig_start < t_end and trig_end > t_start and direction == "any":
        return True                              # trigger inside the target span (e.g. "?" or "resolved")
    return False


def _find(doc: str, lex, axis: str, value: str, a: int, b: int, pseudo) -> List[Trigger]:
    out = []
    for pattern, direction in lex:
        for m in re.finditer(pattern, doc[a:b], re.I):
            s, e = a + m.start(), a + m.end()
            if _in(pseudo, s, e):
                continue
            out.append(Trigger(axis, value, doc[s:e], s, e, direction))
    return out


def classify(doc: str, st: Statement, target: Optional[Tuple[int, int]] = None) -> ContextResult:
    """Classify `target` (default: the statement span) within its clause."""
    a, b = st.clause.start, st.clause.end
    t_start, t_end = target or (st.span.start, st.span.end)
    r = ContextResult()
    pseudo = _pseudo_spans(doc, a, b)

    # Search window: the clause, plus a governing list trigger if it sits outside the item.
    def applies(tr: Trigger) -> bool:
        if st.list_trigger and tr.start >= st.list_trigger.start and tr.end <= st.list_trigger.end:
            return True                          # "Denies" governs every item in its list
        if target is None and st.span.start <= tr.start and tr.end <= st.span.end:
            return True                          # statement-level: triggers inside the statement apply
        return _scoped(doc, a, b, t_start, t_end, tr.start, tr.end, tr.direction)

    neg = [t for t in _find(doc, NEGATION, "polarity", "NEGATED", a, b, pseudo) if applies(t)]
    # Plain "no/not/without" only act on what immediately follows (within 4 tokens),
    # so "has no insurance and reports numbness" doesn't negate numbness.
    neg = [t for t in neg if not (re.fullmatch(r"no|not|without|none", t.text, re.I) and not st.list_trigger
                                  and len(doc[t.end:t_start].split()) > 4)]
    affirm = [Trigger("polarity", "AFFIRMED", m.group(0), a + m.start(), a + m.end(), "pre")
              for m in re.finditer(AFFIRM_LIST, doc[a:b], re.I)]
    if neg:
        r.polarity = "NEGATED"
        r.triggers.append(neg[0])
        lead = doc[st.span.start:neg[0].start].strip()
        if target is None and st.span.start <= neg[0].start and lead and not st.list_trigger:
            r.review.append("negation inside a mixed statement; classify each concept with a target span")
        if any(applies(t) and t.end <= t_start and t.start > neg[0].end for t in affirm):
            r.review.append("negation and affirmation triggers both scope this target")

    # A certainty phrase that is part of a negation phrase ("not consistent with")
    # belongs to the negation, not to certainty.
    def inside_longer_negation(t: Trigger) -> bool:
        return any(n.start <= t.start and t.end <= n.end and (n.end - n.start) > (t.end - t.start) for n in neg)
    hyp = [t for t in _find(doc, HYPOTHETICAL, "certainty", "HYPOTHETICAL", a, b, pseudo)
           if applies(t) and not inside_longer_negation(t)]
    hed = [t for t in _find(doc, HEDGED, "certainty", "HEDGED", a, b, pseudo)
           if applies(t) and not inside_longer_negation(t)]
    if hyp:
        r.certainty = "HYPOTHETICAL"
        r.triggers.append(hyp[0])
    elif hed:
        r.certainty = "HEDGED"
        r.triggers.append(hed[0])

    # Experiencer: family words in the clause, or an explicit family-history header.
    fam = [Trigger("experiencer", "FAMILY", m.group(0), a + m.start(), a + m.end(), "any")
           for m in re.finditer(FAMILY, doc[a:b], re.I)]
    oth = [Trigger("experiencer", "OTHER", m.group(0), a + m.start(), a + m.end(), "any")
           for m in re.finditer(OTHER_PERSON, doc[a:b], re.I)]
    if fam and oth:
        r.review.append("both family and other-person terms in clause")
    if fam:
        r.experiencer = "FAMILY"
        r.triggers.append(fam[0])
    elif oth:
        r.experiencer = "OTHER"
        r.triggers.append(oth[0])
    elif st.section == "FH":
        r.experiencer = "FAMILY"
        r.triggers.append(Trigger("experiencer", "FAMILY", "FH:", -1, -1, "header"))

    hist = [t for t in _find(doc, HISTORICAL, "temporality", "HISTORICAL", a, b, pseudo) if applies(t)]
    plan = [t for t in _find(doc, PLANNED, "temporality", "PLANNED", a, b, pseudo) if applies(t)]
    if hist and plan:
        r.review.append("historical and planned triggers both scope this target")
    if hist:
        r.temporality = "HISTORICAL"
        r.triggers.append(hist[0])
    elif plan:
        r.temporality = "PLANNED"
        r.triggers.append(plan[0])
    elif st.section == "PLAN":
        r.temporality = "PLANNED"
        r.triggers.append(Trigger("temporality", "PLANNED", "Plan:", -1, -1, "header"))
    elif r.experiencer == "FAMILY" and FAMILY_PAST.search(doc[a:b]):
        m = FAMILY_PAST.search(doc[a:b])
        r.temporality = "HISTORICAL"
        r.triggers.append(Trigger("temporality", "HISTORICAL", m.group(0), a + m.start(), a + m.end(), "any"))
    elif st.section in ("PMH", "PSH", "FH"):
        r.temporality = "UNSPECIFIED"      # history list: not known, never assumed HISTORICAL
    return r
