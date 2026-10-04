"""Minimal ASI:One chat-completions client (OpenAI-compatible), standard library only.

The key is read from the environment and never logged or shown in reprs. Errors expose a
safe category only: no response body, headers or credentials. Callers must treat model
output as untrusted text and keep a deterministic fallback.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

DEFAULT_BASE_URL = "https://api.asi1.ai/v1"
DEFAULT_MODEL = "asi1"
MAX_RESPONSE_BYTES = 1_000_000

# (url, body, headers, timeout) -> response bytes. Injected in tests.
Transport = Callable[[str, bytes, Mapping[str, str], float], bytes]


class LlmError(RuntimeError):
    """A model call failed; ``category`` is safe to log."""

    def __init__(self, category: str) -> None:
        super().__init__(category)
        self.category = category


@dataclass(frozen=True, slots=True)
class LlmSettings:
    api_key: str | None = field(default=None, repr=False)
    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL

    def __post_init__(self) -> None:
        url = urlsplit(self.base_url)
        if url.scheme != "https" or not url.hostname or url.username or url.password:
            raise ValueError("ASI_ONE_BASE_URL must be an https URL without credentials")
        if not self.model.strip():
            raise ValueError("ASI_ONE_MODEL must not be empty")
        if self.api_key is not None and (
            not self.api_key.strip() or any(c.isspace() for c in self.api_key)
        ):
            raise ValueError("ASI_ONE_API_KEY is malformed")

    @property
    def enabled(self) -> bool:
        return self.api_key is not None

    @classmethod
    def from_env(cls, environment: Mapping[str, str] | None = None) -> LlmSettings:
        env = os.environ if environment is None else environment
        key = env.get("ASI_ONE_API_KEY", "").strip()
        return cls(
            api_key=key or None,
            base_url=env.get("ASI_ONE_BASE_URL", "").strip() or DEFAULT_BASE_URL,
            model=env.get("ASI_ONE_MODEL", "").strip() or DEFAULT_MODEL,
        )


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


def http_transport(url: str, body: bytes, headers: Mapping[str, str], timeout: float) -> bytes:
    request = urllib.request.Request(url, data=body, headers=dict(headers), method="POST")
    opener = urllib.request.build_opener(_NoRedirect)
    with opener.open(request, timeout=timeout) as response:
        data: bytes = response.read(MAX_RESPONSE_BYTES + 1)
    if len(data) > MAX_RESPONSE_BYTES:
        raise LlmError("response_too_large")
    return data


def chat_completion(
    settings: LlmSettings,
    system: str,
    user: str,
    *,
    max_tokens: int = 600,
    timeout: float = 20.0,
    transport: Transport = http_transport,
) -> str:
    """One blocking chat completion; run it off the event loop (asyncio.to_thread)."""
    if settings.api_key is None:
        raise LlmError("disabled")
    body = json.dumps(
        {
            "model": settings.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": max_tokens,
        }
    ).encode()
    headers = {
        "Authorization": f"Bearer {settings.api_key}",
        "Content-Type": "application/json",
    }
    url = f"{settings.base_url.rstrip('/')}/chat/completions"
    try:
        raw = transport(url, body, headers, timeout)
    except LlmError:
        raise
    except urllib.error.HTTPError as error:
        raise LlmError(f"http_{error.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise LlmError("unavailable") from None
    try:
        content = json.loads(raw)["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError):
        raise LlmError("malformed_response") from None
    if not isinstance(content, str) or not content.strip():
        raise LlmError("empty_response")
    return content
