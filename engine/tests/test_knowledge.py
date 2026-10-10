"""Knowledge base: builder on a synthetic PubMed + MeSH pair, retrieval into the Parliament.

The synthetic data is shaped like the real VM schema (psi_health_pubmed_2026.db) and
desc2026.xml. Nothing here is a real citation.
"""
import json
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from knowledge.build_kb import build, normalize          # noqa: E402
from knowledge.kb import KnowledgeBase, record_seeds     # noqa: E402
from records.floats import RecordDoc                     # noqa: E402
from records.pipeline import run                         # noqa: E402
from records.render import lint                          # noqa: E402

FIXTURE = json.loads((Path(__file__).resolve().parents[2] / "src" / "data" / "scenario_001.json").read_text())
QUESTION = "Could the metformin be causing the numbness?"

PUBMED_SCHEMA = """
CREATE TABLE source_files (id INTEGER PRIMARY KEY, filename TEXT NOT NULL UNIQUE, sha256 TEXT NOT NULL,
    schema_version TEXT NOT NULL, article_count INTEGER NOT NULL DEFAULT 0);
CREATE TABLE articles (id INTEGER PRIMARY KEY, pmid INTEGER NOT NULL UNIQUE, title TEXT NOT NULL DEFAULT '',
    journal_title TEXT NOT NULL DEFAULT '', journal_iso TEXT NOT NULL DEFAULT '', volume TEXT NOT NULL DEFAULT '',
    issue TEXT NOT NULL DEFAULT '', pub_year TEXT NOT NULL DEFAULT '', medline_date TEXT NOT NULL DEFAULT '',
    language TEXT NOT NULL DEFAULT '', country TEXT NOT NULL DEFAULT '', citation_status TEXT NOT NULL DEFAULT '',
    source_file_id INTEGER NOT NULL);
CREATE TABLE abstracts (id INTEGER PRIMARY KEY, article_id INTEGER NOT NULL, section_index INTEGER NOT NULL,
    label TEXT NOT NULL DEFAULT '', nlm_category TEXT NOT NULL DEFAULT '', text TEXT NOT NULL);
CREATE TABLE mesh_headings (id INTEGER PRIMARY KEY, article_id INTEGER NOT NULL, descriptor_ui TEXT NOT NULL DEFAULT '',
    descriptor TEXT NOT NULL, major_topic TEXT NOT NULL DEFAULT '');
CREATE TABLE mesh_qualifiers (id INTEGER PRIMARY KEY, mesh_heading_id INTEGER NOT NULL,
    qualifier_ui TEXT NOT NULL DEFAULT '', qualifier TEXT NOT NULL, major_topic TEXT NOT NULL DEFAULT '');
CREATE TABLE publication_types (id INTEGER PRIMARY KEY, article_id INTEGER NOT NULL,
    publication_type_ui TEXT NOT NULL DEFAULT '', publication_type TEXT NOT NULL);
"""

DESC = {"D001": ("Metformin", ["Dimethylbiguanidine"]),
        "D002": ("Vitamin B 12 Deficiency", ["Vitamin B12 Deficiency", "Cobalamin Deficiency"]),
        "D003": ("Hypesthesia", ["Numbness", "Hypoesthesia"]),
        "D004": ("Diabetic Neuropathies", ["Diabetic Neuropathy"]),
        "D005": ("Vitamin B 12", ["Vitamin B12", "Cobalamin"]),
        "D006": ("Acidosis, Lactic", ["Lactic Acidosis"]),
        "D007": ("Humans", [])}


