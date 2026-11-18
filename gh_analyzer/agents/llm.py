"""OpenAI-compatible chat client with usage accounting, for the PR agent.

Rules, all enforced by tests:
  1. No new dependency: stdlib `urllib` behind an injectable `transport`, so tests
     never touch the network.
  2. The API key is stored privately, sent only in the Authorization header, and
     never appears in exceptions, `repr()`, or the URL. Error text from the server
     is scrubbed of the key and any `Bearer ...` token and cut to 200 characters.
  3. Every string `content` in every message passes through `safe.safe_text` before
     the request body is built (defence in depth; it is idempotent). Nothing else
     in a message is touched and the caller's list is never mutated.
  4. Rate limits (429), server errors (5xx) and transport errors (OSError/timeout)
     are retried up to MAX_ATTEMPTS times with `min(2**attempt, 30)` second sleeps
     between attempts. Other statuses fail at once. `Retry-After` is not honoured
     because the transport returns only `(status, text)`.
  5. A malformed or oversized response raises `LLMError` with a fixed short message,
     never `KeyError` or `JSONDecodeError`.
  6. The base URL is checked at the start of every `chat()`: https, or http only for
     localhost/127.0.0.1/::1, and no userinfo. Redirects are never followed, so the
     Authorization header can only ever reach the validated host.
  7. Transport failures: OSError and HTTPException are retried; any other exception
     becomes `LLMError("transport failed")` with nothing from the original included.
"""

from __future__ import annotations

import http.client
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from gh_analyzer.agents.safe import safe_text

DEFAULT_BASE_URL = "https://api.groq.com/openai/v1"
DEFAULT_MODEL = "openai/gpt-oss-120b"
MAX_ATTEMPTS = 6
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
ERROR_TEXT_CHARS = 200
TIMEOUT_SECONDS = 120
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}

_BEARER = re.compile(r"(?i)\bBearer\s+\S+")


class LLMError(RuntimeError):
    """Any failure talking to the model. Messages never contain the API key."""


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def add(self, other: Usage) -> None:
        self.prompt_tokens += other.prompt_tokens
        self.completion_tokens += other.completion_tokens


class ChatClient(Protocol):
    def chat(self, messages: list[dict], tools: list[dict] | None = None,
             temperature: float = 0.2, max_tokens: int = 1500,
             tool_choice: dict | None = None) -> tuple[dict, Usage]:
        """Return the assistant message dict and this call's token usage."""
        ...


