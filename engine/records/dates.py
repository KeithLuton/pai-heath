"""Event dates and temporal resolution (§2.2 event_date, §2.4 rule 10, §7.2).

A date is an interval with a precision and a source. Ordering two events is
only claimed when their intervals don't overlap; an exact day delta only when
both ends are documented single days. Fallback dates are upper bounds.
"""
from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional, Tuple

MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}
MONTHS.update({m.lower(): i for i, m in enumerate(calendar.month_abbr) if m})
APPROX = r"(?:around|about|approximately|approx\.?|roughly|circa|~)"
PART = r"(?:early|mid|middle of|late|end of|beginning of)"
_MONTH_NAMES = "|".join(sorted(MONTHS, key=len, reverse=True))

DATE_EXPR = re.compile(
    rf"(?P<approx>{APPROX}\s+)?(?P<part>{PART}[\s-]+)?"
    rf"(?:(?P<mname>{_MONTH_NAMES})\.?\s+(?:(?P<mday>\d{{1,2}}),?\s+)?(?P<myear>(?:19|20)\d\d)"
    rf"|(?P<iso>(?:19|20)\d\d-\d\d-\d\d)"
    rf"|(?P<us>\d{{1,2}}/\d{{1,2}}/(?:19|20)\d\d)"
    rf"|(?P<year>(?:19|20)\d\d))",
    re.I)
TOLERANCE_DAYS = 15        # [{calibrated}] widening for approximate dates when testing order


@dataclass(frozen=True)
class EventDate:
    value: Optional[str]
    precision: str                 # DAY | MONTH | YEAR | APPROXIMATE | UNKNOWN
    source: str                    # DOCUMENTED | ENCOUNTER_DATE | DOCUMENT_DATE_FALLBACK | NONE
    meaning: Optional[str] = None
    as_stated: Optional[str] = None
    lo: Optional[date] = None      # interval used for ordering (not exported)
    hi: Optional[date] = None

    def as_dict(self) -> dict:
        d = {"value": self.value, "precision": self.precision, "source": self.source}
        if self.meaning:
            d["meaning"] = self.meaning
        if self.as_stated:
            d["as_stated"] = self.as_stated
        return d


def _month_bounds(y: int, m: int, part: Optional[str]) -> Tuple[date, date]:
    last = calendar.monthrange(y, m)[1]
    p = (part or "").lower().strip()
    if p.startswith("early") or p.startswith("beginning"):
        return date(y, m, 1), date(y, m, 10)
    if p.startswith("mid") or p.startswith("middle"):
        return date(y, m, 11), date(y, m, 20)
    if p.startswith("late") or p.startswith("end"):
        return date(y, m, 21), date(y, m, last)
    return date(y, m, 1), date(y, m, last)


def parse_expression(text: str) -> Optional[EventDate]:
    """First date expression in text, at the precision stated. None if there is none."""
    m = DATE_EXPR.search(text)
    if not m:
        return None
    approx, part = m.group("approx"), m.group("part")
    stated = m.group(0).strip()
    if m.group("iso") or m.group("us") or m.group("mday"):
        if m.group("iso"):
            d = date.fromisoformat(m.group("iso"))
        elif m.group("us"):
            mo, dd, yy = map(int, m.group("us").split("/"))
            d = date(yy, mo, dd)
        else:
            d = date(int(m.group("myear")), MONTHS[m.group("mname").lower()], int(m.group("mday")))
        if approx:
            return EventDate(d.isoformat(), "APPROXIMATE", "DOCUMENTED", as_stated=stated,
                             lo=d - timedelta(days=TOLERANCE_DAYS), hi=d + timedelta(days=TOLERANCE_DAYS))
        return EventDate(d.isoformat(), "DAY", "DOCUMENTED", lo=d, hi=d)
    if m.group("mname"):
        y, mo = int(m.group("myear")), MONTHS[m.group("mname").lower()]
        lo, hi = _month_bounds(y, mo, part)
        value = f"{y}-{mo:02d}" + (f" ({re.split(r'[\s-]+', part.strip())[0].lower()})" if part else "")
        if approx or part:
            return EventDate(value, "APPROXIMATE", "DOCUMENTED", as_stated=stated,
                             lo=lo - timedelta(days=TOLERANCE_DAYS), hi=hi + timedelta(days=TOLERANCE_DAYS))
        return EventDate(value, "MONTH", "DOCUMENTED", lo=lo, hi=hi)
    y = int(m.group("year"))
    if approx:
        return EventDate(str(y), "APPROXIMATE", "DOCUMENTED", as_stated=stated,
                         lo=date(y - 1, 7, 1), hi=date(y + 1, 6, 30))
    return EventDate(str(y), "YEAR", "DOCUMENTED", lo=date(y, 1, 1), hi=date(y, 12, 31))


def encounter(doc_date: Optional[str]) -> EventDate:
    if not doc_date:
        return unknown()
    d = date.fromisoformat(doc_date)
    return EventDate(d.isoformat(), "DAY", "ENCOUNTER_DATE", lo=d, hi=d)


def fallback(doc_date: Optional[str]) -> EventDate:
    if not doc_date:
        return unknown()
    d = date.fromisoformat(doc_date)
    return EventDate(d.isoformat(), "DAY", "DOCUMENT_DATE_FALLBACK", meaning="on or before", lo=None, hi=d)


def unknown() -> EventDate:
    return EventDate(None, "UNKNOWN", "NONE")


def resolve(a: EventDate, b: EventDate, report_sequence: Optional[Tuple[str, str]] = None) -> dict:
    """Temporal resolution of a -> b (§7.2). report_sequence = (date_a, date_b) of the reports,
    used only for two fallback-dated results from different reports (rule 2a)."""
    if a.source == "NONE" or b.source == "NONE" or a.hi is None or b.hi is None:
        return {"temporal_resolution": "INDETERMINATE", "delta_days": None}
    if a.source == "DOCUMENT_DATE_FALLBACK" and b.source == "DOCUMENT_DATE_FALLBACK":
        if report_sequence and report_sequence[0] < report_sequence[1]:
            return {"temporal_resolution": "ORDER_ONLY", "order_basis": "REPORT_SEQUENCE", "delta_days": None}
        return {"temporal_resolution": "INDETERMINATE", "delta_days": None}
    a_hi = a.hi
    b_lo = b.lo
    if b_lo is None:                       # b only bounded above: can't be shown to follow a
        return {"temporal_resolution": "INDETERMINATE", "delta_days": None}
    if a_hi >= b_lo:
        return {"temporal_resolution": "INDETERMINATE", "delta_days": None}
    exact = (a.precision == "DAY" and b.precision == "DAY" and a.source != "DOCUMENT_DATE_FALLBACK"
             and b.source != "DOCUMENT_DATE_FALLBACK")
    if exact:
        return {"temporal_resolution": "EXACT", "order_basis": "EVENT_DATES", "delta_days": (b_lo - a_hi).days}
    if "APPROXIMATE" in (a.precision, b.precision) or a.lo is None:
        return {"temporal_resolution": "ORDER_ONLY", "order_basis": "EVENT_DATES", "delta_days": None}
    return {"temporal_resolution": "RANGE", "order_basis": "EVENT_DATES", "delta_days": None,
            "delta_days_min": (b_lo - a_hi).days, "delta_days_max": (b.hi - a.lo).days}