def mesh_xml() -> str:
    recs = []
    for ui, (name, terms) in DESC.items():
        term_xml = "".join(f"<Term><TermUI>T{ui}{i}</TermUI><String>{t}</String></Term>"
                           for i, t in enumerate([name] + terms))
        recs.append(f"""<DescriptorRecord DescriptorClass="1"><DescriptorUI>{ui}</DescriptorUI>
          <DescriptorName><String>{name}</String></DescriptorName>
          <AllowableQualifiersList><AllowableQualifier><QualifierReferredTo><QualifierUI>Q000009</QualifierUI>
            <QualifierName><String>adverse effects</String></QualifierName></QualifierReferredTo>
            <Abbreviation>AE</Abbreviation></AllowableQualifier></AllowableQualifiersList>
          <PharmacologicalActionList><PharmacologicalAction><DescriptorReferredTo><DescriptorUI>D999</DescriptorUI>
            <DescriptorName><String>Hypoglycemic Agents</String></DescriptorName></DescriptorReferredTo>
            </PharmacologicalAction></PharmacologicalActionList>
          <ConceptList><Concept PreferredConceptYN="Y"><ConceptUI>M{ui}</ConceptUI>
            <ConceptName><String>{name}</String></ConceptName><TermList>{term_xml}</TermList></Concept></ConceptList>
        </DescriptorRecord>""")
    return '<?xml version="1.0"?><DescriptorRecordSet LanguageCode="eng">' + "".join(recs) + "</DescriptorRecordSet>"


# (from descriptor, from qualifier, to descriptor, to qualifier, publication types, humans?, year)
ARTICLES = (
    [("D001", "adverse effects", "D002", "chemically induced", ["Meta-Analysis"], True, "2019"),
     ("D001", "adverse effects", "D002", "chemically induced", ["Randomized Controlled Trial"], True, "2010"),
     ("D001", "adverse effects", "D002", "chemically induced", ["Journal Article"], True, "2005"),
     ("D001", "adverse effects", "D002", "chemically induced", ["Journal Article"], False, "2001"),
     ("D001", "adverse effects", "D002", "chemically induced", ["Retracted Publication"], True, "2015")]
    + [("D002", "complications", "D003", "etiology", ["Case Reports"], True, y) for y in ("1999", "2008", "2020")]
    + [("D001", "adverse effects", "D006", "chemically induced", ["Review"], True, y) for y in ("2000", "2003", "2012")]
    + [("D004", "complications", "D003", "etiology", ["Journal Article"], True, y) for y in ("2011", "2016")]
    # wrong direction for the rule: must not create D002 -> D001
    + [("D002", "chemically induced", "D001", "adverse effects", ["Journal Article"], True, "2018")] * 0
)


def make_pubmed(path: Path) -> None:
    db = sqlite3.connect(path)
    db.executescript(PUBMED_SCHEMA)
    db.execute("INSERT INTO source_files VALUES (1, 'pubmed26n0001.xml.gz', 'x', '1', 0)")
    h = q = p = 0
    for i, (fd, fq, td, tq, types, human, year) in enumerate(ARTICLES, 1):
        db.execute("INSERT INTO articles (id, pmid, title, pub_year, source_file_id) VALUES (?,?,?,?,1)",
                   (i, 90000000 + i, "SYNTHETIC", year))
        db.execute("INSERT INTO abstracts (article_id, section_index, text) VALUES (?,0,'SYNTHETIC ABSTRACT TEXT')", (i,))
        heads = [(fd, fq), (td, tq)] + ([("D007", None)] if human else [])
        for d, ql in heads:
            h += 1
            db.execute("INSERT INTO mesh_headings VALUES (?,?,?,?,'N')", (h, i, d, DESC[d][0]))
            if ql:
                q += 1
                db.execute("INSERT INTO mesh_qualifiers VALUES (?,?,'',?, 'N')", (q, h, ql))
        for t in types:
            p += 1
            db.execute("INSERT INTO publication_types VALUES (?,?,'',?)", (p, i, t))
    db.commit()
    db.close()


class KBCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="pai-kb-test-"))
        make_pubmed(cls.tmp / "pubmed.db")
        (cls.tmp / "desc.xml").write_text(mesh_xml())
        build(str(cls.tmp / "pubmed.db"), str(cls.tmp / "desc.xml"), str(cls.tmp / "kb.db"),
              min_articles=3, pmids_per_link=20)
        cls.kb = KnowledgeBase(cls.tmp / "kb.db")

    @classmethod
    def tearDownClass(cls):
        cls.kb.close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def docs(self):
        return [RecordDoc(k, v["title"], v["date"], v["text"], v["sha256"]) for k, v in FIXTURE["documents"].items()]

    def run_engine(self):
        return run(self.docs(), QUESTION, "USER", [],
                   retrieve=lambda fl, q: self.kb.authorities_for(record_seeds(fl, q)))