Transport = Callable[[str, dict, bytes, float], "tuple[int, str]"]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never follow a redirect: a 3xx surfaces as an HTTP error status instead, so the
    Authorization header cannot be forwarded to another host."""

    def redirect_request(self, *args, **kwargs):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def _check_base_url(url: str) -> None:
    """Raise LLMError unless `url` is https (or http on a loopback host) without userinfo."""
    try:
        parts = urllib.parse.urlsplit(url)
        host = (parts.hostname or "").lower()
        scheme = parts.scheme.lower()
        has_userinfo = "@" in parts.netloc
        parts.port  # noqa: B018 (raises ValueError on a malformed port)
    except ValueError:
        raise LLMError("LLM_BASE_URL is not a valid URL") from None
    if has_userinfo:
        raise LLMError("LLM_BASE_URL must not contain credentials")
    if scheme == "https" and host:
        return
    if scheme == "http" and host in _LOCAL_HOSTS:
        return
    raise LLMError(
        f"LLM_BASE_URL not allowed (scheme {scheme[:20]!r}, host {host[:60]!r}): "
        "use https, or http on localhost")


def _urllib_transport(url: str, headers: dict, body: bytes, timeout: float) -> tuple[int, str]:
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with _OPENER.open(req, timeout=timeout) as resp:  # noqa: S310 (https base url)
            raw = resp.read(MAX_RESPONSE_BYTES + 1)
            return resp.status, raw.decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        try:
            raw = e.read(MAX_RESPONSE_BYTES + 1)
        except OSError:
            raw = b""
        return e.code, raw.decode("utf-8", errors="replace")


def _count(value: object) -> int:
    """Token counts arrive as ints, numeric strings or garbage; garbage is 0."""
    if isinstance(value, bool):
        return 0
    try:
        return max(int(value), 0)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return 0


def _parse_usage(data: dict) -> Usage:
    u = data.get("usage")
    if not isinstance(u, dict):
        return Usage()
    return Usage(_count(u.get("prompt_tokens")), _count(u.get("completion_tokens")))


class OpenAICompatClient:
    def __init__(self, api_key: str | None = None, base_url: str | None = None,
                 model: str | None = None, transport: Transport | None = None,
                 sleep: Callable[[float], None] = time.sleep):
        key = api_key if api_key is not None else os.environ.get("LLM_API_KEY", "")
        self._api_key = key.strip() if isinstance(key, str) else ""
        self.base_url = (base_url or os.environ.get("LLM_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.model = model or os.environ.get("LLM_MODEL") or DEFAULT_MODEL
        self._transport = transport or _urllib_transport
        self._sleep = sleep

    def __repr__(self) -> str:
        return f"OpenAICompatClient(model={self.model!r}, base_url={self.base_url!r})"

    __str__ = __repr__

    def _scrub(self, text: str) -> str:
        if self._api_key:
            text = text.replace(self._api_key, "[REDACTED]")
        return _BEARER.sub("Bearer [REDACTED]", text)

    @staticmethod
    def _safe_message(m):
        """Copy of `m` with every string sent to the model redacted; never mutates `m`."""
        if not isinstance(m, dict):
            return m
        m = dict(m)
        content = m.get("content")
        if isinstance(content, str):
            m["content"] = safe_text(content)
        elif isinstance(content, list):
            parts = []
            for p in content:
                if isinstance(p, dict) and isinstance(p.get("text"), str):
                    p = {**p, "text": safe_text(p["text"])}
                parts.append(p)
            m["content"] = parts
        calls = m.get("tool_calls")
        if isinstance(calls, list):
            safe_calls = []
            for c in calls:
                fn = c.get("function") if isinstance(c, dict) else None
                if isinstance(fn, dict) and isinstance(fn.get("arguments"), str):
                    args = safe_text(fn["arguments"])
                    try:
                        json.loads(args)
                    except ValueError:
                        args = "{}"
                    c = {**c, "function": {**fn, "arguments": args}}
                safe_calls.append(c)
            m["tool_calls"] = safe_calls
        return m

    def _body(self, messages, tools, temperature, max_tokens, tool_choice) -> bytes:
        safe_messages = [self._safe_message(m) for m in messages]
        body: dict = {
            "model": self.model,
            "messages": safe_messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            body["tools"] = tools
        if tool_choice:
            body["tool_choice"] = tool_choice
        return json.dumps(body).encode("utf-8")

    def chat(self, messages: list[dict], tools: list[dict] | None = None,
             temperature: float = 0.2, max_tokens: int = 1500,
             tool_choice: dict | None = None) -> tuple[dict, Usage]:
        _check_base_url(self.base_url)
        if not self._api_key:
            raise LLMError("LLM_API_KEY is not set")
        body = self._body(messages, tools, temperature, max_tokens, tool_choice)
        url = f"{self.base_url}/chat/completions"
        headers = {"Authorization": f"Bearer {self._api_key}",
                   "Content-Type": "application/json"}

        last = "no response"
        for attempt in range(MAX_ATTEMPTS):
            failed = False
            try:
                status, text = self._transport(url, headers, body, TIMEOUT_SECONDS)
            except LLMError:
                raise
            except (OSError, http.client.HTTPException):
                status, text = 0, ""
                last = "connection error or timeout"
            except Exception:  # noqa: BLE001 - never surface transport internals
                failed = True
            else:
                if status == 429 or status >= 500:
                    last = f"HTTP {status}"
                elif status != 200:
                    snippet = self._scrub(str(text))[:ERROR_TEXT_CHARS]
                    raise LLMError(f"HTTP {status}: {snippet}" if snippet else f"HTTP {status}")
                else:
                    return self._parse(text)
            if failed:  # raised outside the except block so nothing is chained
                raise LLMError("transport failed")
            if attempt < MAX_ATTEMPTS - 1:
                self._sleep(min(2 ** attempt, 30))
        raise LLMError(f"Gave up after {MAX_ATTEMPTS} attempts ({last})")

    @staticmethod
    def _parse(text: str) -> tuple[dict, Usage]:
        if not isinstance(text, str) or len(text) > MAX_RESPONSE_BYTES:
            raise LLMError("Response too large or not text")
        try:
            data = json.loads(text)
        except ValueError:
            raise LLMError("Response was not valid JSON") from None
        choices = data.get("choices") if isinstance(data, dict) else None
        if not isinstance(choices, list) or not choices:
            raise LLMError("Response had no choices")
        first = choices[0]
        message = first.get("message") if isinstance(first, dict) else None
        if not isinstance(message, dict):
            raise LLMError("Response had no message")
        return message, _parse_usage(data)
