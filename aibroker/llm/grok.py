"""Grok LLM client — centralized xAI API access with retry, JSON mode,
connection pooling, usage tracking, and model tier support.

Models (April 2026):
  - grok-4.1-fast-non-reasoning : fast, cheap ($0.20/$0.50 per 1M tokens), 2M context
  - grok-4.20-non-reasoning     : flagship, smarter ($2/$6 per 1M tokens), 2M context
  - grok-4                      : deep reasoning + web search ($3/$15 per 1M tokens)

Environment variables:
  GROK_API_KEY          : required, xAI API key
  GROK_MODEL            : override default model (default: grok-4.1-fast-non-reasoning)
  GROK_TRADING_MODEL    : model for trading decisions (default: grok-4.1-fast-non-reasoning)
  GROK_SENTIMENT_MODEL  : model for sentiment analysis (default: grok-4.1-fast-non-reasoning)
  GROK_MACRO_MODEL      : model for macro regime calls (default: grok-4.1-fast-non-reasoning)
  GROK_TIMEOUT          : request timeout in seconds (default: 90)
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any

import httpx

log = logging.getLogger(__name__)

XAI_API = "https://api.x.ai/v1/chat/completions"

DEFAULT_MODEL = "grok-4.1-fast-non-reasoning"

COST_PER_1M: dict[str, tuple[float, float]] = {
    "grok-4.1-fast-non-reasoning": (0.20, 0.50),
    "grok-4.1-fast-reasoning": (0.20, 0.50),
    "grok-4.20-non-reasoning": (2.00, 6.00),
    "grok-4.20-reasoning": (2.00, 6.00),
    "grok-4": (3.00, 15.00),
    "grok-3-mini-fast": (0.10, 0.25),
}

_MAX_RETRIES = 3
_RETRY_BASE_DELAY = 1.5


class UsageTracker:
    """Thread-safe token usage and cost tracker."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0
        self.total_cost_usd = 0.0
        self.call_count = 0
        self.error_count = 0

    def record(self, model: str, prompt_tokens: int, completion_tokens: int) -> None:
        input_rate, output_rate = COST_PER_1M.get(model, (1.0, 3.0))
        cost = (prompt_tokens * input_rate + completion_tokens * output_rate) / 1_000_000
        with self._lock:
            self.total_prompt_tokens += prompt_tokens
            self.total_completion_tokens += completion_tokens
            self.total_cost_usd += cost
            self.call_count += 1

    def record_error(self) -> None:
        with self._lock:
            self.error_count += 1

    def summary(self) -> dict[str, Any]:
        with self._lock:
            return {
                "calls": self.call_count,
                "errors": self.error_count,
                "prompt_tokens": self.total_prompt_tokens,
                "completion_tokens": self.total_completion_tokens,
                "total_tokens": self.total_prompt_tokens + self.total_completion_tokens,
                "estimated_cost_usd": round(self.total_cost_usd, 4),
            }

    def reset(self) -> None:
        with self._lock:
            self.total_prompt_tokens = 0
            self.total_completion_tokens = 0
            self.total_cost_usd = 0.0
            self.call_count = 0
            self.error_count = 0


usage = UsageTracker()


