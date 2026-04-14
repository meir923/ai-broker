from __future__ import annotations

import json
import logging
import os
from typing import Any

import httpx

log = logging.getLogger(__name__)

XAI_API = "https://api.x.ai/v1/chat/completions"


class GrokClient:
    def __init__(self, model: str = "grok-3-mini-fast", timeout_s: float = 60.0) -> None:
        self.model = model
        self.timeout_s = timeout_s
        key = os.environ.get("GROK_API_KEY", "").strip()
        if not key:
            log.warning("GROK_API_KEY not set; Grok calls will fail")
        self._key = key

    def chat_json(self, system: str, user: str) -> dict[str, Any]:
        if not self._key:
            raise RuntimeError("Missing GROK_API_KEY")
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.2,
        }
        with httpx.Client(timeout=self.timeout_s) as client:
            r = client.post(
                XAI_API,
                headers={"Authorization": f"Bearer {self._key}", "Content-Type": "application/json"},
                json=payload,
            )
            r.raise_for_status()
            data = r.json()

        choices = data.get("choices") or []
        if not choices:
            log.error("Grok response has no choices: %s", str(data)[:400])
            return {"reasoning": "LLM returned empty response", "actions": []}
        msg = choices[0].get("message") or {}
        text = msg.get("content") or ""
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
        if not self._key:
            raise RuntimeError("Missing GROK_API_KEY")
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.3,
        }
        with httpx.Client(timeout=self.timeout_s) as client:
            r = client.post(
                XAI_API,
                headers={"Authorization": f"Bearer {self._key}", "Content-Type": "application/json"},
                json=payload,
            )
            r.raise_for_status()
            data = r.json()
        choices = data.get("choices") or []
        if not choices:
            return ""
        msg = choices[0].get("message") or {}
        return str(msg.get("content") or "")
