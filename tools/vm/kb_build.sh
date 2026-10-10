#!/bin/bash
# PAi Health: build the knowledge-base link table on the corpus VM.
# Runs in the background at low priority next to the PubMed build; reads the PubMed DB read-only.
set -e
BASE=/data/psi-health
KB=$BASE/kb
mkdir -p $KB
chown paiadmin:paiadmin $KB
cat > $KB/build_kb.py <<'PYEOF'
"""Build the PAi Health knowledge-base link table from a PubMed baseline database.

    python3 build_kb.py --pubmed /data/psi-health/db/psi_health_pubmed_2026.db \
                        --mesh desc2026.xml --out psi_health_kb.db [--min-articles 3]

Stdlib only (runs on the corpus VM). Reads the PubMed database read-only, so it
can run while the baseline build is still writing.

What a link is
--------------
NLM indexers attach qualifiers to MeSH headings. Two qualifier pairings declare
a direction, and only these two are used:

    A / adverse effects   +  B / chemically induced   ->  A -> B  (ADVERSE_EFFECT)
    A / complications     +  B / etiology             ->  A -> B  (COMPLICATION)

A link is "NLM indexed N articles this way". It records that a pairing was
studied, not that it is true, how often it happens, or how large it is.
Retracted publications and retraction notices are excluded.

What ships
----------
Descriptor ids and names, MeSH entry terms (public domain), counts by
publication type, year span, and up to --pmids-per-link PMIDs per link. No
titles, no abstracts, no author names.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone

try:
    from defusedxml.ElementTree import iterparse
except ImportError:  # NLM file; stdlib expat does not fetch external entities
    from xml.etree.ElementTree import iterparse

KB_SCHEMA_VERSION = 1
RULES = (("ADVERSE_EFFECT", "adverse effects", "chemically induced"),
         ("COMPLICATION", "complications", "etiology"))
EXCLUDED_TYPES = ("Retracted Publication", "Retraction of Publication")
TYPE_FLAGS = (("Meta-Analysis", 1), ("Systematic Review", 2), ("Randomized Controlled Trial", 4),
              ("Clinical Trial", 8), ("Observational Study", 16), ("Review", 32), ("Case Reports", 64))
CREDIT = ("Courtesy of the U.S. National Library of Medicine. Derived from the PubMed 2026 baseline "
          "and MeSH 2026. NLM does not endorse this product.")


def log(msg: str) -> None:
    print(time.strftime("%H:%M:%S"), msg, flush=True)


def load_mesh(kb: sqlite3.Connection, path: str) -> int:
    kb.executescript("""
        CREATE TABLE descriptors (ui TEXT PRIMARY KEY, name TEXT NOT NULL);
        CREATE TABLE entry_terms (ui TEXT NOT NULL, term TEXT NOT NULL, norm TEXT NOT NULL,
                                  PRIMARY KEY (ui, term));
    """)
    n = 0
    ui = name = None
    terms: list = []
    for event, el in iterparse(path, events=("end",)):
        tag = el.tag
        if tag == "DescriptorUI" and ui is None:
            ui = (el.text or "").strip()
        elif tag == "String" and name is None and ui is not None:
            name = (el.text or "").strip()     # first String in a record is DescriptorName
        elif tag == "Term":
            s = el.findtext("String")
            if s:
                terms.append(s.strip())
        elif tag == "DescriptorRecord":
            if ui and name:
                kb.execute("INSERT OR IGNORE INTO descriptors VALUES (?,?)", (ui, name))
                for t in set(terms) | {name}:
                    kb.execute("INSERT OR IGNORE INTO entry_terms VALUES (?,?,?)", (ui, t, normalize(t)))
                n += 1
            ui = name = None
            terms = []
            el.clear()
    kb.execute("CREATE INDEX entry_terms_norm ON entry_terms(norm)")
    return n


def normalize(text: str) -> str:
    """Lowercase, alphanumerics only, 'b 12' -> 'b12', single spaces. Shared with kb.py."""
    import re
    s = re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()
    s = re.sub(r"\b([a-z]) (\d+)\b", r"\1\2", s)
    return s


def build(pubmed: str, mesh: str, out: str, min_articles: int, pmids_per_link: int) -> None:
    if os.path.exists(out):
        sys.exit(f"{out} exists; remove it first")
    tmpdir = os.path.join(os.path.dirname(os.path.abspath(out)), "kb_tmp")
    os.makedirs(tmpdir, exist_ok=True)
    os.environ["SQLITE_TMPDIR"] = tmpdir
    kb = sqlite3.connect(out)
    kb.execute("PRAGMA journal_mode=OFF")
    kb.execute("PRAGMA synchronous=OFF")
    kb.execute("PRAGMA temp_store=FILE")
    kb.execute("PRAGMA cache_size=-4000000")
    kb.execute(f"ATTACH DATABASE 'file:{pubmed}?mode=ro' AS src")

    log("MeSH descriptors and entry terms")
    n_desc = load_mesh(kb, mesh)
    log(f"  {n_desc} descriptors")

    quals = sorted({q for _, a, b in RULES for q in (a, b)})
    log("qualified headings")
    kb.execute(f"""CREATE TEMP TABLE tq AS
        SELECT h.article_id AS a, h.descriptor_ui AS d, q.qualifier AS ql
        FROM src.mesh_qualifiers q JOIN src.mesh_headings h ON h.id = q.mesh_heading_id
        WHERE q.qualifier IN ({','.join('?' * len(quals))}) AND h.descriptor_ui <> ''""", quals)
    kb.execute("CREATE INDEX temp.tq_a ON tq(a, ql)")

    log("publication types")
    case = " ".join(f"WHEN '{t}' THEN {bit}" for t, bit in TYPE_FLAGS)
    kb.execute(f"""CREATE TEMP TABLE pt AS
        SELECT article_id AS a, SUM(DISTINCT CASE publication_type {case} ELSE 0 END) AS flags,
               MAX(publication_type IN ({','.join('?' * len(EXCLUDED_TYPES))})) AS excluded
        FROM src.publication_types WHERE article_id IN (SELECT a FROM tq) GROUP BY article_id""",
               EXCLUDED_TYPES)
    kb.execute("CREATE UNIQUE INDEX temp.pt_a ON pt(a)")

    log("human-subject articles")
    kb.execute("""CREATE TEMP TABLE hu AS SELECT DISTINCT article_id AS a FROM src.mesh_headings
                  WHERE descriptor = 'Humans' AND article_id IN (SELECT a FROM tq)""")
    kb.execute("CREATE UNIQUE INDEX temp.hu_a ON hu(a)")

    log("article pairs")
    kb.execute("CREATE TEMP TABLE pairs (a INTEGER, f TEXT, t TEXT, rule TEXT)")
    for rule, qf, qt in RULES:
        kb.execute("""INSERT INTO pairs SELECT DISTINCT f.a, f.d, t.d, ?
                      FROM tq f JOIN tq t ON t.a = f.a AND t.ql = ? AND t.d <> f.d
                      WHERE f.ql = ?""", (rule, qt, qf))
    kb.execute("""DELETE FROM pairs WHERE a IN (SELECT a FROM pt WHERE excluded)""")
    kb.execute("CREATE INDEX temp.pairs_k ON pairs(f, t, rule)")

    log("aggregate links")
    kb.executescript(f"""
        CREATE TABLE links (
            id INTEGER PRIMARY KEY, rule TEXT NOT NULL,
            from_ui TEXT NOT NULL, to_ui TEXT NOT NULL,
            n_articles INTEGER NOT NULL, n_humans INTEGER NOT NULL,
            n_meta_analysis INTEGER NOT NULL, n_systematic_review INTEGER NOT NULL,
            n_rct INTEGER NOT NULL, n_clinical_trial INTEGER NOT NULL,
            n_observational INTEGER NOT NULL, n_review INTEGER NOT NULL, n_case_reports INTEGER NOT NULL,
            first_year TEXT, last_year TEXT,
            UNIQUE (from_ui, to_ui, rule));
        INSERT INTO links (rule, from_ui, to_ui, n_articles, n_humans, n_meta_analysis, n_systematic_review,
                           n_rct, n_clinical_trial, n_observational, n_review, n_case_reports, first_year, last_year)
        SELECT p.rule, p.f, p.t, COUNT(*), COUNT(hu.a),
               SUM(COALESCE(pt.flags,0) & 1 > 0), SUM(COALESCE(pt.flags,0) & 2 > 0),
               SUM(COALESCE(pt.flags,0) & 4 > 0), SUM(COALESCE(pt.flags,0) & 8 > 0),
               SUM(COALESCE(pt.flags,0) & 16 > 0), SUM(COALESCE(pt.flags,0) & 32 > 0),
               SUM(COALESCE(pt.flags,0) & 64 > 0),
               MIN(NULLIF(SUBSTR(ar.pub_year,1,4),'')), MAX(NULLIF(SUBSTR(ar.pub_year,1,4),''))
        FROM pairs p
        LEFT JOIN pt ON pt.a = p.a LEFT JOIN hu ON hu.a = p.a
        JOIN src.articles ar ON ar.id = p.a
        GROUP BY p.f, p.t, p.rule HAVING COUNT(*) >= {int(min_articles)}
        ORDER BY p.f, p.t, p.rule;
        CREATE INDEX links_from ON links(from_ui);
        CREATE INDEX links_to ON links(to_ui);
    """)

    log("citations per link")
    # Strongest designs first, then newest, then PMID — fully deterministic.
    kb.executescript(f"""
        CREATE TABLE link_pmids (link_id INTEGER NOT NULL, rank INTEGER NOT NULL, pmid INTEGER NOT NULL,
                                 pub_year TEXT, flags INTEGER NOT NULL, PRIMARY KEY (link_id, rank));
        INSERT INTO link_pmids
        SELECT link_id, rnk, pmid, pub_year, flags FROM (
            SELECT l.id AS link_id, ar.pmid AS pmid, SUBSTR(ar.pub_year,1,4) AS pub_year,
                   COALESCE(pt.flags,0) AS flags,
                   ROW_NUMBER() OVER (PARTITION BY l.id ORDER BY
                       (COALESCE(pt.flags,0) & 1 > 0) DESC, (COALESCE(pt.flags,0) & 2 > 0) DESC,
                       (COALESCE(pt.flags,0) & 4 > 0) DESC, ar.pub_year DESC, ar.pmid DESC) AS rnk
            FROM links l JOIN pairs p ON p.f = l.from_ui AND p.t = l.to_ui AND p.rule = l.rule
            JOIN src.articles ar ON ar.id = p.a LEFT JOIN pt ON pt.a = p.a)
        WHERE rnk <= {int(pmids_per_link)};
    """)

    log("metadata")
    shards = kb.execute("SELECT COUNT(*) FROM src.source_files").fetchone()[0]
    articles = kb.execute("SELECT COUNT(*) FROM src.articles").fetchone()[0]
    files = [r[0] for r in kb.execute("SELECT filename FROM src.source_files ORDER BY filename")]
    meta = {
        "kb_schema_version": str(KB_SCHEMA_VERSION),
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "PubMed 2026 baseline (MEDLINE/PubMed XML) + MeSH 2026 descriptors",
        "shards_included": str(shards),
        "shards_digest": hashlib.sha256("\n".join(files).encode()).hexdigest(),
        "articles_in_source": str(articles),
        "min_articles": str(min_articles),
        "pmids_per_link": str(pmids_per_link),
        "rules": "; ".join(f"{r}: A/{a} + B/{b} -> A->B" for r, a, b in RULES),
        "excluded": ", ".join(EXCLUDED_TYPES),
        "credit": CREDIT,
        "meaning": "A link records that NLM indexed articles with this qualifier pairing. "
                   "It is not a finding, a frequency, or an effect size.",
    }
    kb.execute("CREATE TABLE kb_meta (k TEXT PRIMARY KEY, v TEXT NOT NULL)")
    kb.executemany("INSERT INTO kb_meta VALUES (?,?)", meta.items())
    kb.commit()
    n_links = kb.execute("SELECT COUNT(*) FROM links").fetchone()[0]
    kb.execute("DETACH DATABASE src")
    kb.execute("VACUUM")
    kb.close()
    log(f"done: {n_links} links from {articles} articles in {shards} shards -> {out}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pubmed", required=True)
    ap.add_argument("--mesh", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--min-articles", type=int, default=3)
    ap.add_argument("--pmids-per-link", type=int, default=20)
    a = ap.parse_args()
    build(a.pubmed, a.mesh, a.out, a.min_articles, a.pmids_per_link)
    return 0


if __name__ == "__main__":
    sys.exit(main())
PYEOF
cd $KB
if [ ! -s desc2026.xml ]; then
  curl -sS -f -o desc2026.xml https://nlmpubs.nlm.nih.gov/projects/mesh/MESH_FILES/xmlmesh/desc2026.xml
fi
ls -l desc2026.xml
OUT=$KB/psi_health_kb_$(date -u +%Y%m%d_%H%M).db
chown -R paiadmin:paiadmin $KB
mkdir -p $KB/kb_tmp; chown paiadmin:paiadmin $KB/kb_tmp
sudo -u paiadmin env SQLITE_TMPDIR=$KB/kb_tmp nohup nice -n 10 ionice -c3 python3 $KB/build_kb.py \
  --pubmed $BASE/db/psi_health_pubmed_2026.db --mesh $KB/desc2026.xml --out $OUT \
  > $KB/build_kb.log 2>&1 &
sleep 3
echo "started -> $OUT"
python3 --version; sqlite3 --version
tail -3 $KB/build_kb.log
