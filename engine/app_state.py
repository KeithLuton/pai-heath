"""PAi Health application state: users, AI setup, ingest and the double-check.

One process, one unlocked user at a time. Machine-level things (hardware,
private models, the knowledge base) live under app_data_root(); everything
about a person (documents, settings, API key, check results) lives in that
person's encrypted vault.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
import shutil
import tempfile
import threading
import time
import traceback
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional

from ai import local_ai
from ai.catalog import CATALOG
from ai.hardware import app_data_root, scan_hardware
from ai.providers import PROVIDERS, ExternalModel, ProviderError
import billing
from ingest import ingest_bytes
from ingest.ingest import _sniff, _zip_children
from ingest.model import IngestedDocument, IngestedPage, PageQuality, StructuredRecord
from records import floats as F
from records.floats import RecordDoc
from records.pipeline import run as run_pipeline
from review import check as C
import runtime
from storage import Vault, VaultLocked

# §17 (v0.4.2): external AI may see document content only for double-checking uploads,
# only what the user switched on, and only after they accepted this exact text.
CONSENT_VERSION = "PSI-INGEST-CONSENT-v1"
CONSENT_TEXT = ("I understand that the parts I switched on for {provider} leave this computer and are processed "
                "by {provider} under its own terms: for double-checking uploads, page images with the text read "
                "from them and/or the lines each value came from; for investigating my questions, the records "
                "and source text it looks up while exploring. Whole documents are never sent. Each call is "
                "written to my audit log. I can turn this off at any time.")

SETTING_VALUES = {"data_path": {"REVIEW", "LIVE"}, "device_dose_confirmation": {True, False},
                  "ai_mode": {"None", "PAi", "PSi"}}


class AppError(ValueError):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


# ------------------------------------------------------------------ helpers
def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return s[:40] or "user"


def doc_to_dict(d: IngestedDocument) -> dict:
    return asdict(d)


def doc_from_dict(x: dict) -> IngestedDocument:
    pages = [IngestedPage(**dict(p, quality=PageQuality(**p["quality"]))) for p in x["pages"]]
    structured = [StructuredRecord(**r) for r in x.get("structured", [])]
    return IngestedDocument(**dict(x, pages=pages, structured=structured))


def doc_date(d: IngestedDocument) -> Optional[str]:
    raw = d.metadata.get("document_date") or ""
    if re.fullmatch(r"\d{8}", raw):
        return f"{raw[:4]}-{raw[4:6]}-{raw[6:]}"
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        return raw
    m = re.match(r"(\d{4}-\d{2}-\d{2})", Path(d.file_name.split(":")[-1]).name)
    return m.group(1) if m else None


def record_doc(d: IngestedDocument) -> RecordDoc:
    return RecordDoc(d.doc_id, d.metadata.get("title") or Path(d.file_name.split(":")[-1]).stem, doc_date(d),
                     d.canonical_text, d.original_sha256, [(p.start, p.end, p.initial_lock) for p in d.pages])


class AuditedModel:
    """Wraps an external model: every call is written to the vault's audit chain (§17), never its content."""

    def __init__(self, inner: ExternalModel, vault: Vault, purpose: str):
        self.inner, self.vault, self.purpose = inner, vault, purpose
        self.label = inner.label

    def chat(self, messages: list, max_tokens: int = 800, temperature: float = 0.0) -> str:
        raw = json.dumps(messages)
        images = raw.count('"image_url"')
        try:
            out = self.inner.chat(messages, max_tokens, temperature)
            ok = True
            return out
        except Exception:
            ok = False
            raise
        finally:
            self.vault.audit.append({"event": "EXTERNAL_AI_CALL", "provider": self.inner.provider,
                                     "model": self.inner.model, "purpose": self.purpose,
                                     "bytes_sent": len(raw.encode()), "images_sent": images, "ok": ok,
                                     "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})

    def close(self) -> None:
        pass


# ------------------------------------------------------------------ jobs
class Jobs:
    def __init__(self):
        self._lock = threading.Lock()
        self._jobs: Dict[str, dict] = {}
        self._queue_lock = threading.Lock()     # one heavy job (model load) at a time

    def start(self, kind: str, fn: Callable[[Callable[[str, int, int], None]], dict], serial: bool = True,
              docs: Optional[List[str]] = None) -> str:
        jid = f"J-{secrets.token_hex(6)}"
        with self._lock:
            self._jobs[jid] = {"id": jid, "kind": kind, "state": "QUEUED", "message": "", "done": 0, "total": 0,
                               "result": None, "error": None, "docs": list(docs or [])}

        def update(message: str = "", done: int = 0, total: int = 0) -> None:
            with self._lock:
                self._jobs[jid].update(message=message, done=done, total=total)

        def worker() -> None:
            lock = self._queue_lock if serial else threading.Lock()
            with lock:
                with self._lock:
                    self._jobs[jid]["state"] = "RUNNING"
                try:
                    result = fn(update)
                    with self._lock:
                        self._jobs[jid].update(state="DONE", result=result)
                except Exception as exc:
                    traceback.print_exc()
                    with self._lock:
                        self._jobs[jid].update(state="FAILED", error=str(exc))

        threading.Thread(target=worker, daemon=True).start()
        return jid

    def get(self, jid: str) -> dict:
        with self._lock:
            if jid not in self._jobs:
                raise AppError(404, "No such job")
            return dict(self._jobs[jid])

    def active(self) -> List[dict]:
        with self._lock:
            return [dict(j) for j in self._jobs.values() if j["state"] in ("QUEUED", "RUNNING")]


# ------------------------------------------------------------------ the app
class App:
    def __init__(self, root: Optional[Path] = None, kb_path: Optional[Path] = None,
                 transport=None):
        self.root = Path(root) if root else app_data_root()
        (self.root / "users").mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.vault: Optional[Vault] = None
        self.user: Optional[str] = None
        self.user_name: Optional[str] = None
        self.jobs = Jobs()
        self._hw = None
        self._scenario = None
        self._transport = transport           # tests inject a fake provider transport
        from companion.service import Companion
        self.companion = Companion(self)
        self._lan = None
        self.kb = None
        kb_file = kb_path
        if kb_file is None and (self.root / "knowledge").is_dir():
            found = sorted((self.root / "knowledge").glob("psi_health_kb*.db"))
            kb_file = found[-1] if found else None
        if kb_file is None:
            kb_file = runtime.bundled_kb()        # shipped read-only inside the install folder
        if kb_file and Path(kb_file).is_file():
            from knowledge.kb import KnowledgeBase
            self.kb = KnowledgeBase(Path(kb_file))

    # ---------------- machine
    def system(self, rescan: bool = False) -> dict:
        if self._hw is None or rescan:
            self._hw = scan_hardware(self.root)
        out = local_ai.options(self._hw)
        out["providers"] = [{"id": k, "label": v["label"], "key_hint": v["key_hint"], "keys_url": v["keys_url"]}
                            for k, v in PROVIDERS.items()]
        return out

    def install_local(self, model_key: str, checker_key: Optional[str]) -> str:
        hw = self._hw or scan_hardware(self.root)
        model = next((m for m in CATALOG if m.key == model_key), None) if model_key else None
        checker = next((c for c in local_ai.CHECKERS if c.key == checker_key), None) if checker_key else None
        if model_key and not model:
            raise AppError(400, "Unknown model")
        if checker_key and not checker:
            raise AppError(400, "Unknown checker")
        if not model and not checker:
            raise AppError(400, "Choose a model or a checker")

        def job(update):
            done = {}
            if model:
                update(f"Downloading {model.label}…")
                local_ai.provision_analysis(model, hw, lambda d, t: update(f"Downloading {model.label}…", d, t))
                update(f"Testing {model.label}…")
                m = local_ai.LocalModel("analysis")
                m.start()
                m.close()
                done["analysis"] = model.label
            if checker:
                update(f"Downloading {checker.label}…")
                local_ai.provision_checker(checker, hw, lambda d, t: update(f"Downloading {checker.label}…", d, t))
                update(f"Testing {checker.label}…")
                m = local_ai.LocalModel("checker")
                m.start()
                m.close()
                done["checker"] = checker.label
            return done
        return self.jobs.start("INSTALL", job)

    # ---------------- users
    def users(self) -> List[dict]:
        out = []
        for d in sorted((self.root / "users").iterdir()):
            meta = d / "user.json"
            if meta.is_file():
                out.append({"id": d.name, "name": json.loads(meta.read_text(encoding="utf-8")).get("name", d.name)})
        return out

    def create_user(self, name: str, passphrase: str, notice: Optional[str] = None) -> dict:
        if not name.strip():
            raise AppError(400, "Enter a name")
        slug = _slug(name)
        d = self.root / "users" / slug
        n = 2
        while d.exists():
            d = self.root / "users" / f"{slug}-{n}"
            n += 1
        try:
            vault = Vault.create(d / "vault", passphrase)
        except ValueError as exc:
            raise AppError(400, str(exc)) from exc
        (d / "user.json").write_text(json.dumps({"name": name.strip()}), encoding="utf-8")
        vault.set_setting("ai_mode", "PAi" if self._local_ready() else "None", SETTING_VALUES)
        vault.set_setting("data_path", "REVIEW", SETTING_VALUES)
        vault.set_setting("device_dose_confirmation", False, SETTING_VALUES)
        if notice:
            if notice != runtime.NOTICE_VERSION:
                raise AppError(400, "Out-of-date data notice; reload the app")
            vault.audit.append({"event": "DATA_NOTICE_ACCEPTED", "notice": notice,
                                "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
        self._open(d.name, name.strip(), vault)
        return {"id": d.name, "name": name.strip()}

    def unlock(self, uid: str, passphrase: str) -> dict:
        d = self.root / "users" / uid
        if not (d / "user.json").is_file():
            raise AppError(404, "No such user")
        try:
            vault = Vault.open(d / "vault", passphrase)
        except (VaultLocked, ValueError) as exc:
            raise AppError(403, "Wrong passphrase") from exc
        name = json.loads((d / "user.json").read_text(encoding="utf-8")).get("name", uid)
        self._open(uid, name, vault)
        return {"id": uid, "name": name}

    def _open(self, uid: str, name: str, vault: Vault) -> None:
        with self.lock:
            if self.vault:
                self.vault.close()
            self.vault, self.user, self.user_name = vault, uid, name
            self._scenario = None

    def lock_user(self) -> None:
        with self.lock:
            if self.vault:
                self.vault.close()
            self.vault = self.user = self.user_name = None
            self._scenario = None

    def close_models(self) -> None:
        local_ai.close_all()

    def need_vault(self) -> Vault:
        if not self.vault:
            raise AppError(401, "Unlock a user first")
        return self.vault

    def change_passphrase(self, old: str, new: str) -> dict:
        with self.lock:
            vault = self.need_vault()
            try:
                vault.change_passphrase(old, new)
            except VaultLocked as exc:
                raise AppError(403, "Wrong current passphrase") from exc
            except RuntimeError as exc:
                raise AppError(409, str(exc)) from exc
            return {"ok": True}

    # ---------------- AI configuration
    def _local_ready(self) -> bool:
        return bool(local_ai.read_installation("analysis").get("files_present")
                    or local_ai.read_installation("checker").get("files_present"))

    def ai_config(self) -> dict:
        v = self.need_vault()
        r = next(v.iter("AI_CONFIG"), None)
        return dict(r.body) if r else {}

    def external_models(self, provider: str, api_key: str) -> dict:
        try:
            m = ExternalModel(provider, api_key, **({"transport": self._transport} if self._transport else {}))
            ids = m.list_models()
        except ProviderError as exc:
            raise AppError(400, str(exc)) from exc
        return {"models": ids, "suggested": ExternalModel.preferred(provider, ids)}

    def consent_text(self, provider: str) -> dict:
        if provider not in PROVIDERS:
            raise AppError(400, "Unknown provider")
        return {"version": CONSENT_VERSION, "text": CONSENT_TEXT.format(provider=PROVIDERS[provider]["label"])}

    def save_external(self, provider: str, model: str, api_key: str, allow_pages: bool, allow_values: bool,
                      consent: Optional[str] = None, allow_investigate: bool = False) -> dict:
        v = self.need_vault()
        prior = next(v.iter("AI_CONFIG"), None)
        key = api_key or (prior.body.get("api_key") if prior and prior.body.get("provider") == provider else "")
        if provider not in PROVIDERS or not model or not key:
            raise AppError(400, "Choose a provider and model, and enter the API key")
        allow_pages, allow_values, allow_inv = bool(allow_pages), bool(allow_values), bool(allow_investigate)
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        sharing = allow_pages or allow_values or allow_inv
        had = prior.body.get("consent") if prior else None
        same_scope = bool(had and prior.body.get("provider") == provider
                          and (not allow_pages or prior.body.get("allow_pages"))
                          and (not allow_values or prior.body.get("allow_values"))
                          and (not allow_inv or prior.body.get("allow_investigate")))
        if sharing and not same_scope and consent != CONSENT_VERSION:
            raise AppError(409, "Sending document content to an external provider needs your consent first")
        record = None
        if sharing:
            record = had if same_scope else {"version": CONSENT_VERSION, "at": now, "provider": provider,
                                             "scope": [n for n, on in (("pages", allow_pages), ("values", allow_values),
                                                                       ("investigate", allow_inv)) if on],
                                             "text_sha256": hashlib.sha256(self.consent_text(provider)["text"].encode()).hexdigest()}
        body = {"provider": provider, "model": model, "api_key": key,
                "allow_pages": allow_pages, "allow_values": allow_values, "allow_investigate": allow_inv,
                "consent": record}
        v.put("AI_CONFIG", body, supersedes=prior.id if prior else None)
        v.audit.append({"event": "AI_CONFIG_CHANGED", "provider": provider, "model": model,
                        "allow_pages": allow_pages, "allow_values": allow_values,
                        "allow_investigate": allow_inv, "at": now})
        if sharing and not same_scope:
            v.audit.append({"event": "CONSENT_GIVEN", "consent_version": CONSENT_VERSION, "provider": provider,
                            "scope": record["scope"], "text_sha256": record["text_sha256"], "at": now})
        if had and not sharing:
            v.audit.append({"event": "CONSENT_WITHDRAWN", "provider": had.get("provider"), "at": now})
        return self.ai_public()

    def ai_public(self) -> dict:
        """AI state for the UI. Never includes the API key."""
        cfg = self.ai_config() if self.vault else {}
        settings = self.vault.settings() if self.vault else {}
        a, c = local_ai.read_installation("analysis"), local_ai.read_installation("checker")
        return {"mode": settings.get("ai_mode", "None"),
                "local": {"analysis": a.get("model_label") if a.get("files_present") else None,
                          "checker": c.get("model_label") if c.get("files_present") else None,
                          "runtime": bool(local_ai.find_runtime())},
                "external": ({"provider": cfg["provider"], "label": PROVIDERS[cfg["provider"]]["label"],
                              "model": cfg["model"], "allow_pages": cfg.get("allow_pages", False),
                              "allow_values": cfg.get("allow_values", False),
                              "allow_investigate": cfg.get("allow_investigate", False),
                              "key_set": bool(cfg.get("api_key")),
                              "consent": cfg.get("consent")}
                             if cfg else None)}

    def _models(self):
        """(page_model, page_policy, value_model, value_policy) for the current mode."""
        v = self.need_vault()
        mode = v.settings().get("ai_mode", "None")
        if mode == "PAi":
            checker, analysis = local_ai.LocalModel("checker"), local_ai.LocalModel("analysis")
            pm = checker if checker.installed else None
            vm = analysis if analysis.installed else pm
            return (pm, C.Policy(True, True, "" if pm else "No private page checker is installed."),
                    vm, C.Policy(True, True, "" if vm else "No private model is installed."))
        if mode == "PSi":
            cfg = self.ai_config()
            if not cfg:
                off = C.Policy(False, False, "No external AI provider is set up.")
                return None, off, None, off
            ext = ExternalModel(cfg["provider"], cfg["api_key"], cfg["model"],
                                **({"transport": self._transport} if self._transport else {}))
            why = f"You haven't allowed {PROVIDERS[cfg['provider']]['label']} to see "
            consented = bool(cfg.get("consent")) and cfg["consent"].get("version") == CONSENT_VERSION
            pages = consented and cfg.get("allow_pages", False)
            values = consented and cfg.get("allow_values", False)
            return (AuditedModel(ext, v, "INGEST_PAGE_CHECK"), C.Policy(pages, pages, why + "page images."),
                    AuditedModel(ext, v, "INGEST_VALUE_CHECK"), C.Policy(values, values, why + "document lines."))
        off = C.Policy(False, False, "AI is off. Turn on private AI or an API provider to double-check.")
        return None, off, None, off

    # ---------------- ingest
    def upload(self, name: str, data: bytes, options: Optional[dict] = None) -> dict:
        v = self.need_vault()
        if not data:
            raise AppError(400, "Empty file")
        from tracker import device_store as DS
        kind = DS.detect(name, data)
        if kind:
            options = {k: val for k, val in (options or {}).items() if val}
            missing = [k for k in DS.needs(kind) if not options.get(k)]
            if missing:
                return {"device": {"kind": kind, "label": DS.LABEL[kind], "needs": missing}, "documents": [],
                        "failures": [], "job": None}
            return {"device": {"kind": kind, "label": DS.LABEL[kind], "needs": []}, "documents": [], "failures": [],
                    "job": self.import_device(name, data, kind, options)}
        parts = [(name, data)]
        if data[:4] == b"PK\x03\x04" and _sniff(data, name) == "zip":
            # Each member is kept as its own original, so its pages can be shown and checked.
            parts = [(f"{name}:{n}", b) for n, b in _zip_children(data)]
        added, failures, new_docs = [], [], []
        for part_name, blob in parts:
            vid, is_new = v.add_document(part_name, blob)
            docs, fails = ingest_bytes(blob, part_name)
            failures += [{"file_name": f.file_name, "reason": f.reason} for f in fails]
            for d in docs:
                ref = v.tag("document-text", d.doc_id)
                if next(v.iter("DOCUMENT_TEXT", ref=ref), None) and not is_new:
                    added.append({"doc_id": d.doc_id, "file_name": d.file_name, "duplicate": True})
                    continue
                v.put("DOCUMENT_TEXT", {"doc": doc_to_dict(d), "original_id": vid if d.parent_id is None else None},
                      ref=ref, meta={"document_id": d.doc_id})
                added.append({"doc_id": d.doc_id, "file_name": d.file_name, "duplicate": False})
                new_docs.append(d.doc_id)
        self._scenario = None
        job = self.start_check(new_docs) if new_docs else None
        return {"documents": added, "failures": failures, "job": job}

    def _docs(self) -> Dict[str, dict]:
        v = self.need_vault()
        return {r.meta["document_id"]: r.body for r in v.iter("DOCUMENT_TEXT")}

    def _doc(self, doc_id: str):
        body = self._docs().get(doc_id)
        if not body:
            raise AppError(404, "No such document")
        doc = doc_from_dict(body["doc"])
        original = self.vault.get(body["original_id"]).body if body.get("original_id") else b""
        return doc, original

    def _reports(self) -> Dict[str, dict]:
        return {r.meta["document_id"]: r.body for r in self.need_vault().iter("INGEST_CHECK")}

    def _resolutions(self) -> Dict[str, Dict[str, str]]:
        out: Dict[str, Dict[str, str]] = {}
        for r in self.need_vault().iter("INGEST_RESOLUTION"):
            out.setdefault(r.body["doc_id"], {})[r.body["target"]] = r.body["resolution"]
        return out

    def start_check(self, doc_ids: List[str]) -> str:
        v = self.need_vault()

        def job(update):
            pm, pp, vm, vp = self._models()
            results = {}
            billing_docs = set(self._billing_confirmed())
            try:
                for n, did in enumerate(doc_ids, 1):
                    update(f"Double-checking document {n} of {len(doc_ids)}…", n - 1, len(doc_ids))
                    doc, original = self._doc(did)
                    floats = [] if did in billing_docs else F.extract([record_doc(doc)])[0]
                    rep = C.run_check(doc, original, [F.public(f) for f in floats], pm, pp, vm, vp,
                                      lambda png: "data:image/png;base64," + base64.b64encode(png).decode())
                    ref = v.tag("ingest-check", did)
                    prior = next(v.iter("INGEST_CHECK", ref=ref), None)
                    v.put("INGEST_CHECK", rep, ref=ref, meta={"document_id": did}, supersedes=prior.id if prior else None)
                    results[did] = rep["summary"]
            finally:
                for m in (pm, vm):
                    if m is not None:
                        m.close()
                self._scenario = None
            update("Done", len(doc_ids), len(doc_ids))
            return results
        return self.jobs.start("CHECK", job, docs=doc_ids)

    def resolve(self, doc_id: str, target: str, resolution: str) -> dict:
        v = self.need_vault()
        if resolution not in C.RESOLUTIONS or not re.fullmatch(r"(page:\d+|item:F-[0-9a-f]+)", target):
            raise AppError(400, "Invalid resolution")
        self._doc(doc_id)
        ref = v.tag("ingest-resolution", f"{doc_id}|{target}")
        prior = next(v.iter("INGEST_RESOLUTION", ref=ref), None)
        v.put("INGEST_RESOLUTION", {"doc_id": doc_id, "target": target, "resolution": resolution},
              ref=ref, supersedes=prior.id if prior else None)
        self._scenario = None
        return self.document(doc_id)

    def documents(self) -> List[dict]:
        reports, res = self._reports(), self._resolutions()
        running = {d for j in self.jobs.active() if j["kind"] == "CHECK" for d in j["docs"]}
        billing_conf = self._billing_confirmed()
        out = []
        for did, body in self._docs().items():
            d = body["doc"]
            rep = reports.get(did)
            issues = C.open_issues(rep, res.get(did, {})) if rep else []
            status = ("CHECKING" if did in running else "NOT_CHECKED_YET" if rep is None
                      else "NEEDS_REVIEW" if issues
                      else "NOT_CHECKED" if rep["summary"]["not_checked"] and not (rep["summary"]["items_agree"]
                                                                                 or rep["summary"]["pages_agree"])
                      else "CHECKED")
            bp = billing_conf.get(did)
            out.append({"billing": {"profile_id": bp["profile_id"] if bp else None,
                                    "candidates": billing.classify_text(d["canonical_text"])},
                        "doc_id": did, "file_name": d["file_name"], "media_type": d["media_type"],
                        "pages": len(d["pages"]), "date": doc_date(doc_from_dict(d)),
                        "summary": rep["summary"] if rep else None, "open_issues": len(issues), "status": status,
                        "checkers": rep["checkers"] if rep else None, "warnings": d.get("warnings", [])})
        return sorted(out, key=lambda x: (x["date"] or "", x["file_name"]))

    def document(self, doc_id: str) -> dict:
        doc, original = self._doc(doc_id)
        rep = self._reports().get(doc_id)
        res = self._resolutions().get(doc_id, {})
        is_billing = doc_id in self._billing_confirmed()
        floats = [] if is_billing else [F.public(f) for f in F.extract([record_doc(doc)])[0]]
        C.apply_locks(floats, {doc_id: rep} if rep else {}, {doc_id: res})
        has_images = bool(original) and (doc.media_type == "application/pdf" or doc.media_type.startswith("image"))
        return {"doc_id": doc_id, "file_name": doc.file_name, "media_type": doc.media_type, "date": doc_date(doc),
                "warnings": doc.warnings,
                "pages": [{"number": p.number, "method": p.method, "text": p.text, "start": p.start, "end": p.end,
                           "flags": p.quality.flags, "ocr_confidence": p.quality.ocr_mean_confidence,
                           "has_image": has_images} for p in doc.pages],
                "report": rep, "resolutions": res, "issues": C.open_issues(rep, res) if rep else [],
                "floats": floats, "billing": self._billing(doc, original)}

    # ---------------- billing (billing/, ingest/billing_profiles.py)
    def _billing_confirmed(self) -> Dict[str, dict]:
        out: Dict[str, dict] = {}
        for r in self.need_vault().iter("BILLING_PROFILE"):
            if r.body.get("profile_id"):
                out[r.body["doc_id"]] = r.body
            else:
                out.pop(r.body["doc_id"], None)
        return out

    def _billing(self, doc, original: bytes) -> dict:
        cls = billing.classify(doc, original)
        confirmed = self._billing_confirmed().get(doc.doc_id)
        ext = billing.extract(doc, original, confirmed) if (confirmed or cls["fhir"]) else None
        return dict(cls, confirmed=confirmed, extraction=ext)

    def billing_profiles(self) -> List[dict]:
        return billing.profiles()

    def billing_confirm(self, doc_id: str, profile_id: Optional[str]) -> dict:
        v = self.need_vault()
        if profile_id is not None and profile_id not in billing.PAPER:
            known = profile_id in {p["profile_id"] for p in billing.profiles()}
            raise AppError(400, "There's no reader for that billing format yet." if known else "Unknown billing format")
        doc, original = self._doc(doc_id)
        if profile_id and billing.classify(doc, original)["fhir"]:
            raise AppError(400, "This is a FHIR financial resource; its type is read from the file itself.")
        ref = v.tag("billing-profile", doc_id)
        prior = next(v.iter("BILLING_PROFILE", ref=ref), None)
        v.put("BILLING_PROFILE", {"doc_id": doc_id, "profile_id": profile_id, "by": self.user_name,
                                  "at": datetime.now().astimezone().isoformat(timespec="seconds")},
              ref=ref, meta={"document_id": doc_id}, supersedes=prior.id if prior else None)
        self._scenario = None
        return self.document(doc_id)

    def page_png(self, doc_id: str, page: int) -> bytes:
        from review.pages import render_png
        doc, original = self._doc(doc_id)
        png = render_png(original, doc.media_type, page) if original else None
        if png is None:
            raise AppError(404, "No page image")
        return png

    # ---------------- AI investigation (§14 v0.4.3)
    def _investigator(self):
        v = self.need_vault()
        mode = v.settings().get("ai_mode", "None")
        if mode == "PAi":
            for role in ("analysis", "checker"):
                m = local_ai.LocalModel(role)
                if m.installed:
                    return m
            raise AppError(400, "No private model is installed")
        if mode == "PSi":
            cfg = self.ai_config()
            ok = cfg and cfg.get("allow_investigate") and (cfg.get("consent") or {}).get("version") == CONSENT_VERSION
            if not ok:
                raise AppError(409, f"Allow {PROVIDERS[cfg['provider']]['label'] if cfg else 'your provider'} to "
                                    "investigate your questions in AI setup first (it needs your consent)")
            ext = ExternalModel(cfg["provider"], cfg["api_key"], cfg["model"],
                                **({"transport": self._transport} if self._transport else {}))
            return AuditedModel(ext, v, "INVESTIGATION")
        raise AppError(400, "AI is off. Turn on private AI or an API provider in Settings.")

    def investigate(self, question: str) -> str:
        from investigate.agent import Workspace, investigate
        v = self.need_vault()
        question = (question or "").strip()
        if not question or len(question) > 2000:
            raise AppError(400, "Enter a question (up to 2000 characters)")
        model = self._investigator()

        def job(update):
            ws = Workspace(self.scenario(), self.kb)
            try:
                inv = investigate(model, ws, question,
                                  lambda st: update(f"Step {st.n}: {st.action.get('tool', '?')}", st.n, 14))
            finally:
                model.close()
            rec = inv.as_dict()
            rid = v.put("INVESTIGATION", rec)
            rec["id"] = rid
            return rec
        return self.jobs.start("INVESTIGATE", job)

    def investigations(self) -> List[dict]:
        out = []
        for r in self.need_vault().iter("INVESTIGATION"):
            b = dict(r.body)
            b["id"], b["saved_at"] = r.id, r.created_at
            out.append(b)
        return out[::-1]

    # ---------------- phone companion (live mode, §29 data_path LIVE)
    def _companion_cfg_path(self) -> Path:
        return self.root / "config" / "companion.json"

    def _companion_cfg(self) -> dict:
        try:
            return json.loads(self._companion_cfg_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"enabled": False, "port": runtime.COMPANION_PORTS[0]}

    def companion_start_if_enabled(self, host: Optional[str] = None) -> None:
        cfg = self._companion_cfg()
        if cfg.get("enabled") and self._lan is None:
            from companion.server import LanSyncServer
            from companion.service import lan_ip
            ip = host or lan_ip()
            if not ip:
                return
            # Only ports the installer's firewall rule opens (private networks only).
            want = int(cfg.get("port") or 0)
            ports = [want] if want in runtime.COMPANION_PORTS else []
            ports += [p for p in runtime.COMPANION_PORTS if p != want]
            if not runtime.packaged():
                ports.append(0)            # from source there is no installer firewall rule to stay inside
            for port in ports:
                lan = LanSyncServer(self, ip, port)
                try:
                    cfg["port"] = lan.start()
                except OSError:
                    continue
                self._lan = lan
                self._write_companion_cfg(cfg)
                return

    def _write_companion_cfg(self, cfg: dict) -> None:
        path = self._companion_cfg_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(cfg), encoding="utf-8")

    def companion_state(self) -> dict:
        from companion.service import lan_ip
        cfg = self._companion_cfg()
        running = self._lan is not None
        return {"enabled": bool(cfg.get("enabled")), "running": running,
                "address": f"{self._lan.host}:{self._lan.port}" if running else None, "lan_ip": lan_ip(),
                "devices": self.companion.devices() if self.vault else []}

    def companion_enable(self, on: bool, host: Optional[str] = None) -> dict:
        cfg = self._companion_cfg()
        cfg["enabled"] = bool(on)
        self._write_companion_cfg(cfg)
        if on:
            self.companion_start_if_enabled(host)
            if self._lan is None:
                if not lan_ip_now():
                    raise AppError(400, "This computer isn't on a private home network (no 192.168.x / 10.x address)")
                raise AppError(409, f"Ports {runtime.COMPANION_PORTS[0]}-{runtime.COMPANION_PORTS[-1]} are all in use "
                                    "by other programs; close them and try again")
        elif self._lan is not None:
            self._lan.stop()
            self._lan = None
        if self.vault:
            self.vault.audit.append({"event": "COMPANION_" + ("ON" if on else "OFF"),
                                     "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
        return self.companion_state()

    # ---------------- nutrition / USDA FoodData Central
    def search_foods(self, query: str) -> List[dict]:
        from nutrition import foods
        return foods.search(query)

    def food_portions(self, fdc_id: int) -> dict:
        from nutrition import foods
        nut = foods.nutrients(fdc_id, 100.0)
        if nut is None:
            raise AppError(404, f"Food {fdc_id} not found")
        p = foods.portions(fdc_id)
        nutrient_keys = (
            "energy_kcal", "protein_g", "fat_g", "sat_fat_g", "carbs_g",
            "fiber_g", "sugars_g", "sodium_mg", "potassium_mg",
            "calcium_mg", "iron_mg", "vitamin_k_ug", "caffeine_mg"
        )
        return {
            "fdc_id": fdc_id,
            "description": nut.get("description", ""),
            "food_category": nut.get("food_category", ""),
            "publication_date": nut.get("publication_date", ""),
            "portions": p,
            "nutrients_per_100g": {k: nut.get(k, 0.0) for k in nutrient_keys},
        }

    # ---------------- backups (outside the app's folder, so they survive uninstall)
    def backup(self, passphrase: str) -> dict:
        from storage.backup import create_backup
        v = self.need_vault()
        if len(passphrase or "") < 12:
            raise AppError(400, "Backup passphrase: at least 12 characters")
        dest = runtime.backup_dir() / f"{self.user}-{datetime.now().strftime('%Y%m%d-%H%M%S')}.paihbkp"
        try:
            path = create_backup(v, dest, passphrase)
        except (RuntimeError, ValueError) as exc:
            raise AppError(409, str(exc)) from exc
        v.audit.append({"event": "BACKUP_CREATED", "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
        return {"path": str(path), "folder": str(path.parent)}

    def restore(self, data: bytes, name: str, backup_passphrase: str, vault_passphrase: str) -> dict:
        """Restore an encrypted backup as a user on this computer, then open it."""
        from storage.backup import restore_backup
        if not data:
            raise AppError(400, "Choose a backup file")
        name = (name or "").strip() or "Restored"
        slug = _slug(name)
        d = self.root / "users" / slug
        n = 2
        while d.exists():
            d = self.root / "users" / f"{slug}-{n}"
            n += 1
        tmp = Path(tempfile.mkdtemp(prefix="pai-health-restore-in-"))
        try:
            src = tmp / "backup.paihbkp"
            src.write_bytes(data)
            d.mkdir(parents=True)
            state = restore_backup(src, d / "vault", backup_passphrase, vault_passphrase)
        except Exception as exc:                       # noqa: BLE001 — nothing half-restored is left behind
            shutil.rmtree(d, ignore_errors=True)
            if isinstance(exc, ValueError) and "passphrase" not in str(exc).lower() and "verification" in str(exc):
                raise AppError(400, str(exc)) from exc
            raise AppError(400, "Couldn't restore: not a PAi Health backup, or one of the passphrases is wrong") from exc
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        (d / "user.json").write_text(json.dumps({"name": name}), encoding="utf-8")
        opened = self.unlock(d.name, vault_passphrase)
        return {**opened, "verified": bool(state.get("valid"))}

    def about(self) -> dict:
        return {"version": runtime.APP_VERSION, "notice_version": runtime.NOTICE_VERSION,
                "packaged": runtime.packaged(), "backup_folder": str(runtime.backup_dir()),
                "ocr": bool(runtime.configure_tesseract()) or _ocr_available()}

    def companion_pair(self) -> dict:
        self.need_vault()
        if self._lan is None:
            raise AppError(400, "Turn on the phone companion first")
        return self.companion.start_pairing(self._lan.host, self._lan.port)

    def companion_revoke(self, pair_id: str) -> dict:
        self.companion.revoke(pair_id)
        return self.companion_state()

    # ---------------- Bluetooth LE devices
    def bluetooth_status(self) -> dict:
        from devices.bluetooth import collector as ble
        return {"available": ble.available(),
                "install": 'py -m pip install "bleak>=0.22,<2"',
                "note": "Standard Bluetooth health devices only (heart-rate straps, BP cuffs, thermometers, scales, "
                        "pulse oximeters). Most watches don't share health data over standard Bluetooth — use the "
                        "watch app's export in Ingest instead."}

    def bluetooth_scan(self, seconds: int = 10) -> str:
        from devices.bluetooth import collector as ble
        if not ble.available():
            raise AppError(400, "The Bluetooth package isn't installed")
        seconds = max(3, min(int(seconds or 10), 60))

        def job(update):
            update(f"Scanning for {seconds} seconds…")
            import asyncio
            return {"devices": asyncio.run(ble.scan(seconds))}
        return self.jobs.start("BLE_SCAN", job, serial=False)

    def bluetooth_collect(self, address: str, seconds: int = 60) -> str:
        from devices.bluetooth import collector as ble
        v = self.need_vault()
        if not re.fullmatch(r"[0-9A-Fa-f:\-{}]{6,64}", address or ""):
            raise AppError(400, "Invalid device address")
        if not ble.available():
            raise AppError(400, "The Bluetooth package isn't installed")
        seconds = max(5, min(int(seconds or 60), 600))

        def job(update):
            import asyncio
            got = []
            update(f"Listening for {seconds} seconds — take a measurement on the device now.", 0, seconds)
            packets = asyncio.run(ble.collect(address, seconds, lambda p: got.append(p)))
            if not packets:
                return {"saved": None, "packets": 0, "samples": 0,
                        "note": "No measurements arrived. Put the device in measurement or sync mode, and pair it "
                                "in Windows Bluetooth settings first if it asks for pairing."}
            rec = ble.batch_record(packets)
            rid = v.put("DEVICE_BATCH", rec, meta={"source": ble.IMPORTER})
            return {"saved": rid, "packets": len(packets), "samples": len(rec["samples"]),
                    "by_measure": rec["report"]["by_measure"], "rejected": len(rec["report"]["rejected"])}
        return self.jobs.start("BLE_COLLECT", job, serial=False)

    def device_batches(self) -> List[dict]:
        out = []
        for r in self.need_vault().iter("DEVICE_BATCH"):
            b = r.body
            rep = b.get("report") or {}
            out.append({"id": r.id, "source": b.get("source"), "device_name": b.get("device_name"),
                        "saved_at": r.created_at, "samples": len(b.get("samples", [])),
                        "packets": len(b.get("packets", [])), "by_measure": rep.get("by_measure", {}),
                        "first": rep.get("first"), "last": rep.get("last"), "rejected": len(rep.get("rejected", [])),
                        "recent": [{k: x[k] for k in ("measure", "value", "unit", "start")} | {"time_basis": x["metadata"].get("time_basis")}
                                   for x in b.get("samples", [])[-12:]]})
        return out[::-1]

    # ---------------- engine views
    def scenario(self) -> dict:
        with self.lock:
            if self._scenario is not None:
                return self._scenario
            billing_docs = set(self._billing_confirmed())    # billing assertions are not clinical records
            docs = [doc_from_dict(b["doc"]) for did, b in self._docs().items() if did not in billing_docs]
            retrieve = None
            if self.kb is not None:
                from knowledge.kb import record_seeds
                retrieve = lambda fl, q: self.kb.authorities_for(record_seeds(fl, q))  # noqa: E731
            q = next((r.body.get("text") for r in reversed(list(self.need_vault().iter("QUESTION")))), None)
            out = run_pipeline([record_doc(d) for d in docs], q, "USER", [], retrieve=retrieve)
            C.apply_locks(out["expected"]["floats"], self._reports(), self._resolutions())
            out["fixture_id"] = "USER_RECORDS"
            self._scenario = out
            return out

    def tracker(self, demo: dict, now: Optional[datetime] = None) -> dict:
        if not self.vault:
            t = json.loads(json.dumps(demo))
            t["settings"]["local_model_installed"] = self._local_ready()
            return t
        from tracker import compute, device_store
        logs = []
        for r in self.vault.iter("USER_LOG"):
            logs.append(dict(r.body, _created=r.created_at))
        try:
            floats = self.scenario()["expected"]["floats"]
        except Exception:
            floats = []
        I = compute.Inputs(logs, lambda m: device_store.rows_for(self.vault, m), floats, self.vault.settings(),
                           self.user_name or "", now or datetime.now().astimezone())
        t = compute.build(I)
        t["settings"] = {"data_path": "REVIEW", "device_dose_confirmation": False, "ai_mode": "None",
                         **self.vault.settings(), "live_available": self.companion.any_confirmed(),
                         "local_model_installed": self._local_ready()}
        return t

    # ---------------- first-person logs (§19)
    def log_entry(self, entry: dict) -> dict:
        from tracker import logs as LG
        v = self.need_vault()
        e = LG.normalize(entry)
        if e["type"] == "LAB" and len(e["results"]) != 1:
            raise AppError(400, "Enter one analyte per save so corrections cannot replace other results")
        ref = LG.ref_for(v, e)
        prior = next(v.iter("USER_LOG", ref=ref), None) if ref else None
        rid = v.put("USER_LOG", e, ref=ref, supersedes=prior.id if prior else None)
        return {"id": rid, "entry": e}

    # ---------------- device exports
    def import_device(self, name: str, data: bytes, kind: str, options: dict) -> str:
        from tracker import device_store as DS
        v = self.need_vault()
        missing = [k for k in DS.needs(kind) if not options.get(k)]
        if missing:
            raise AppError(400, f"{DS.LABEL[kind]} needs: {', '.join(missing)}")

        def job(update):
            return DS.import_bytes(v, name, data, kind, options, update)
        return self.jobs.start("DEVICE_IMPORT", job)

    def device_imports(self) -> List[dict]:
        from tracker import device_store as DS
        return DS.imports(self.need_vault())

    def set_setting(self, key: str, value) -> None:
        v = self.need_vault()
        if key == "data_path" and value == "LIVE" and not self.companion.any_confirmed():
            raise ValueError("Pair the phone companion first (Settings › Phone companion)")
        if key == "ai_mode" and value == "PAi" and not self._local_ready():
            raise ValueError("Install a private model first (Settings › AI setup)")
        if key == "ai_mode" and value == "PSi" and not self.ai_config():
            raise ValueError("Set up an API provider first (Settings › AI setup)")
        v.set_setting(key, value, SETTING_VALUES)
        self._scenario = None

    def app_state(self) -> dict:
        return {"users": self.users(), "user": ({"id": self.user, "name": self.user_name} if self.vault else None),
                "ai": self.ai_public() if self.vault else {"local": self.ai_public()["local"]},
                "knowledge_base": ({k: self.kb.meta.get(k) for k in ("built_at", "shards_included")}
                                   if self.kb else None),
                "jobs": self.jobs.active()}


def lan_ip_now():
    from companion.service import lan_ip
    return lan_ip()


def _ocr_available() -> bool:
    try:
        from ingest.formats_pdf_image import tesseract_available
        return tesseract_available()
    except Exception:
        return False
