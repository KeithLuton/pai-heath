"""Value parsing for billing fields: money, dates, identifiers, codes, quantities.

Every parser returns (normalized, issues, extra). `normalized` is None when the
raw text does not fit the type; the raw text is always kept by the caller.
Money uses Decimal under the profile's declared locale (en-US for every paper
profile here); nothing is rounded or re-scaled.
"""
from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import List, Optional, Tuple

Parsed = Tuple[Optional[str], List[str], dict]

_MONEY = re.compile(
    r"^(?P<open>\()?\s*(?P<lead>-)?\s*(?P<sym>\$)?\s*(?P<lead2>-)?\s*"
    r"(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d{2})?|\d+(?:\.\d{2})?|\.\d{2})"
    r"\s*(?P<close>\))?\s*(?P<trail>-|CR)?$", re.I)
_SPLIT_CENTS = re.compile(r"^(?P<sym>\$)?\s*(?P<d>\d{1,7}) (?P<c>\d{2})$")

_D_MDY = r"(?P<m>\d{1,2})[/.-](?P<d>\d{1,2})[/.-](?P<y>\d{4}|\d{2})"
_D_ISO = r"(?P<Y>\d{4})-(?P<M>\d{2})-(?P<D>\d{2})"
_D_SP = r"(?P<sm>\d{2}) (?P<sd>\d{2}) (?P<sy>\d{4}|\d{2})"
_D_MON = (r"(?P<mon>Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?\s+(?P<md>\d{1,2}),?\s+"
          r"(?P<my>\d{4})")
_DATE = re.compile(rf"^(?:{_D_MDY}|{_D_ISO}|{_D_SP}|{_D_MON})$", re.I)
DATE_TOKEN = re.compile(rf"(?<![\d/])(?:{_D_MDY}|{_D_ISO}|{_D_MON})(?![\d/])", re.I)
MONEY_TOKEN = re.compile(r"(?<![\w.])\(?-?\$?\s?(?:\d{1,3}(?:,\d{3})+|\d+)\.\d{2}\)?(?:-|CR)?(?![\w.])")
_MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct",
                                       "nov", "dec"], 1)}
_RANGE_SEP = re.compile(r"\s*(?:-|–|—|to|thru|through)\s*|\s{1,}", re.I)


def money(raw: str) -> Parsed:
    s = raw.strip()
    m = _MONEY.match(s)
    if m:
        if bool(m.group("open")) != bool(m.group("close")):
            return None, ["UNBALANCED_PARENTHESES"], {}
        try:
            val = Decimal(m.group("num").replace(",", ""))
        except InvalidOperation:
            return None, ["NOT_MONEY"], {}
        signs = [k for k in ("open", "lead", "lead2", "trail") if m.group(k)]
        extra = {"currency_symbol": "$" if m.group("sym") else None}
        if len(signs) > 1:
            return None, ["SIGN_AMBIGUOUS"], extra
        if signs:
            basis = {"open": "parentheses", "lead": "leading minus", "lead2": "leading minus",
                     "trail": "CR suffix" if (m.group("trail") or "").upper() == "CR" else "trailing minus"}[signs[0]]
            extra["sign_basis"] = basis
            val = -val
        return str(val), [], extra
    m = _SPLIT_CENTS.match(s)
    if m:
        # Paper claim forms print dollars and cents in separate boxes.
        return (str(Decimal(f"{m.group('d')}.{m.group('c')}")), ["CENTS_IN_SEPARATE_BOX"],
                {"currency_symbol": "$" if m.group("sym") else None})
    return None, ["NOT_MONEY"], {}


def _ymd(y: str, mo: int, d: int, issues: List[str]) -> Optional[str]:
    if len(y) == 2:
        issues.append("TWO_DIGIT_YEAR")
        yy = 2000 + int(y)
    else:
        yy = int(y)
    try:
        return date(yy, mo, d).isoformat()
    except ValueError:
        issues.append("INVALID_DATE")
        return None


def one_date(raw: str) -> Parsed:
    s = raw.strip()
    m = _DATE.match(s)
    issues: List[str] = []
    if not m:
        return None, ["NOT_A_DATE"], {}
    g = m.groupdict()
    if g.get("m"):
        iso = _ymd(g["y"], int(g["m"]), int(g["d"]), issues)
    elif g.get("Y"):
        iso = _ymd(g["Y"], int(g["M"]), int(g["D"]), issues)
    elif g.get("sm"):
        iso = _ymd(g["sy"], int(g["sm"]), int(g["sd"]), issues)
    else:
        iso = _ymd(g["my"], _MONTHS[g["mon"][:3].lower()], int(g["md"]), issues)
    return iso, issues, {}


def date_or_range(raw: str) -> Parsed:
    s = raw.strip()
    iso, issues, extra = one_date(s)
    if iso or "INVALID_DATE" in issues:
        return iso, issues, extra
    toks = [t.group(0) for t in DATE_TOKEN.finditer(s)]
    if not toks:
        # CMS-1500 style "01 02 26 01 05 26"
        sp = re.fullmatch(r"(\d{2} \d{2} \d{2,4})\s+(\d{2} \d{2} \d{2,4})", s)
        toks = list(sp.groups()) if sp else []
    if len(toks) == 2:
        rest = s
        for t in toks:
            rest = rest.replace(t, "", 1)
        if rest.strip(" -–—tohrugTOHRUG") == "":
            a, ia, _ = one_date(toks[0])
            b, ib, _ = one_date(toks[1])
            issues = sorted(set(ia + ib))
            if a and b:
                if b < a:
                    issues.append("RANGE_END_BEFORE_START")
                return f"{a}/{b}", issues, {"start": a, "end": b}
            return None, issues or ["NOT_A_DATE"], {}
    return None, ["NOT_A_DATE"], {}


def identifier(raw: str) -> Parsed:
    s = raw.strip()
    if re.fullmatch(r"[A-Za-z0-9#][A-Za-z0-9#\-/. ]{1,40}", s) and re.search(r"\d", s):
        return s, [], {}
    return None, ["NOT_AN_IDENTIFIER"], {}


def quantity(raw: str) -> Parsed:
    s = raw.strip()
    if re.fullmatch(r"\d+(?:\.\d+)?", s):
        return str(Decimal(s)), [], {}
    return None, ["NOT_A_QUANTITY"], {}


def code(raw: str) -> Parsed:
    """An opaque printed service/revenue/diagnosis identifier. Never expanded or looked up."""
    s = raw.strip()
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.]{1,9}", s):
        return s, [], {}
    return None, ["NOT_A_CODE"], {}


def text(raw: str) -> Parsed:
    s = re.sub(r"\s+", " ", raw.strip())
    return (s, [], {}) if s else (None, ["EMPTY"], {})


PARSERS = {"money": money, "date": date_or_range, "id": identifier, "qty": quantity, "code": code, "text": text}


def parse(kind: str, raw: str) -> Parsed:
    return PARSERS[kind](raw)


def looks_like(kind: str, raw: str) -> bool:
    return parse(kind, raw)[0] is not None