class GrokClient:
    def __init__(
        self,
        model: str | None = None,
        timeout_s: float | None = None,
        temperature: float = 0.2,
        max_tokens: int = 2048,
    ) -> None:
        self.model = model or os.environ.get("GROK_MODEL", DEFAULT_MODEL)
        self.timeout_s = timeout_s or float(os.environ.get("GROK_TIMEOUT", "90"))
        self.temperature = temperature
        self.max_tokens = max_tokens
        key = os.environ.get("GROK_API_KEY", "").strip()
        if not key:
            log.warning("GROK_API_KEY not set; Grok calls will fail")
        self._key = key
        self._client: httpx.Client | None = None
        self._lock = threading.Lock()

    def _get_client(self) -> httpx.Client:
        """Lazy-init persistent HTTP client (connection pooling)."""
        if self._client is None or self._client.is_closed:
            with self._lock:
                if self._client is None or self._client.is_closed:
                    self._client = httpx.Client(
                        timeout=self.timeout_s,
                        limits=httpx.Limits(
                            max_connections=10,
                            max_keepalive_connections=5,
                            keepalive_expiry=120,
                        ),
                    )
        return self._client

    def close(self) -> None:
        if self._client and not self._client.is_closed:
            self._client.close()
            self._client = None

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._key}",
            "Content-Type": "application/json",
        }

    def _call_api(self, payload: dict[str, Any]) -> dict[str, Any]:
        """POST to xAI API with retry + exponential backoff."""
        if not self._key:
            raise RuntimeError("Missing GROK_API_KEY")

        client = self._get_client()
        last_exc: Exception | None = None

        for attempt in range(_MAX_RETRIES):
            try:
                r = client.post(XAI_API, headers=self._headers(), json=payload)
                if r.status_code == 429:
                    retry_after = float(r.headers.get("retry-after", _RETRY_BASE_DELAY * (2 ** attempt)))
                    log.warning("Rate limited (429), retrying in %.1fs", retry_after)
                    time.sleep(retry_after)
                    continue
                if r.status_code >= 500:
                    delay = _RETRY_BASE_DELAY * (2 ** attempt)
                    log.warning("Server error %d, retrying in %.1fs", r.status_code, delay)
                    time.sleep(delay)
                    continue
                r.raise_for_status()
                data = r.json()
                self._track_usage(data)
                return data
            except httpx.TimeoutException as e:
                last_exc = e
                delay = _RETRY_BASE_DELAY * (2 ** attempt)
                log.warning("Timeout on attempt %d, retrying in %.1fs", attempt + 1, delay)
                time.sleep(delay)
            except httpx.HTTPStatusError as e:
                if e.response.status_code in (429, 500, 502, 503, 504):
                    last_exc = e
                    delay = _RETRY_BASE_DELAY * (2 ** attempt)
                    log.warning("HTTP %d on attempt %d, retrying in %.1fs",
                                e.response.status_code, attempt + 1, delay)
                    time.sleep(delay)
                else:
                    usage.record_error()
                    raise

        usage.record_error()
        raise RuntimeError(f"xAI API failed after {_MAX_RETRIES} retries: {last_exc}")

    def _track_usage(self, data: dict[str, Any]) -> None:
        u = data.get("usage", {})
        pt = int(u.get("prompt_tokens", 0))
        ct = int(u.get("completion_tokens", 0))
        if pt or ct:
            usage.record(self.model, pt, ct)

    def _extract_content(self, data: dict[str, Any]) -> str:
        choices = data.get("choices") or []
        if not choices:
            log.error("Grok response has no choices: %s", str(data)[:400])
            return ""
        msg = choices[0].get("message") or {}
        return str(msg.get("content") or "")

    def chat_json(self, system: str, user: str) -> dict[str, Any]:
        """Send prompt and parse JSON response. Uses JSON mode for guaranteed valid JSON."""
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "response_format": {"type": "json_object"},
        }

        data = self._call_api(payload)
        text = self._extract_content(data)

        if not text.strip():
            log.error("Grok response content is empty")
            return {"reasoning": "LLM returned empty content", "actions": []}

        log.debug("Grok raw response: %s", text[:500])
        text = text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            log.error("Grok returned invalid JSON: %s — raw: %s", e, text[:400])
            return {"reasoning": f"LLM returned non-JSON: {text[:100]}", "actions": []}

    def chat_text(self, system: str, user: str) -> str:
        """Send prompt and return raw text response."""
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.3,
            "max_tokens": self.max_tokens,
        }

        data = self._call_api(payload)
        return self._extract_content(data)


# ---------------------------------------------------------------------------
# Task-specific client factories (model per task type)
# ---------------------------------------------------------------------------

_clients: dict[str, GrokClient] = {}
_factory_lock = threading.Lock()


def get_trading_client() -> GrokClient:
    """Client for trading decisions — uses GROK_TRADING_MODEL or default."""
    return _get_or_create(
        "trading",
        os.environ.get("GROK_TRADING_MODEL", DEFAULT_MODEL),
        temperature=0.15,
        max_tokens=2048,
    )


def get_sentiment_client() -> GrokClient:
    """Client for sentiment analysis — uses GROK_SENTIMENT_MODEL or default."""
    return _get_or_create(
        "sentiment",
        os.environ.get("GROK_SENTIMENT_MODEL", DEFAULT_MODEL),
        temperature=0.2,
        max_tokens=1024,
    )


def get_macro_client() -> GrokClient:
    """Client for macro regime assessment — uses GROK_MACRO_MODEL or default."""
    return _get_or_create(
        "macro",
        os.environ.get("GROK_MACRO_MODEL", DEFAULT_MODEL),
        temperature=0.2,
        max_tokens=512,
    )


def get_chat_client() -> GrokClient:
    """Client for interactive chat."""
    return _get_or_create(
        "chat",
        os.environ.get("GROK_MODEL", DEFAULT_MODEL),
        temperature=0.4,
        max_tokens=4096,
    )


def _get_or_create(
    task: str, model: str, temperature: float, max_tokens: int,
) -> GrokClient:
    with _factory_lock:
        existing = _clients.get(task)
        if existing and existing.model == model:
            return existing
        client = GrokClient(
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        _clients[task] = client
        return client
