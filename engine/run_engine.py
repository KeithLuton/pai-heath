"""Run the record engine on a folder of real documents and serve the UI.

    python engine\\run_engine.py C:\\path\\to\\records [--question "..."] [--no-ocr] [--no-browser]
                                   [--vault C:\\path\\to\\vault [--new-vault]]
                                   [--kb C:\\path\\to\\psi_health_kb.db]

With --kb, the Parliament gets authority links from the knowledge base built on
the corpus VM (knowledge/build_kb.py); without it, chains have no sources.

With --vault, original document bytes and your settings are kept in the
encrypted vault (engine/storage). The passphrase is asked for at start, or read
from PAI_HEALTH_PASSPHRASE. Without --vault nothing is written to disk.

Document dates: taken from the document itself when it states one (C-CDA,
email Date), else from a YYYY-MM-DD prefix in the file name
("2023-03-10 neurology.pdf"). Undated documents are still read, but their
statements can't be placed in time — the engine says so rather than guessing.

The tracker side still shows the synthetic demo profile until the tracker
engine exists; the scenario side is your documents, marked non-synthetic.
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import sys
import time
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from health_server import SETTING_VALUES, HealthServer  # noqa: E402
from ingest import ingest_path                    # noqa: E402
from records.floats import RecordDoc              # noqa: E402
from records.pipeline import run                  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def doc_date(doc) -> str | None:
    m = doc.metadata
    raw = m.get("document_date") or ""
    if re.fullmatch(r"\d{8}", raw):
        return f"{raw[:4]}-{raw[4:6]}-{raw[6:]}"
    name_date = re.match(r"(\d{4}-\d{2}-\d{2})", Path(doc.file_name.split(":")[-1]).name)
    return name_date.group(1) if name_date else None


def build(folder: Path, question: str | None, allow_ocr: bool, vault=None, kb=None) -> dict:
    docs, notes = [], []
    for path in sorted(p for p in folder.rglob("*") if p.is_file()):
        if vault is not None:
            vault.add_document(path.name, path.read_bytes())
        ingested, fails = ingest_path(path, allow_ocr=allow_ocr)
        notes += [f"{f.file_name}: {f.reason}" for f in fails]
        for d in ingested:
            date = doc_date(d)
            if not date:
                notes.append(f"{d.file_name}: no document date found; statements can't be placed in time")
            docs.append(RecordDoc(d.doc_id, d.metadata.get("title") or Path(d.file_name).stem, date,
                                  d.canonical_text, d.original_sha256,
                                  [(p.start, p.end, p.initial_lock) for p in d.pages]))
    retrieve = None
    if kb is not None:
        from knowledge.kb import record_seeds
        retrieve = lambda fl, q: kb.authorities_for(record_seeds(fl, q))  # noqa: E731
    out = run(docs, question, "USER", [], retrieve=retrieve)
    out["ingest_notes"] = notes
    if kb is not None:
        out["knowledge_base"] = {k: kb.meta.get(k) for k in ("built_at", "shards_included", "source", "credit")}
    return out


def settings_handlers(tracker: dict, vault=None):
    """Provider + sink for HealthServer. With a vault, settings persist and are audited."""
    def tracker_view():
        if vault is not None:
            tracker["settings"].update(vault.settings())
        return tracker, True

    def on_setting(key, value):
        if key == "data_path" and value == "LIVE":
            raise ValueError("Live mode needs the phone companion, which isn't available yet")
        if vault is not None:
            vault.set_setting(key, value, SETTING_VALUES)
        else:
            tracker["settings"][key] = value

    return tracker_view, on_setting


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("folder")
    ap.add_argument("--question")
    ap.add_argument("--no-ocr", action="store_true")
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--vault")
    ap.add_argument("--new-vault", action="store_true")
    ap.add_argument("--kb")
    a = ap.parse_args()
    vault = open_vault(Path(a.vault), a.new_vault) if a.vault else None
    kb = None
    if a.kb:
        from knowledge.kb import KnowledgeBase
        kb = KnowledgeBase(Path(a.kb))
        print(f"Knowledge base: {kb.meta.get('shards_included')} PubMed shards, built {kb.meta.get('built_at')}")
    scenario = build(Path(a.folder), a.question, not a.no_ocr, vault, kb)
    tracker = json.loads((ROOT / "src" / "data" / "tracker_demo.json").read_text(encoding="utf-8"))
    tracker_view, on_setting = settings_handlers(tracker, vault)
    server = HealthServer(ROOT / "dist", lambda: (scenario, False), tracker_view, on_setting)
    url = server.start()
    print("PAi Health:", url)
    for n in scenario["ingest_notes"]:
        print("note:", n)
    if not a.no_browser:
        webbrowser.open(url)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        server.stop()
        if vault is not None:
            vault.close()
    return 0


def open_vault(directory: Path, new: bool):
    from storage import Vault
    pw = os.environ.get("PAI_HEALTH_PASSPHRASE") or getpass.getpass("Vault passphrase: ")
    if new:
        if pw != (os.environ.get("PAI_HEALTH_PASSPHRASE") or getpass.getpass("Repeat passphrase: ")):
            raise SystemExit("Passphrases differ")
        return Vault.create(directory, pw)
    vault = Vault.open(directory, pw)
    state = vault.verify()
    if not state["valid"]:
        raise SystemExit("Vault failed verification: " + "; ".join(state["problems"][:3]))
    return vault


if __name__ == "__main__":
    sys.exit(main())
