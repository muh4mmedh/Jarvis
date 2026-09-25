"""Gemini native speech — the engine with a personality.

edge-tts reads text. This *performs* it: the request carries a plain-English
direction ("dry, faintly amused, unhurried") alongside the line, and the model
acts it. That is the difference between a screen reader and something that
sounds like a person, and it is why this is the default when a key is present.

Returns 24 kHz mono PCM, wrapped in a WAV header so a browser can play it
without any decoding on our side.
"""
from __future__ import annotations

import base64
import io
import logging
import struct

import httpx

from .config import cfg
from .voice import VoiceError, clean_for_speech

log = logging.getLogger("jarvis.voice.gemini")

BASE = "https://generativelanguage.googleapis.com/v1beta"

# Deeper, measured voices first — the ones that suit the character.
VOICE_PREFERENCE = ["Charon", "Orus", "Enceladus", "Iapetus", "Algieba", "Puck"]

PREBUILT_VOICES = [
    ("Charon", "Deep, measured, informative — closest to the films"),
    ("Orus", "Firm and level"),
    ("Enceladus", "Breathy, quieter"),
    ("Iapetus", "Clear and even"),
    ("Algieba", "Smooth, low"),
    ("Puck", "Brighter, more animated"),
    ("Zephyr", "Bright"),
    ("Fenrir", "Excitable"),
    ("Kore", "Firm, female"),
    ("Leda", "Youthful, female"),
    ("Aoede", "Breezy, female"),
    ("Callirrhoe", "Easy-going, female"),
    ("Autonoe", "Bright, female"),
    ("Umbriel", "Easy-going"),
    ("Erinome", "Clear, female"),
    ("Rasalgethi", "Informative"),
    ("Achernar", "Soft, female"),
    ("Schedar", "Even"),
    ("Gacrux", "Mature"),
    ("Sadaltager", "Knowledgeable"),
]

DEFAULT_STYLE = (
    "Speak as a composed British butler: dry, understated and faintly amused, "
    "unhurried, warm underneath the formality. Let the meaning shape the "
    "delivery — a touch of wry emphasis where it belongs, never theatrical."
)

MODEL_CANDIDATES = [
    "gemini-2.5-flash-preview-tts",
    "gemini-2.5-pro-preview-tts",
]

_model_cache: str | None = None


def _wav(pcm: bytes, rate: int = 24000, channels: int = 1, bits: int = 16) -> bytes:
    """Wrap raw PCM in a WAV container. Gemini returns headerless samples."""
    block_align = channels * bits // 8
    buf = io.BytesIO()
    buf.write(b"RIFF")
    buf.write(struct.pack("<I", 36 + len(pcm)))
    buf.write(b"WAVEfmt ")
    buf.write(struct.pack("<IHHIIHH", 16, 1, channels, rate,
                          rate * block_align, block_align, bits))
    buf.write(b"data")
    buf.write(struct.pack("<I", len(pcm)))
    buf.write(pcm)
    return buf.getvalue()


def _rate_from_mime(mime: str) -> int:
    """Gemini reports e.g. 'audio/L16;codec=pcm;rate=24000'."""
    for part in (mime or "").split(";"):
        part = part.strip()
        if part.startswith("rate="):
            try:
                return int(part[5:])
            except ValueError:
                break
    return 24000


async def available_models(key: str) -> list[str]:
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(f"{BASE}/models", headers={"x-goog-api-key": key},
                        params={"pageSize": 200})
        if r.status_code != 200:
            raise VoiceError(f"Could not list models ({r.status_code}).")
        return [m["name"].removeprefix("models/") for m in r.json().get("models", [])
                if "tts" in m["name"].lower()]


async def resolve_model() -> str:
    global _model_cache
    if _model_cache:
        return _model_cache
    configured = str(cfg.get("voice.gemini_model", "") or "").strip()
    if configured:
        _model_cache = configured
        return configured

    key = cfg.api_key
    if not key:
        raise VoiceError("No Gemini API key configured.")
    try:
        found = await available_models(key)
    except VoiceError:
        found = []
    for want in MODEL_CANDIDATES:
        if want in found:
            _model_cache = want
            return want
    if found:
        _model_cache = sorted(found)[0]
        return _model_cache
    # Nothing listed: try the documented name anyway and let the call report.
    _model_cache = MODEL_CANDIDATES[0]
    return _model_cache


def invalidate() -> None:
    global _model_cache
    _model_cache = ""


def voice_name() -> str:
    configured = str(cfg.get("voice.gemini_voice", "") or "").strip()
    return configured or VOICE_PREFERENCE[0]


async def synthesise(text: str) -> tuple[bytes, str]:
    """Return (wav_bytes, mime). Raises VoiceError so the caller can fall back."""
    key = cfg.api_key
    if not key:
        raise VoiceError("No Gemini API key configured.")

    spoken = clean_for_speech(text)
    if not spoken:
        raise VoiceError("nothing to say")
    if len(spoken) > 2500:
        spoken = spoken[:2500].rsplit(".", 1)[0] + "."

    style = str(cfg.get("voice.style", "") or "").strip() or DEFAULT_STYLE
    model = await resolve_model()

    body = {
        "contents": [{"parts": [{"text": f"{style}\n\nSay this:\n{spoken}"}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {
                "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice_name()}}
            },
        },
    }

    try:
        async with httpx.AsyncClient(timeout=90) as c:
            r = await c.post(f"{BASE}/models/{model}:generateContent",
                             headers={"x-goog-api-key": key,
                                      "Content-Type": "application/json"},
                             json=body)
    except httpx.RequestError as exc:
        raise VoiceError(f"Could not reach the speech service: {exc}")

    if r.status_code != 200:
        detail = r.text[:300]
        try:
            detail = r.json().get("error", {}).get("message", detail)
        except Exception:
            pass
        if r.status_code == 429:
            raise VoiceError("Speech rate limit reached on the free tier.")
        raise VoiceError(f"Speech failed ({r.status_code}): {detail}")

    try:
        part = r.json()["candidates"][0]["content"]["parts"][0]
        inline = part.get("inlineData") or part.get("inline_data") or {}
        pcm = base64.b64decode(inline["data"])
        mime = inline.get("mimeType") or inline.get("mime_type") or ""
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise VoiceError(f"Unexpected speech response: {exc}")

    if not pcm:
        raise VoiceError("The speech service returned no audio.")

    return _wav(pcm, rate=_rate_from_mime(mime)), "audio/wav"


def catalogue() -> list[dict]:
    return [{"id": name, "description": desc,
             "recommended": name in VOICE_PREFERENCE[:3]}
            for name, desc in PREBUILT_VOICES]
