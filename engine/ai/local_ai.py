"""Private AI on this computer: llama.cpp runtime, analysis model and page checker.

Ported from PAi Legal's local_ai.py and vision.py. Same boundaries:
  * llama-server binds to 127.0.0.1 on a free port with a per-run API key
  * models are downloaded once against an immutable revision and a published
    SHA-256 (catalog.py), then nothing leaves the machine
  * the checker is a separate vision model that is only ever asked whether a
    page and its extracted text agree

Runtime lookup order: PAI_HEALTH_LLAMA_SERVER, the bundled resources/llama
folder, then llama-server on PATH (e.g. `winget install ggml.llamacpp`).
"""
from __future__ import annotations

import base64
import json
import os
import secrets
import shutil
import socket
import atexit
import subprocess
import sys
import time
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, List, Optional

from .catalog import CATALOG, ModelCandidate, compatible_models, download_model, recommend_model, \
    resolve_huggingface_file
from .hardware import HardwareProfile, app_data_root
from .netsecurity import bearer_headers, require_loopback_url
from .procutil import hidden


class LocalAIError(RuntimeError):
    pass


# ---------------------------------------------------------------- checker catalog
@dataclass(frozen=True)
class VisionCandidate:
    """A vision model is two files: the weights and its projector."""
    key: str
    label: str
    repo_id: str
    filename: str
    mmproj_filename: str
    size_gb: float
    min_ram_gb: float
    context: int
    rank: int
    license: str = "Apache-2.0"


CHECKERS = (
    VisionCandidate("qwen2.5-vl-3b", "Qwen2.5-VL 3B · Checker", "ggml-org/Qwen2.5-VL-3B-Instruct-GGUF",
                    "Qwen2.5-VL-3B-Instruct-Q4_K_M.gguf", "mmproj-Qwen2.5-VL-3B-Instruct-f16.gguf", 3.4, 10, 8192, 1),
    VisionCandidate("qwen2.5-vl-7b", "Qwen2.5-VL 7B · Checker (thorough)", "ggml-org/Qwen2.5-VL-7B-Instruct-GGUF",
                    "Qwen2.5-VL-7B-Instruct-Q4_K_M.gguf", "mmproj-Qwen2.5-VL-7B-Instruct-f16.gguf", 6.2, 18, 8192, 2),
)


def _usable_ram(p: HardwareProfile) -> float:
    return min(max(p.available_ram_gb, p.total_ram_gb * 0.65), p.total_ram_gb - 5.0)


def compatible_checkers(p: HardwareProfile) -> List[VisionCandidate]:
    return [c for c in CHECKERS if _usable_ram(p) >= c.min_ram_gb and p.free_disk_gb >= c.size_gb * 2.2 + 2]


def recommend_checker(p: HardwareProfile) -> Optional[VisionCandidate]:
    return max(compatible_checkers(p), key=lambda c: c.rank, default=None)


def options(profile: HardwareProfile) -> dict:
    """Everything the setup screen shows about private AI on this machine."""
    rec, rec_c = recommend_model(profile), recommend_checker(profile)
    fits = {m.key for m in compatible_models(profile)}
    fits_c = {c.key for c in compatible_checkers(profile)}
    return {
        "hardware": profile.as_dict(),
        "runtime": str(find_runtime() or ""),
        "analysis_models": [dict(asdict(m), fits=m.key in fits, recommended=bool(rec and m.key == rec.key))
                            for m in CATALOG],
        "checkers": [dict(asdict(c), fits=c.key in fits_c, recommended=bool(rec_c and c.key == rec_c.key))
                     for c in CHECKERS],
        "installed": {"analysis": read_installation("analysis"), "checker": read_installation("checker")},
    }


# ---------------------------------------------------------------- install records
def installation_path(role: str) -> Path:
    return app_data_root() / "config" / f"local_{role}.json"


