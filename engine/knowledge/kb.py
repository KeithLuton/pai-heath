"""Read side of the knowledge base: record concepts -> authority inputs for the Parliament.

Retrieval is a deterministic lookup, never a model:
  1. Seeds are the record's own concepts (medications, findings, assessments,
     measured analytes) plus the question's words.
  2. A seed resolves to a MeSH descriptor only by exact match on a normalized
     MeSH entry term (or on the analyte's lexicon aliases). No fuzzy matching.
  3. Links offered: seed -> seed directly, and seed -> X -> seed in two hops.
     One-hop links from a seed to something not in the record are offered only
     when they continue to a seed, so the map never wanders off the record.
  4. Each link becomes one authority input with a declared {"from", "to"}; the
     Parliament decides what to do with it (§10.5). Nothing here reads prose.

Every authority carries its counts, PMIDs and the NLM credit line, and says
plainly that an indexed pairing is not a finding.
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set

from records import lexicon

from .build_kb import normalize

MAX_PER_SEED = 25
STUDY_COLUMNS = (("n_meta_analysis", "meta-analysis", "meta-analyses"),
                 ("n_systematic_review", "systematic review", "systematic reviews"),
                 ("n_rct", "randomized trial", "randomized trials"),
                 ("n_observational", "observational study", "observational studies"),
                 ("n_case_reports", "case report", "case reports"))


class KnowledgeBase:
    def __init__(self, path: Path):
        path = Path(path)
        if not path.is_file():
            raise FileNotFoundError(path)
        self.db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.meta = {r["k"]: r["v"] for r in self.db.execute("SELECT k, v FROM kb_meta")}
        if self.meta.get("kb_schema_version") != "1":
            raise ValueError("Unsupported knowledge-base version")

    def close(self) -> None:
        self.db.close()

    # ---- term resolution -------------------------------------------------
    def resolve(self, text: str) -> Set[str]:
        """MeSH descriptor ids whose entry term equals the text (normalized), or a lexicon alias of it."""
        candidates = {normalize(text)}
        canon = lexicon.analyte_of(text)
        if canon:
            candidates |= {normalize(a) for a in lexicon.aliases_of(canon)}
        candidates.discard("")
        out: Set[str] = set()
        for c in sorted(candidates):
            out |= {r[0] for r in self.db.execute("SELECT ui FROM entry_terms WHERE norm = ?", (c,))}
        return out

    def label(self, ui: str, prefer: Dict[str, str]) -> str:
        """Record wording if the record named this descriptor, else a term the lexicon recognizes, else MeSH name."""
        if ui in prefer:
            return prefer[ui]
        terms = sorted(r[0] for r in self.db.execute("SELECT term FROM entry_terms WHERE ui = ?", (ui,)))
        ranked = []
        for t in terms:
            m = lexicon.analyte_match(t)
            if m:
                aliases = lexicon.aliases_of(m[0])
                hit = t.lower()[m[1]:m[2]]
                ranked.append((aliases.index(hit) if hit in aliases else len(aliases), len(t), t))
        if ranked:
            return min(ranked)[2]   # the lexicon's primary alias first (e.g. "Vitamin B12 Deficiency")
        row = self.db.execute("SELECT name FROM descriptors WHERE ui = ?", (ui,)).fetchone()
        return row[0] if row else ui

    # ---- retrieval -------------------------------------------------------
    def authorities_for(self, seeds: Iterable[str]) -> List[dict]:
        prefer: Dict[str, str] = {}
        seed_uis: Set[str] = set()
        for s in sorted(set(seeds)):
            for ui in sorted(self.resolve(s)):
                prefer.setdefault(ui, s)
                seed_uis.add(ui)
        if not seed_uis:
            return []
        rows: Dict[int, sqlite3.Row] = {}
        ph = ",".join("?" * len(seed_uis))
        seeds_sorted = sorted(seed_uis)
        for r in self.db.execute(f"SELECT * FROM links WHERE from_ui IN ({ph}) AND to_ui IN ({ph})",
                                 seeds_sorted + seeds_sorted):
            rows[r["id"]] = r
        for ui in seeds_sorted:
            hop1 = self.db.execute(
                f"""SELECT l1.* FROM links l1 WHERE l1.from_ui = ? AND l1.to_ui NOT IN ({ph})
                    AND EXISTS (SELECT 1 FROM links l2 WHERE l2.from_ui = l1.to_ui AND l2.to_ui IN ({ph})
                                AND l2.to_ui <> l1.from_ui)
                    ORDER BY l1.n_articles DESC, l1.id LIMIT ?""",
                [ui] + seeds_sorted + seeds_sorted + [MAX_PER_SEED]).fetchall()
            for r in hop1:
                rows[r["id"]] = r
                for r2 in self.db.execute(
                        f"""SELECT * FROM links WHERE from_ui = ? AND to_ui IN ({ph}) AND to_ui <> ?
                            ORDER BY n_articles DESC, id LIMIT ?""",
                        [r["to_ui"]] + seeds_sorted + [ui, MAX_PER_SEED]):
                    rows[r2["id"]] = r2
        ordered = sorted(rows.values(), key=lambda r: (-r["n_articles"], r["id"]))
        return [self._authority(r, prefer) for r in ordered]

    def _authority(self, r: sqlite3.Row, prefer: Dict[str, str]) -> dict:
        name = {ui: (self.db.execute("SELECT name FROM descriptors WHERE ui=?", (ui,)).fetchone() or [ui])[0]
                for ui in (r["from_ui"], r["to_ui"])}
        qual = {"ADVERSE_EFFECT": ("adverse effects", "chemically induced"),
                "COMPLICATION": ("complications", "etiology")}[r["rule"]]
        designs = [f"{r[c]} {one if r[c] == 1 else many}" for c, one, many in STUDY_COLUMNS if r[c]]
        years = f"{r['first_year']}–{r['last_year']}" if r["first_year"] else "years not stated"
        pmids = [p["pmid"] for p in self.db.execute(
            "SELECT pmid FROM link_pmids WHERE link_id=? ORDER BY rank", (r["id"],))]
        text = (f"PubMed indexing: {r['n_articles']} articles index {name[r['from_ui']]} ({qual[0]}) with "
                f"{name[r['to_ui']]} ({qual[1]}), {years}"
                + (f"; including {', '.join(designs)}" if designs else "")
                + ". An indexed pairing records that this was studied; it is not a finding, a frequency, "
                  "or an effect size.")
        return {
            "id": f"PM-{r['rule'][:3]}-{r['id']}",
            "status": "INDEXED_LINK",
            "level": "POPULATION",
            "text": text,
            "links": [{"from": self.label(r["from_ui"], prefer), "to": self.label(r["to_ui"], prefer)}],
            "mesh": {"from": {"ui": r["from_ui"], "name": name[r["from_ui"]], "qualifier": qual[0]},
                     "to": {"ui": r["to_ui"], "name": name[r["to_ui"]], "qualifier": qual[1]},
                     "rule": r["rule"]},
            "counts": {k: r[k] for k in r.keys() if k.startswith("n_")},
            "years": [r["first_year"], r["last_year"]],
            "pmids": pmids,
            "credit": self.meta.get("credit", ""),
            "kb": {"built_at": self.meta.get("built_at"), "shards_included": self.meta.get("shards_included")},
        }


def record_seeds(floats: List[dict], question: Optional[str]) -> List[str]:
    """The record's own concepts — what retrieval is allowed to start from."""
    out: List[str] = []
    for f in floats:
        if f.get("polarity") == "NEGATED":
            continue
        if f["kind"] in ("MEDICATION_STATUS", "ACTION", "REPORT", "OBSERVATION", "ASSESSMENT"):
            out.append(f["concept"])
            med = f.get("medication") or {}
            if med.get("name"):
                out.append(med["name"])
        elif f["kind"] == "MEASUREMENT" and f.get("value", {}).get("analyte"):
            out.append(f["value"]["analyte"])
    if question:
        words = re.findall(r"[A-Za-z0-9]+", question)
        out += [w for w in words if len(w) > 3]
    return out
