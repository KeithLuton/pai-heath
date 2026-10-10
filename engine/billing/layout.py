"""Label-driven extraction for paper billing layouts (CMS-1500, UB-04, EOB, MSN, patient bill).

Order of work:
1. Find line tables: a header row (up to three stacked lines) where at least two
   cells match this layout's column labels, one of them a date or money column.
   Rows follow until a totals line, a gap, a page change, or a line that is
   neither a row nor a wrapped description.
2. Read header fields from the remaining lines: a printed label followed by
   ':' / '#', a column gap, or the end of the line (value on the line below).
3. Review checks. They raise issues; they never change or derive a value.

Every value carries raw text and a locator; for text-mode lines
canonical_text[text_start:text_end] == raw_value.
"""
from __future__ import annotations

import re
from collections import defaultdict
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

from . import layouts as L
from . import values as V
from .lines import Line

RUN = re.compile(r"\S+(?: \S+)*")
TOTAL_ROW = re.compile(r"^\s*(?:claim |grand |page |account )?totals?\b(?!\s+(?:charge|due)\b)|^\s*total (?:charges|billed)\b",
                       re.I)
MAX_HEADER_LINES = 3


def _runs(text: str) -> List[Tuple[int, int, str]]:
    return [(m.start(), m.end(), m.group(0)) for m in RUN.finditer(text)]


def _overlap(a0, a1, b0, b1) -> int:
    return max(0, min(a1, b1) - max(a0, b0))


class _Builder:
    def __init__(self, source_id: str, assertion: str):
        self.source_id, self.assertion = source_id, assertion

    def field(self, name: str, kind: str, line: Line, a: int, b: int, label: Optional[str],
              row_id: Optional[str] = None, issues: Optional[List[str]] = None) -> dict:
        raw = line.raw(a, b)
        loc, li = line.locate(a, b)
        if kind in ("sentence", "unlabeled", "text"):
            norm, pi, extra = V.text(raw)
        elif kind == "ambiguous":
            norm, pi, extra = V.money(raw)
            pi = list(pi) + ["AMBIGUOUS_COLUMN_LABEL"]
        else:
            norm, pi, extra = V.parse(kind, raw)
        if kind == "unlabeled":
            pi = list(pi) + ["UNRECOGNIZED_COLUMN"]
        all_issues = list(dict.fromkeys(li + list(pi) + list(issues or [])))
        if norm is None and kind not in ("sentence",):
            all_issues = [i if i not in ("NOT_MONEY", "NOT_A_DATE", "NOT_A_CODE", "NOT_AN_IDENTIFIER",
                                         "NOT_A_QUANTITY") else "TYPE_MISMATCH" for i in all_issues]
        f = {"field": name, "label": label, "raw_value": raw, "normalized_value": norm, "source_id": self.source_id,
             "locator": loc, "row_id": row_id,
             "assertion_kind": "paid-as-reported" if name in L.PAID_FIELDS else self.assertion,
             "issues": all_issues}
        if extra:
            f.update({k: v for k, v in extra.items() if v is not None})
        return f


# ------------------------------------------------------------------ tables
def _header_block(profile: str, lines: List[Line], i: int):
    """Best header interpretation starting at line i: (k, columns) or None."""
    best = None
    for k in range(1, MAX_HEADER_LINES + 1):
        block = lines[i:i + k]
        if len(block) < k or any(l.page != block[0].page or not l.text.strip() for l in block):
            break
        if any(V.MONEY_TOKEN.search(l.text) or V.DATE_TOKEN.search(l.text) for l in block):
            break
        toks: List[List] = []
        for n, l in enumerate(block):
            for s, e, t in _runs(l.text):
                hit = next((x for x in toks if _overlap(s, e, x[0], x[1]) > 0), None)
                if hit:
                    hit[0], hit[1], hit[2] = min(hit[0], s), max(hit[1], e), hit[2] + " " + t
                    hit[3].add(n)
                else:
                    toks.append([s, e, t, {n}])
        toks.sort(key=lambda x: x[0])
        cols, matched, has_key, contributes = [], 0, False, set()
        for s, e, t, src in toks:
            m = L.match_column(profile, t)
            field, kind = (m if m else ("unlabeled_column", "unlabeled"))
            if m and kind != "ambiguous":
                matched += 1
                has_key = has_key or kind in ("money", "date")
                contributes |= src
            cols.append({"start": s, "end": e, "label": t, "field": field, "kind": kind})
        if len(contributes) < k:            # every stacked line must be part of a recognized label
            continue
        if matched >= 2 and has_key and matched * 2 >= len(cols) and (best is None or matched > best[2]):
            best = (k, cols, matched)
    return (best[0], best[1]) if best else None


