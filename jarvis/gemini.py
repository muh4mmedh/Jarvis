"""Thin async client for the Gemini API — streaming + function calling.

Deliberately talks raw REST rather than pulling in an SDK: one dependency
(httpx), no version churn, and full control over the parts we echo back
(thought signatures included, which newer models require on tool turns).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

import httpx

from .config import cfg

log = logging.getLogger("jarvis.gemini")

BASE = "https://generativelanguage.googleapis.com/v1beta"


class GeminiError(RuntimeError):
    pass


@dataclass
class StreamResult:
    """What one model turn produced."""
    text: str = ""
    # Raw parts exactly as the API emitted them. Echoed back verbatim on the
    # next request so thought signatures survive the round trip.
    parts: list[dict] = field(default_factory=list)
    calls: list[dict] = field(default_factory=list)   # [{name, args}]
    finish_reason: str = ""
    usage: dict = field(default_factory=dict)


class Gemini:
    def __init__(self) -> None:
        self._model_cache = ""
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(180.0, connect=15.0))

    # Read live from config so a key or model changed in the Settings panel
    # takes effect on the very next request — no restart required.
    @property
    def api_key(self) -> str:
        return cfg.api_key

    @property
    def model(self) -> str:
        configured = str(cfg.get("model.name") or "").strip()
        return configured or self._model_cache

    @model.setter
    def model(self, value: str) -> None:
        self._model_cache = value

    def invalidate(self) -> None:
        """Called after settings change so the next call re-resolves the model."""
        self._model_cache = ""

    # ── setup ───────────────────────────────────────────────────────
    async def list_models(self) -> list[str]:
        r = await self._client.get(f"{BASE}/models", headers=self._headers(), params={"pageSize": 200})
        if r.status_code != 200:
            raise GeminiError(f"ListModels failed [{r.status_code}]: {r.text[:300]}")
        out = []
        for m in r.json().get("models", []):
            if "generateContent" in (m.get("supportedGenerationMethods") or []):
                out.append(m["name"].removeprefix("models/"))
        return out

    async def resolve_model(self) -> str:
        """Pick a working Flash model on this key, honouring config preference order."""
        if self.model:
            return self.model
        try:
            available = await self.list_models()
        except Exception as exc:
            log.warning("model auto-detect failed (%s) — falling back", exc)
            self.model = "gemini-flash-latest"
            return self.model

        prefs = cfg.get("model.prefer", []) or []
        # exact match first, then prefix match (catches dated suffixes like -001)
        for p in prefs:
            if p in available:
                self.model = p
                return p
        for p in prefs:
            hits = sorted(m for m in available if m.startswith(p))
            if hits:
                self.model = hits[0]
                return self.model
        # last resort: any flash, else any model at all
        flash = sorted(m for m in available if "flash" in m and "thinking" not in m)
        self.model = flash[0] if flash else (available[0] if available else "gemini-flash-latest")
        return self.model

    def _headers(self) -> dict:
        if not self.api_key:
            raise GeminiError(
                "No API key configured. Open Settings (top right) and paste your Gemini key — "
                "you can get one free at https://aistudio.google.com/apikey"
            )
        return {"x-goog-api-key": self.api_key, "Content-Type": "application/json"}

    # ── generation ──────────────────────────────────────────────────
    async def stream(
        self,
        contents: list[dict],
        system_instruction: str,
        tool_declarations: list[dict] | None = None,
    ) -> AsyncIterator[dict]:
        """Yield events: {'type':'text','delta':str} | {'type':'done','result':StreamResult}"""
        model = self.model or await self.resolve_model()
        body: dict[str, Any] = {
            "contents": contents,
            "systemInstruction": {"parts": [{"text": system_instruction}]},
            "generationConfig": {
                "temperature": float(cfg.get("model.temperature", 0.85)),
                "maxOutputTokens": int(cfg.get("model.max_output_tokens", 8192)),
            },
        }
        if tool_declarations:
            body["tools"] = [{"functionDeclarations": tool_declarations}]

        url = f"{BASE}/models/{model}:streamGenerateContent"
        result = StreamResult()

        try:
            async with self._client.stream(
                "POST", url, headers=self._headers(), params={"alt": "sse"}, json=body
            ) as resp:
                if resp.status_code != 200:
                    raw = (await resp.aread()).decode("utf-8", "replace")
                    raise GeminiError(_explain_http(resp.status_code, raw, model))

                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if not payload or payload == "[DONE]":
                        continue
                    try:
                        chunk = json.loads(payload)
                    except json.JSONDecodeError:
                        continue

                    if "error" in chunk:
                        raise GeminiError(str(chunk["error"].get("message", chunk["error"])))

                    if usage := chunk.get("usageMetadata"):
                        result.usage = {
                            "in": usage.get("promptTokenCount", 0),
                            "out": usage.get("candidatesTokenCount", 0),
                            "total": usage.get("totalTokenCount", 0),
                        }

                    for cand in chunk.get("candidates", []):
                        if fr := cand.get("finishReason"):
                            result.finish_reason = fr
                        for part in (cand.get("content") or {}).get("parts", []) or []:
                            result.parts.append(part)
                            if fc := part.get("functionCall"):
                                result.calls.append(
                                    {"name": fc.get("name", ""), "args": fc.get("args") or {}}
                                )
                            elif "text" in part and not part.get("thought"):
                                delta = part["text"]
                                result.text += delta
                                yield {"type": "text", "delta": delta}
        except httpx.TimeoutException:
            raise GeminiError("Gemini did not respond in time. The request may have been too large.")
        except httpx.RequestError as exc:
            raise GeminiError(f"Network error reaching Gemini: {exc}")

        yield {"type": "done", "result": result}

    async def vision(self, prompt: str, image_b64: str | list[str],
                     mime: str = "image/png") -> str:
        """Ask about one image, or about a sequence of them.

        Passing several frames lets the model reason about what changed
        between them, which is what makes watching different from glancing.
        """
        model = self.model or await self.resolve_model()
        images = [image_b64] if isinstance(image_b64, str) else list(image_b64)
        parts: list[dict] = []
        for i, img in enumerate(images):
            if len(images) > 1:
                parts.append({"text": f"Frame {i + 1} of {len(images)}:"})
            parts.append({"inlineData": {"mimeType": mime, "data": img}})
        parts.append({"text": prompt})

        body = {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {"temperature": 0.3, "maxOutputTokens": 2048},
        }
        r = await self._client.post(
            f"{BASE}/models/{model}:generateContent", headers=self._headers(), json=body
        )
        if r.status_code != 200:
            raise GeminiError(_explain_http(r.status_code, r.text, model))
        parts = (r.json().get("candidates") or [{}])[0].get("content", {}).get("parts", [])
        return "".join(p.get("text", "") for p in parts).strip() or "(no description returned)"

    async def aclose(self) -> None:
        await self._client.aclose()


def _explain_http(status: int, raw: str, model: str) -> str:
    """Turn Google's error envelope into something actionable."""
    detail = raw[:400]
    try:
        detail = json.loads(raw).get("error", {}).get("message", detail)
    except Exception:
        pass
    if status == 400 and "API key not valid" in detail:
        return "That API key was rejected. Check GEMINI_API_KEY in your .env file."
    if status == 403:
        return f"Access denied for model '{model}'. The key may lack permission for it. ({detail})"
    if status == 404:
        return (f"Model '{model}' does not exist on this key. Clear GEMINI_MODEL in .env "
                f"to let me auto-detect one, or run: python -m jarvis.cli models")
    if status == 429:
        return "Rate limit hit on the Gemini free tier. Wait a moment and try again."
    if status >= 500:
        return f"Google's side is having trouble ({status}). Worth retrying. ({detail})"
    return f"Gemini API error {status}: {detail}"


gemini = Gemini()
