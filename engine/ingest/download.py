"""Safe download of medical PDFs (and images) to a local staging directory.

Rules:
* HTTPS only (http refused).
* Size cap (default 50 MiB) — real multi-page lab PDFs are far smaller; large
  files are more often the wrong thing than a 200-page chart dump the user
  did not intend.
* Content-Type must be PDF or a common image type, or the magic bytes must
  match (servers often send application/octet-stream).
* Destination name is the content SHA-256; the original filename is kept only
  in metadata, never trusted as a path component.
* No redirect to a different host (basic open-redirect defense).
* The caller always reviews the file locally before it enters the clinical path.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import requests

DEFAULT_MAX_BYTES = 50 * 1024 * 1024
ALLOWED_CONTENT = {
    "application/pdf",
    "application/x-pdf",
    "image/png",
    "image/jpeg",
    "image/jpg",
    "image/webp",
    "image/tiff",
    "application/octet-stream",  # allowed only if magic bytes confirm
}

PDF_MAGIC = b"%PDF"
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
JPEG_MAGIC = b"\xff\xd8\xff"
WEBP_MAGIC_PREFIX = b"RIFF"
TIFF_MAGIC_LE = b"II*\x00"
TIFF_MAGIC_BE = b"MM\x00*"


class DownloadError(ValueError):
    pass


@dataclass(frozen=True)
class DownloadResult:
    path: Path
    sha256: str
    media_type: str
    bytes_written: int
    source_url: str
    final_url: str


def _detect_media(head: bytes, content_type: Optional[str]) -> str:
    if head.startswith(PDF_MAGIC):
        return "application/pdf"
    if head.startswith(PNG_MAGIC):
        return "image/png"
    if head.startswith(JPEG_MAGIC):
        return "image/jpeg"
    if head.startswith(TIFF_MAGIC_LE) or head.startswith(TIFF_MAGIC_BE):
        return "image/tiff"
    if head.startswith(WEBP_MAGIC_PREFIX) and len(head) >= 12 and head[8:12] == b"WEBP":
        return "image/webp"
    ct = (content_type or "").split(";")[0].strip().lower()
    if ct in ALLOWED_CONTENT and ct != "application/octet-stream":
        return ct
    raise DownloadError(
        f"unrecognized content (Content-Type={content_type!r}, "
        f"magic={head[:8]!r}); expected PDF or image"
    )


def download(
    url: str,
    dest_dir: str | Path,
    *,
    max_bytes: int = DEFAULT_MAX_BYTES,
    timeout: float = 60.0,
    session: Optional[requests.Session] = None,
) -> DownloadResult:
    """Download `url` into `dest_dir` as `<sha256>.<ext>`.

    Raises DownloadError on policy violations or network/content failures.
    """
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise DownloadError("only https:// URLs are accepted")
    if not parsed.netloc:
        raise DownloadError("URL is missing a host")

    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    sess = session or requests.Session()
    try:
        with sess.get(url, stream=True, timeout=timeout, allow_redirects=True) as resp:
            if resp.status_code != 200:
                raise DownloadError(f"HTTP {resp.status_code} from {parsed.netloc}")
            # Host must not change across redirects
            final = urlparse(resp.url)
            if final.scheme != "https" or final.netloc.lower() != parsed.netloc.lower():
                raise DownloadError(
                    f"redirect left original host ({parsed.netloc} → {final.netloc})"
                )
            cl = resp.headers.get("Content-Length")
            if cl is not None:
                try:
                    if int(cl) > max_bytes:
                        raise DownloadError(f"Content-Length {cl} exceeds limit {max_bytes}")
                except ValueError:
                    pass

            digest = hashlib.sha256()
            chunks: list[bytes] = []
            total = 0
            head = b""
            for chunk in resp.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                total += len(chunk)
                if total > max_bytes:
                    raise DownloadError(f"download exceeded size limit ({max_bytes} bytes)")
                digest.update(chunk)
                if len(head) < 16:
                    head += chunk[: 16 - len(head)]
                chunks.append(chunk)

            media = _detect_media(head, resp.headers.get("Content-Type"))
            sha = digest.hexdigest()
            ext = {
                "application/pdf": ".pdf",
                "image/png": ".png",
                "image/jpeg": ".jpg",
                "image/webp": ".webp",
                "image/tiff": ".tiff",
            }.get(media, ".bin")
            out = dest_dir / f"{sha}{ext}"
            out.write_bytes(b"".join(chunks))
            return DownloadResult(
                path=out,
                sha256=sha,
                media_type=media,
                bytes_written=total,
                source_url=url,
                final_url=resp.url,
            )
    except requests.RequestException as exc:
        raise DownloadError(f"download failed: {exc}") from exc


_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def staging_name_from_url(url: str) -> str:
    """Human hint only — never used as the on-disk identity."""
    path = urlparse(url).path.rsplit("/", 1)[-1] or "document"
    return _SAFE_NAME.sub("_", path)[:120]