def _assign(cols: List[dict], s: int, e: int, txt: str):
    scored = sorted(((_overlap(s, e, c["start"], c["end"]), n) for n, c in enumerate(cols)), reverse=True)
    hits = [n for ov, n in scored if ov > 0]
    if not hits:
        dist = sorted((min(abs(s - c["end"]), abs(e - c["start"]), abs(s - c["start"])), n) for n, c in enumerate(cols))
        return (dist[0][1], []) if dist and dist[0][0] <= 4 else (None, ["UNMAPPED_CELL"])
    if len(hits) == 1:
        return hits[0], []

    def fits(n):
        k = cols[n]["kind"]
        if k in ("text", "unlabeled", "ambiguous"):
            return True
        if k == "code_mod":
            return V.looks_like("code", txt.split()[0])
        return V.looks_like(k, txt)
    valid = [n for n in hits if fits(n)]
    typed = [n for n in valid if cols[n]["kind"] not in ("text", "unlabeled")]
    if len(typed) == 1:
        return typed[0], []
    if len(valid) == 1:
        return valid[0], []
    return (valid or hits)[0], ["AMBIGUOUS_CELL_POSITION"]


def _cell_fields(b: _Builder, col: dict, line: Line, s: int, e: int, row_id: str, issues: List[str]) -> List[dict]:
    if col["kind"] != "code_mod":
        return [b.field(col["field"], col["kind"], line, s, e, col["label"], row_id, issues)]
    out = []
    toks = list(re.finditer(r"[A-Za-z0-9.]+", line.text[s:e]))
    for n, t in enumerate(toks):
        a, z = s + t.start(), s + t.end()
        if n == 0:
            out.append(b.field("service_code", "code", line, a, z, col["label"], row_id, issues))
        elif re.fullmatch(r"[A-Za-z0-9]{2}", t.group(0)):
            out.append(b.field("modifier", "code", line, a, z, col["label"], row_id, issues))
        else:
            out.append(b.field("service_code_extra", "text", line, a, z, col["label"], row_id,
                               issues + ["UNEXPECTED_TOKEN_IN_CODE_CELL"]))
    return out


def _row_is_data(line: Line, cols: List[dict]) -> bool:
    """A row has a date in a date column or an amount in a money (or ambiguous amount) column."""
    for s, e, t in _runs(line.text):
        n, _ = _assign(cols, s, e, t)
        if n is None:
            continue
        k = cols[n]["kind"]
        if k == "date" and V.looks_like("date", t):
            return True
        if k in ("money", "ambiguous") and V.looks_like("money", t):
            return True
    return False