def read_installation(role: str) -> dict:
    try:
        data = json.loads(installation_path(role).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    files = [data.get("model_path"), data.get("mmproj_path")] if role == "checker" else [data.get("model_path")]
    data["files_present"] = all(f and Path(f).is_file() for f in files)
    return data


def _write_installation(role: str, payload: dict) -> dict:
    path = installation_path(role)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(path)
    return payload


def find_runtime() -> Optional[Path]:
    override = os.environ.get("PAI_HEALTH_LLAMA_SERVER")
    if override:
        return Path(override) if Path(override).is_file() else None
    name = "llama-server.exe" if os.name == "nt" else "llama-server"
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
    bundled = base / "resources" / "llama" / name
    if bundled.is_file():
        return bundled
    found = shutil.which("llama-server")
    return Path(found) if found else None


def _missing_runtime() -> str:
    if getattr(sys, "frozen", False):
        return ("The private AI runtime is missing from this installation. "
                "Repair or reinstall PAi Health from the Microsoft Store.")
    return ("The llama.cpp runtime (llama-server) isn't installed. "
            "Run: winget install ggml.llamacpp  — then choose this model again.")


def provision_analysis(model: ModelCandidate, hw: HardwareProfile,
                       progress: Optional[Callable[[int, int], None]] = None) -> dict:
    runtime = find_runtime()
    if not runtime:
        raise LocalAIError(_missing_runtime())
    resolved = resolve_huggingface_file(model)
    path = download_model(resolved, app_data_root() / "models", progress)
    return _write_installation("analysis", {
        "schema_version": 1, "model_id": model.key, "model_label": model.label, "repo_id": model.repo_id,
        "filename": resolved.filename, "model_path": str(path), "model_revision": resolved.revision,
        "model_sha256": resolved.sha256, "context": model.context,
        "gpu_layers": -1 if hw.vram_gb >= model.size_gb + 2 else 0,
        "installed_at": datetime.now(timezone.utc).isoformat(), "launch_confirmed": False})


def provision_checker(c: VisionCandidate, hw: HardwareProfile,
                      progress: Optional[Callable[[int, int], None]] = None) -> dict:
    if not find_runtime():
        raise LocalAIError(_missing_runtime())
    as_model = lambda f: ModelCandidate(c.key, c.label, c.repo_id, f, c.size_gb, c.min_ram_gb,  # noqa: E731
                                        c.context, c.rank, c.license)
    rm = resolve_huggingface_file(as_model(c.filename))
    rp = resolve_huggingface_file(as_model(c.mmproj_filename))
    if Path(rm.filename).name != c.filename or Path(rp.filename).name != c.mmproj_filename:
        raise LocalAIError(f"{c.repo_id} no longer publishes the vetted checker files")
    dest = app_data_root() / "models"
    mp, pp = download_model(rm, dest, progress), download_model(rp, dest, progress)
    return _write_installation("checker", {
        "schema_version": 1, "model_id": c.key, "model_label": c.label, "repo_id": c.repo_id,
        "model_path": str(mp), "mmproj_path": str(pp), "model_revision": rm.revision, "model_sha256": rm.sha256,
        "mmproj_revision": rp.revision, "mmproj_sha256": rp.sha256, "context": c.context,
        "gpu_layers": -1 if hw.vram_gb >= c.size_gb + 2 else 0,
        "installed_at": datetime.now(timezone.utc).isoformat()})


# ---------------------------------------------------------------- runtime
def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class LocalModel:
    """One llama-server process on loopback. chat() takes OpenAI-style messages."""

    def __init__(self, role: str, resources: Optional[dict] = None, timeout: int = 240):
        self.role = role
        self.resources = resources if resources is not None else read_installation(role)
        self.timeout = timeout
        self.api_url = ""
        self.api_key = ""
        self._proc: Optional[subprocess.Popen] = None

    @property
    def label(self) -> str:
        return str(self.resources.get("model_label") or self.role)

    @property
    def installed(self) -> bool:
        return bool(self.resources.get("files_present")) or (
            bool(self.resources.get("model_path")) and Path(self.resources["model_path"]).is_file()
            and (self.role != "checker" or Path(str(self.resources.get("mmproj_path"))).is_file()))

    def _healthy(self) -> bool:
        for suffix in ("/health", "/v1/models"):
            try:
                req = urllib.request.Request(self.api_url + suffix, headers=bearer_headers(self.api_key))
                with urllib.request.urlopen(req, timeout=1) as r:
                    if 200 <= r.status < 300:
                        return True
            except Exception:
                pass
        return False

    def start(self) -> None:
        if self.api_url and self._healthy():
            return
        if not self.installed:
            raise LocalAIError(f"No private {self.role} model is installed")
        runtime = find_runtime()
        if not runtime:
            raise LocalAIError("The llama.cpp runtime (llama-server) isn't installed")
        port = _free_port()
        self.api_url = require_loopback_url(f"http://127.0.0.1:{port}", "Private AI")
        self.api_key = secrets.token_urlsafe(32)
        args = [str(runtime), "--model", str(self.resources["model_path"]), "--host", "127.0.0.1",
                "--port", str(port), "--api-key", self.api_key, "-c", str(self.resources.get("context") or 8192)]
        if self.role == "checker":
            args += ["--mmproj", str(self.resources["mmproj_path"])]
        if int(self.resources.get("gpu_layers") or 0):
            args += ["-ngl", str(self.resources["gpu_layers"])]
        self._proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                      stdin=subprocess.DEVNULL, **hidden())
        _LIVE.add(self)
        deadline = time.time() + self.timeout
        while time.time() < deadline:
            if self._healthy():
                return
            if self._proc.poll() is not None:
                raise LocalAIError(f"The private {self.role} model stopped while loading")
            time.sleep(0.5)
        self.close()
        raise LocalAIError(f"The private {self.role} model did not become ready in time")

    def chat(self, messages: list, max_tokens: int = 800, temperature: float = 0.0) -> str:
        self.start()
        body = json.dumps({"model": "local", "messages": messages, "temperature": temperature,
                           "top_p": 0.1, "max_tokens": max_tokens, "stream": False}).encode()
        req = urllib.request.Request(self.api_url + "/v1/chat/completions", data=body, method="POST",
                                     headers={"Content-Type": "application/json", **bearer_headers(self.api_key)})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            payload = json.loads(r.read().decode("utf-8"))
        return str(payload["choices"][0]["message"]["content"]).strip()

    def close(self) -> None:
        if self._proc:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=5)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass
            self._proc = None
        _LIVE.discard(self)
        self.api_url = self.api_key = ""


_LIVE: "set[LocalModel]" = set()


def close_all() -> None:
    """Stop every llama-server this process started (app exit)."""
    for m in list(_LIVE):
        m.close()


atexit.register(close_all)


def image_data_url(path: Path) -> str:
    media = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp",
             ".gif": "image/gif"}.get(Path(path).suffix.lower(), "image/png")
    return f"data:{media};base64," + base64.b64encode(Path(path).read_bytes()).decode("ascii")
