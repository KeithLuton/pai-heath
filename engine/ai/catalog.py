"""Vetted Hugging Face GGUF catalog and conservative fit scoring (ported from PAi Legal).

Analysis models only phrase what the deterministic engine computed and answer the
value check during ingest; they never decide a status (constitution §14, §17).
"""
from __future__ import annotations

import json
import hashlib
import os
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from .hardware import HardwareProfile


@dataclass(frozen=True)
class ModelCandidate:
    key: str
    label: str
    repo_id: str
    filename: str
    size_gb: float
    min_ram_gb: float
    context: int
    rank: int
    license: str = "Apache-2.0"
    revision: str = ""
    sha256: str = ""


CATALOG = (
    ModelCandidate("qwen3-1.7b", "Qwen3 1.7B · Light", "Qwen/Qwen3-1.7B-GGUF", "Qwen3-1.7B-Q8_0.gguf", 1.8, 6, 8192, 1),
    ModelCandidate("qwen3-4b", "Qwen3 4B · Standard", "Qwen/Qwen3-4B-GGUF", "Qwen3-4B-Q4_K_M.gguf", 2.5, 10, 12288, 2),
    ModelCandidate("qwen3-8b", "Qwen3 8B · Advanced", "Qwen/Qwen3-8B-GGUF", "Qwen3-8B-Q4_K_M.gguf", 5.0, 16, 16384, 3),
    ModelCandidate("qwen3-14b", "Qwen3 14B · Heavy", "Qwen/Qwen3-14B-GGUF", "Qwen3-14B-Q4_K_M.gguf", 9.0, 28, 16384, 4),
)


def compatible_models(profile: HardwareProfile, catalog: Iterable[ModelCandidate] = CATALOG) -> list[ModelCandidate]:
    # Require room for Windows, the context/KV cache, ingestion, and an interrupted download.
    # Treat some file cache as reclaimable, but never assume all installed RAM
    # is free. This prevents an optimistic recommendation on a busy computer.
    usable_ram = min(max(profile.available_ram_gb, profile.total_ram_gb * 0.65), profile.total_ram_gb - 5.0)
    return [model for model in catalog if usable_ram >= model.min_ram_gb and profile.free_disk_gb >= model.size_gb * 2.2 + 2]


def recommend_model(profile: HardwareProfile, catalog: Iterable[ModelCandidate] = CATALOG) -> ModelCandidate | None:
    choices = compatible_models(profile, catalog)
    return max(choices, key=lambda item: item.rank, default=None)


def resolve_huggingface_file(model: ModelCandidate, timeout: int = 20) -> ModelCandidate:
    # ``blobs=true`` supplies both the immutable repository commit and the
    # Git-LFS SHA-256/size needed to verify the downloaded binary.
    url = ("https://huggingface.co/api/models/" +
           urllib.parse.quote(model.repo_id, safe="/") + "?blobs=true")
    request = urllib.request.Request(url, headers={"User-Agent": "PAi-Health/0.5"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    siblings = payload.get("siblings", [])
    exact = next((item for item in siblings if item.get("rfilename") == model.filename), None)
    if not exact and model.filename.endswith("Q4_K_M.gguf"):
        # Only an equivalent quantisation may stand in; a projector or another
        # precision never silently replaces the vetted file.
        exact = next((item for item in siblings if str(item.get("rfilename", "")).endswith("Q4_K_M.gguf")), None)
    if not exact:
        raise RuntimeError(f"{model.repo_id} no longer publishes {model.filename}")
    size = int(exact.get("size") or 0)
    revision = str(payload.get("sha") or "")
    if not re.fullmatch(r"[0-9a-f]{40,64}", revision, re.I):
        raise RuntimeError(f"{model.repo_id} did not return an immutable repository revision")
    digest = str((exact.get("lfs") or {}).get("sha256") or "")
    if not re.fullmatch(r"[0-9a-f]{64}", digest, re.I):
        raise RuntimeError(f"{model.repo_id} did not publish a verifiable SHA-256 for the model file")
    return ModelCandidate(model.key, model.label, model.repo_id,
                          str(exact["rfilename"]),
                          size / 2**30 if size else model.size_gb,
                          model.min_ram_gb, model.context, model.rank,
                          model.license, revision, digest.lower())


def download_model(model: ModelCandidate, destination: Path, progress: Callable[[int, int], None] | None = None, timeout: int = 60) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    target = destination / Path(model.filename).name
    partial = target.with_suffix(target.suffix + ".part")
    existing = partial.stat().st_size if partial.exists() else 0
    if not model.revision:
        raise RuntimeError("Model download requires an immutable resolved revision")
    if not re.fullmatch(r"[0-9a-f]{64}", model.sha256, re.I):
        raise RuntimeError("Model download requires a published SHA-256 digest")
    revision = urllib.parse.quote(model.revision, safe="")
    url = f"https://huggingface.co/{urllib.parse.quote(model.repo_id, safe='/')}/resolve/{revision}/{urllib.parse.quote(model.filename)}?download=true"
    headers = {"User-Agent": "PAi-Health/0.5"}
    if existing:
        headers["Range"] = f"bytes={existing}-"
    token = os.environ.get("HF_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        if existing and response.status != 206:
            existing = 0
            mode = "wb"
        else:
            mode = "ab" if existing else "wb"
        if existing and response.status == 206:
            content_range = str(response.headers.get("Content-Range") or "")
            if not content_range.startswith(f"bytes {existing}-"):
                raise RuntimeError("Model server returned an unsafe resume range")
        remaining = int(response.headers.get("Content-Length") or 0)
        total = existing + remaining
        with partial.open(mode) as handle:
            received = existing
            while True:
                block = response.read(1024 * 1024)
                if not block:
                    break
                handle.write(block)
                received += len(block)
                if progress:
                    progress(received, total)
    if total and partial.stat().st_size != total:
        raise RuntimeError("Model download ended before the complete file was received")
    digest = hashlib.sha256()
    with partial.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    if digest.hexdigest().lower() != model.sha256.lower():
        partial.unlink(missing_ok=True)
        raise RuntimeError("Model download failed SHA-256 verification")
    partial.replace(target)
    return target