def find_tables(profile: str, lines: List[Line], b: _Builder) -> Tuple[List[dict], set]:
    used, tables = set(), []
    i = 0
    while i < len(lines):
        hb = _header_block(profile, lines, i)
        if not hb:
            i += 1
            continue
        k, cols = hb
        tid = f"T{len(tables) + 1}"
        table = {"table_id": tid, "page": lines[i].page, "columns": [{"label": c["label"], "field": c["field"]}
                                                                     for c in cols],
                 "rows": [], "totals": None}
        used.update(range(i, i + k))
        j, blanks = i + k, 0
        while j < len(lines):
            line = lines[j]
            if line.page != lines[i].page:
                break
            if not line.text.strip():
                blanks += 1
                if blanks >= 2:
                    break
                j += 1
                continue
            if _header_block(profile, lines, j):
                break
            runs = _runs(line.text)
            is_total = bool(TOTAL_ROW.match(line.text)) or any(re.fullmatch(r"totals?:?", t, re.I) for _, _, t in runs)
            if not is_total and not _row_is_data(line, cols):
                text_cols = [n for n, c in enumerate(cols) if c["kind"] in ("text",)]
                assigned = [_assign(cols, s, e, t)[0] for s, e, t in runs]
                if table["rows"] and assigned and all(a in text_cols for a in assigned) and blanks == 0:
                    row = table["rows"][-1]
                    for (s, e, t), a in zip(runs, assigned):
                        f = b.field(cols[a]["field"], "text", line, s, e, cols[a]["label"], row["row_id"],
                                    ["WRAPPED_CONTINUATION"])
                        row["fields"].append(f)
                    used.add(j)
                    j += 1
                    continue
                break
            blanks = 0
            row_id = f"{tid}:TOTAL" if is_total else f"{tid}:R{len(table['rows']) + 1}"
            row = {"row_id": row_id, "page": line.page, "fields": [], "issues": []}
            for s, e, t in runs:
                if is_total and (TOTAL_ROW.match(t) or re.fullmatch(r"totals?:?", t, re.I)):
                    continue
                n, iss = _assign(cols, s, e, t)
                if n is None:
                    row["fields"].append(b.field("unmapped", "text", line, s, e, None, row_id, iss))
                    continue
                row["fields"] += _cell_fields(b, cols[n], line, s, e, row_id, iss)
            seen = defaultdict(int)
            for f in row["fields"]:
                if f["field"] not in ("modifier", "description", "remarks", "unmapped") and "WRAPPED_CONTINUATION" not in f["issues"]:
                    seen[f["field"]] += 1
            for name, c in seen.items():
                if c > 1:
                    for f in row["fields"]:
                        if f["field"] == name:
                            f["issues"].append("MULTIPLE_VALUES_IN_COLUMN")
            used.add(j)
            if is_total:
                table["totals"] = row
                j += 1
                break
            table["rows"].append(row)
            j += 1
        tables.append(table)
        i = max(j, i + k)
    return tables, used


# ------------------------------------------------------------------ header fields
_SEP = re.compile(r"[ \t]*[:#][ \t]*|[ \t]{2,}|[ \t]*$")
_DIAG = re.compile(r"(?<![A-Za-z0-9])([A-L])\s*[.|)]\s*([A-Z][0-9][0-9A-Z](?:\.?[0-9A-Z]{1,4})?)(?![0-9A-Za-z])")


def _is_label(profile: str, text: str) -> bool:
    t = text.strip()
    if L.match_column(profile, t):
        return True
    for _, _, rx in L.header_patterns(profile):
        m = rx.match(t)
        if m and m.end() >= len(t.rstrip(":# ")) - 1:
            return True
    return False


def _below(lines: List[Line], li: int, used: set, a: int, z: int) -> Optional[Tuple[int, int, int]]:
    for d in (1, 2):
        j = li + d
        if j >= len(lines) or j in used or lines[j].page != lines[li].page:
            return None
        runs = _runs(lines[j].text)
        if not runs:
            continue
        cands = [(_overlap(s, e, a, max(z, a + 25)), s, e) for s, e, _ in runs]
        cands = [c for c in cands if c[0] > 0 and abs(c[1] - a) <= max(12, z - a)]
        if cands:
            _, s, e = max(cands)
            return j, s, e
        return None
    return None


