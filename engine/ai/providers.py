"""External AI with the user's own API key: NVIDIA Nemotron, OpenAI GPT, Anthropic Claude.

Constitution §17 (PSi mode): off by default; the UI says "health data leaves
this computer" whenever it is on; every call is written to the audit chain.

Endpoints are fixed in netsecurity.EXTERNAL_HOSTS — no setting can point a
provider somewhere else. Model ids are not hard-coded: after the key is entered
the provider's own model list is fetched and the user picks one.

chat() takes OpenAI-style messages (system/user, content as text or parts with
image_url data URLs) and translates them for Anthropic.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import Callable, List, Optional

from .netsecurity import require_external_url

PROVIDERS = {
    "nemotron": {"label": "NVIDIA Nemotron", "key_hint": "nvapi-…", "keys_url": "https://build.nvidia.com",
                 "prefer": [r"nemotron.*vl", r"nemotron.*super", r"nemotron"]},
    "openai": {"label": "OpenAI GPT", "key_hint": "sk-…", "keys_url": "https://platform.openai.com/api-keys",
               "prefer": [r"^gpt-\d+(\.\d+)?$", r"^gpt-\d", r"^o\d"]},
    "anthropic": {"label": "Anthropic Claude", "key_hint": "sk-ant-…", "keys_url": "https://console.anthropic.com",
                  "prefer": [r"^claude-sonnet", r"^claude-opus", r"^claude"]},
}
ANTHROPIC_VERSION = "2023-06-01"
UA = "PAi-Health/0.5"


class ProviderError(RuntimeError):
    pass


Transport = Callable[[str, str, dict, Optional[bytes], int], dict]


def _http(method: str, url: str, headers: dict, body: Optional[bytes], timeout: int) -> dict:
    req = urllib.request.Request(url, data=body, method=method, headers={"User-Agent": UA, **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        if exc.code in (401, 403):
            raise ProviderError("The provider rejected the API key") from exc
        raise ProviderError(f"Provider returned {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise ProviderError(f"Could not reach the provider: {exc.reason}") from exc


class ExternalModel:
    def __init__(self, provider: str, api_key: str, model: str = "", timeout: int = 120,
                 transport: Transport = _http):
        if provider not in PROVIDERS:
            raise ProviderError(f"Unknown provider {provider!r}")
        if not api_key or len(api_key) < 8:
            raise ProviderError("An API key is required")
        self.provider, self.api_key, self.model, self.timeout = provider, api_key.strip(), model, timeout
        self.base = require_external_url(provider)
        self._t = transport

    @property
    def label(self) -> str:
        return f"{PROVIDERS[self.provider]['label']} · {self.model}"

    def _headers(self) -> dict:
        if self.provider == "anthropic":
            return {"x-api-key": self.api_key, "anthropic-version": ANTHROPIC_VERSION,
                    "content-type": "application/json"}
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    def list_models(self) -> List[str]:
        data = self._t("GET", self.base + "/models", self._headers(), None, 30)
        ids = sorted({str(m.get("id")) for m in data.get("data", []) if m.get("id")})
        if not ids:
            raise ProviderError("The provider returned no models for this key")
        return ids

    @staticmethod
    def preferred(provider: str, ids: List[str]) -> str:
        for pattern in PROVIDERS[provider]["prefer"]:
            hits = sorted((i for i in ids if re.search(pattern, i.split("/")[-1], re.I)), reverse=True)
            if hits:
                return hits[0]
        return ids[0] if ids else ""

    def chat(self, messages: list, max_tokens: int = 800, temperature: float = 0.0) -> str:
        if not self.model:
            raise ProviderError("Choose a model first")
        if self.provider == "anthropic":
            system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
            conv = [{"role": m["role"], "content": _to_anthropic(m["content"])}
                    for m in messages if m["role"] != "system"]
            body = {"model": self.model, "max_tokens": max_tokens, "temperature": temperature, "messages": conv}
            if system:
                body["system"] = system
            data = self._t("POST", self.base + "/messages", self._headers(), json.dumps(body).encode(), self.timeout)
            return "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text").strip()
        body = {"model": self.model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens}
        if self.provider == "openai":
            body.pop("max_tokens")
            body["max_completion_tokens"] = max_tokens
            body.pop("temperature")     # several current OpenAI models accept only the default
        data = self._t("POST", self.base + "/chat/completions", self._headers(), json.dumps(body).encode(),
                       self.timeout)
        try:
            return str(data["choices"][0]["message"]["content"]).strip()
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError("Unexpected reply from the provider") from exc

    def close(self) -> None:
        pass


def _to_anthropic(content):
    if isinstance(content, str):
        return content
    out = []
    for part in content:
        if part.get("type") == "text":
            out.append({"type": "text", "text": part["text"]})
        elif part.get("type") == "image_url":
            url = part["image_url"]["url"]
            m = re.match(r"data:([^;]+);base64,(.*)", url, re.S)
            if not m:
                raise ProviderError("Only inline images are sent")
            out.append({"type": "image", "source": {"type": "base64", "media_type": m.group(1), "data": m.group(2)}})
    return out