class TestBuilder(KBCase):
    def links(self):
        return {(r[0], r[1], r[2]): r[3] for r in self.kb.db.execute(
            "SELECT from_ui, to_ui, rule, n_articles FROM links")}

    def test_rules_threshold_and_retraction(self):
        self.assertEqual(self.links(), {("D001", "D002", "ADVERSE_EFFECT"): 4,      # retracted one excluded
                                         ("D002", "D003", "COMPLICATION"): 3,
                                         ("D001", "D006", "ADVERSE_EFFECT"): 3})   # D004->D003 below 3

    def test_counts_by_design(self):
        r = self.kb.db.execute("SELECT * FROM links WHERE from_ui='D001' AND to_ui='D002'").fetchone()
        self.assertEqual((r["n_meta_analysis"], r["n_rct"], r["n_humans"], r["first_year"], r["last_year"]),
                         (1, 1, 3, "2001", "2019"))

    def test_citations_strongest_first(self):
        lid = self.kb.db.execute("SELECT id FROM links WHERE from_ui='D001' AND to_ui='D002'").fetchone()[0]
        years = [r[0] for r in self.kb.db.execute("SELECT pub_year FROM link_pmids WHERE link_id=? ORDER BY rank", (lid,))]
        self.assertEqual(years, ["2019", "2010", "2005", "2001"])

    def test_no_titles_or_abstracts_shipped(self):
        raw = (self.tmp / "kb.db").read_bytes()
        self.assertNotIn(b"SYNTHETIC ABSTRACT TEXT", raw)
        self.assertNotIn(b"SYNTHETIC", raw)

    def test_mesh_parser_ignores_nested_names(self):
        names = dict(self.kb.db.execute("SELECT ui, name FROM descriptors"))
        self.assertEqual(names["D001"], "Metformin")
        self.assertNotIn("D999", names)
        self.assertNotIn("Hypoglycemic Agents", {r[0] for r in self.kb.db.execute("SELECT term FROM entry_terms")})

    def test_meta_and_credit(self):
        self.assertIn("National Library of Medicine", self.kb.meta["credit"])
        self.assertEqual(self.kb.meta["shards_included"], "1")

    def test_normalize(self):
        self.assertEqual(normalize("Vitamin B 12 Deficiency"), normalize("vitamin b12 deficiency"))


class TestRetrieval(KBCase):
    def test_resolution_is_exact(self):
        self.assertEqual(self.kb.resolve("numbness"), {"D003"})
        self.assertEqual(self.kb.resolve("metformin"), {"D001"})
        self.assertEqual(self.kb.resolve("numb"), set())

    def test_off_record_dead_ends_not_offered(self):
        fl = run(self.docs(), None)["expected"]["floats"]
        ids = {a["mesh"]["to"]["ui"] for a in self.kb.authorities_for(record_seeds(fl, QUESTION))}
        self.assertNotIn("D006", ids)   # lactic acidosis: not in the record, leads nowhere in it

    def test_chain_through_parliament(self):
        out = self.run_engine()
        stir = out["expected"]["stir"]
        chain = [(l["from"], l["to"], l["source"]["type"]) for l in stir["chain"]]
        self.assertEqual(chain[0][0], "metformin")
        self.assertEqual(chain[0][1], "Vitamin B12 Deficiency")
        self.assertTrue(all(l["source"]["id"].startswith("PM-") or l["source"]["type"] == "CLINICIAN"
                            for l in stir["chain"]))
        self.assertEqual(chain[-1][1], "numbness")
        self.assertTrue(any(s["cites"].startswith("PM-") for s in stir["support"]))

    def test_rendering_passes_lint(self):
        out = self.run_engine()
        self.assertEqual(lint(out["expected"]["rendering"]["reference_text"], QUESTION), [])

    def test_deterministic(self):
        a, b = self.run_engine(), self.run_engine()
        self.assertEqual(json.dumps(a, sort_keys=True), json.dumps(b, sort_keys=True))

    def test_authority_says_what_it_is(self):
        auth = self.kb.authorities_for(["metformin", "numbness"])
        self.assertTrue(auth)
        for a in auth:
            self.assertIn("not a finding", a["text"])
            self.assertEqual(a["level"], "POPULATION")
            self.assertTrue(a["pmids"])
            self.assertIn("National Library of Medicine", a["credit"])


if __name__ == "__main__":
    unittest.main()