def header_fields(profile: str, lines: List[Line], used: set, b: _Builder) -> Tuple[List[dict], List[dict]]:
    pats = L.header_patterns(profile)
    out: List[dict] = []
    empty: List[dict] = []
    for li, line in enumerate(lines):
        if li in used or not line.text.strip():
            continue
        hits = []
        for field, kind, rx in pats:
            for m in rx.finditer(line.text):
                hits.append((m.end() - m.start(), m.start(), m.end(), field, kind))
        hits.sort(key=lambda h: (-h[0], h[1]))
        taken: List[Tuple[int, int]] = []
        for _, a, z, field, kind in hits:
            if any(_overlap(a, z, s, e) for s, e in taken):
                continue
            label = line.text[a:z]
            if kind == "sentence":
                runs = [r for r in _runs(line.text) if r[0] <= a < r[1]]
                if runs:
                    s, e, _ = runs[0]
                    out.append(b.field(field, "sentence", line, s, e, None))
                    taken.append((a, z))
                continue
            if kind == "diag_list":
                taken.append((a, z))
                for j in range(li, min(li + 4, len(lines))):
                    if lines[j].page != line.page or (j != li and j in used):
                        break
                    start = z if j == li else 0
                    for m in _DIAG.finditer(lines[j].text, start):
                        out.append(b.field("diagnosis", "code", lines[j], m.start(2), m.end(2), f"{label} {m.group(1)}"))
                continue
            if field == "appeal_deadline":
                m = V.DATE_TOKEN.search(line.text, z, z + 24)
                if m:
                    taken.append((a, m.end()))
                    out.append(b.field(field, "date", line, m.start(), m.end(), label))
                continue
            sep = _SEP.match(line.text, z)
            if not sep:
                continue
            taken.append((a, z))
            after = sep.end()
            cell = RUN.match(line.text, after) if after < len(line.text) else None
            target = None
            if cell and cell.start() == after and not _is_label(profile, cell.group(0)):
                target = (line, cell.start(), cell.end())
            elif after >= len(line.text.rstrip()) or (cell and _is_label(profile, cell.group(0))):
                got = _below(lines, li, used, a, z)
                if got:
                    j, s, e = got
                    target = (lines[j], s, e)
            if target is None:
                empty.append({"field": field, "label": label, "page": line.page})
                continue
            tl, s, e = target
            txt = tl.text[s:e]
            if _is_label(profile, txt):
                empty.append({"field": field, "label": label, "page": line.page})
                continue
            ok = V.looks_like(kind, txt) if kind != "text" else bool(txt.strip())
            if not ok:
                empty.append({"field": field, "label": label, "page": line.page, "seen": txt})
                continue
            if tl is line:
                taken.append((s, e))
            out.append(b.field(field, kind, tl, s, e, label))
    return out, empty


# ------------------------------------------------------------------ checks
def _issue(code: str, message: str, **kw) -> dict:
    return dict({"code": code, "message": message}, **kw)


def _dec(f: dict) -> Optional[Decimal]:
    v = f.get("normalized_value")
    try:
        return Decimal(v) if v is not None and "TYPE_MISMATCH" not in f["issues"] else None
    except Exception:
        return None


