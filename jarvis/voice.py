"""Speech synthesis.

Two engines:

  neural   Microsoft Edge's online neural voices via edge-tts. No API key, no
           account. `en-GB-RyanNeural` is the closest thing to the films — a
           measured British male — and is the default.
  browser  The operating system's own voices through the Web Speech API.
           Lower quality, but works with no network at all.

The client asks the server for audio; if that fails for any reason it falls
back to the browser engine on its own, so speech never simply stops working.
"""
from __future__ import annotations

import asyncio
import logging
import re

from .config import cfg

log = logging.getLogger("jarvis.voice")

# Ordered best-first. The first that exists on the service wins if the
# configured voice is unavailable.
BRITISH_PREFERENCE = [
    "en-GB-RyanNeural",      # measured British male — the JARVIS default
    "en-GB-ThomasNeural",    # crisper, more formal
    "en-IE-ConnorNeural",
    "en-AU-WilliamMultilingualNeural",
    "en-GB-SoniaNeural",
]

_voices_cache: list[dict] | None = None


class VoiceError(RuntimeError):
    pass


def _pct(value: float, unit: str = "%") -> str:
    """edge-tts wants signed offsets like '+8%' or '-4Hz'."""
    n = int(round(value))
    return f"{n:+d}{unit}"


def clean_for_speech(text: str) -> str:
    """Strip anything that would be read aloud as noise."""
    text = re.sub(r"```[\s\S]*?```", " — code omitted — ", text)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    # Markdown links must go before bare URLs, or the URL rule eats the target
    # and leaves a broken "[text](a link" behind.
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    # Stop the URL match at a closing bracket so trailing punctuation survives.
    text = re.sub(r"https?://[^\s)\]]+", "a link", text)
    text = re.sub(r"[*_#>|]", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


async def list_neural_voices() -> list[dict]:
    """English neural voices available from the Edge service."""
    global _voices_cache
    if _voices_cache is not None:
        return _voices_cache
    try:
        import edge_tts
    except ImportError:
        raise VoiceError("edge-tts is not installed — run: pip install edge-tts")

    try:
        raw = await edge_tts.list_voices()
    except Exception as exc:
        raise VoiceError(f"Could not reach the voice service: {exc}")

    out = []
    for v in raw:
        if not v["Locale"].startswith("en"):
            continue
        tag = v.get("VoiceTag", {}) or {}
        out.append({
            "id": v["ShortName"],
            "locale": v["Locale"],
            "gender": v["Gender"],
            "traits": "/".join(tag.get("VoicePersonalities", []) or []),
            "recommended": v["ShortName"] in BRITISH_PREFERENCE,
        })
    # British first, then the rest alphabetically.
    out.sort(key=lambda v: (not v["recommended"], not v["locale"].startswith("en-GB"), v["id"]))
    _voices_cache = out
    return out


async def resolve_voice() -> str:
    configured = str(cfg.get("voice.neural_voice", "") or "").strip()
    if configured:
        return configured
    try:
        available = {v["id"] for v in await list_neural_voices()}
        for want in BRITISH_PREFERENCE:
            if want in available:
                return want
    except VoiceError:
        pass
    return BRITISH_PREFERENCE[0]


async def synthesise(text: str) -> bytes:
    """Render text to MP3 bytes. Raises VoiceError if the engine is unavailable."""
    try:
        import edge_tts
    except ImportError:
        raise VoiceError("edge-tts is not installed — run: pip install edge-tts")

    spoken = clean_for_speech(text)
    if not spoken:
        raise VoiceError("nothing to say")
    # Keep a runaway response from becoming a five-minute monologue.
    if len(spoken) > 3000:
        spoken = spoken[:3000].rsplit(".", 1)[0] + "."

    voice = await resolve_voice()

    # UI stores rate as a multiplier (1.0 = normal) and pitch likewise;
    # edge-tts wants percentage and Hertz offsets.
    rate = float(cfg.get("voice.rate", 1.04))
    pitch = float(cfg.get("voice.pitch", 0.92))

    try:
        comm = edge_tts.Communicate(
            spoken,
            voice,
            rate=_pct((rate - 1.0) * 100),
            pitch=_pct((pitch - 1.0) * 50, "Hz"),
        )
        audio = bytearray()
        async for chunk in comm.stream():
            if chunk["type"] == "audio":
                audio.extend(chunk["data"])
    except Exception as exc:
        raise VoiceError(f"Speech synthesis failed: {exc}")

    if not audio:
        raise VoiceError("The voice service returned no audio.")
    return bytes(audio)


async def available() -> dict:
    """Report engine health for the Settings panel."""
    try:
        import edge_tts  # noqa: F401
    except ImportError:
        return {"neural": False, "reason": "edge-tts not installed"}
    try:
        voices = await asyncio.wait_for(list_neural_voices(), timeout=12)
        return {"neural": True, "count": len(voices), "using": await resolve_voice()}
    except (VoiceError, asyncio.TimeoutError) as exc:
        return {"neural": False, "reason": str(exc)}