def checks(profile: str, header: List[dict], tables: List[dict], empty: List[dict]) -> List[dict]:
    spec = L.LAYOUTS[profile]
    issues: List[dict] = []
    by: Dict[str, List[dict]] = defaultdict(list)
    for f in header:
        by[f["field"]].append(f)
    for name, fs in by.items():
        if name in ("appeal", "diagnosis"):
            continue
        distinct = {f["normalized_value"] for f in fs}
        if len(distinct) > 1:
            for f in fs:
                f["issues"].append("SOURCE_CONFLICT")
                f["conflict_values"] = sorted(str(x) for x in distinct)
                f["normalized_value"] = None
            issues.append(_issue("SOURCE_CONFLICT", f"'{name}' is printed with {len(distinct)} different values; "
                                 "it stays unknown until you choose.", field=name))
    for e in empty:
        if e["field"] not in by:
            issues.append(_issue("LABEL_WITHOUT_VALUE", f"Found the label “{e['label']}” on page {e['page']} but no "
                                 "readable value next to or below it.", field=e["field"]))
    for name in spec["required_header"]:
        if name not in by:
            issues.append(_issue("MISSING_FIELD", f"No '{name}' was found. It stays unknown.", field=name))
    if not tables:
        issues.append(_issue("NO_LINE_TABLE_FOUND", "No service-line table with recognizable column labels was found. "
                             "Lines can't be read without labeled columns."))
    for t in tables:
        for row in t["rows"]:
            names = {f["field"] for f in row["fields"]}
            for need in spec["required_line"]:
                if need not in names and not (need == "billed" and "amount_unqualified" in names):
                    row["issues"].append(f"MISSING_{need.upper()}")
        # Repeated rows are kept and flagged, never collapsed.
        keyed = defaultdict(list)
        for row in t["rows"]:
            key = tuple(sorted((f["field"], str(f["normalized_value"]).lower()) for f in row["fields"]
                               if f["field"] in ("service_date", "service_code", "modifier", "billed", "description",
                                                 "revenue_code", "units")))
            if any(k[0] == "billed" for k in key):
                keyed[key].append(row)
        for rows in keyed.values():
            if len(rows) > 1:
                ids = [r["row_id"] for r in rows]
                for r in rows:
                    r["issues"].append("REPEATED_ROW")
                issues.append(_issue("REPEATED_ROW", f"Rows {', '.join(ids)} are printed identically (date, code, "
                                     "charge). Both are kept; check whether the service was billed twice.", rows=ids))
        # Printed totals vs printed lines: a consistency flag only.
        if t["totals"]:
            money_fields = {f for f, k, _ in spec["columns"] if k == "money"}
            for tf in t["totals"]["fields"]:
                if tf["field"] not in money_fields or _dec(tf) is None:
                    continue
                vals = [[_dec(f) for f in r["fields"] if f["field"] == tf["field"]] for r in t["rows"]]
                if vals and all(len(v) == 1 and v[0] is not None for v in vals):
                    s = sum(v[0] for v in vals)
                    if s != _dec(tf):
                        tf["issues"].append("TOTAL_DIFFERS_FROM_LINES")
                        issues.append(_issue("TOTAL_DIFFERS_FROM_LINES",
                                             f"Printed {tf['field']} total {tf['raw_value']} differs from the sum of the "
                                             f"printed lines ({s}). Neither is changed.", field=tf["field"],
                                             row_id=t["totals"]["row_id"]))
    if spec.get("header_total") and tables:
        hname, lname = spec["header_total"]
        tot = [f for f in by.get(hname, []) if _dec(f) is not None]
        vals = [[_dec(f) for f in r["fields"] if f["field"] == lname] for t in tables for r in t["rows"]]
        if len(tot) == 1 and vals and all(len(v) == 1 and v[0] is not None for v in vals):
            s = sum(v[0] for v in vals)
            if s != _dec(tot[0]):
                tot[0]["issues"].append("TOTAL_DIFFERS_FROM_LINES")
                issues.append(_issue("TOTAL_DIFFERS_FROM_LINES", f"Printed {hname} {tot[0]['raw_value']} differs from "
                                     f"the sum of the printed line charges ({s}). Neither is changed.", field=hname))
    return issues


def extract(profile: str, lines: List[Line], source_id: str) -> dict:
    spec = L.LAYOUTS[profile]
    b = _Builder(source_id, spec["assertion"])
    tables, used = find_tables(profile, lines, b)
    header, empty = header_fields(profile, lines, used, b)
    issues = checks(profile, header, tables, empty)
    return {"adapter": f"layout-{profile.lower()}-v1", "adapter_status": L.STATUS, "locale": L.LOCALE,
            "assertion_kind": spec["assertion"], "header": header, "tables": tables, "issues": issues,
            "notes": list(spec["notes"])}
