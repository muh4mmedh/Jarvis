"""Transport-independent request handling.

Every payload the HUD can ask for is built here as a plain function. The HTTP
server and the native in-process bridge are both thin wrappers over these, so
the two transports can never drift apart.
"""
from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any

from .config import cfg
from .gemini import Gemini, gemini
from .memory import memory
from .permissions import describe_mode
from .registry import REGISTRY, catalogue
from .voice import VoiceError, list_neural_voices, synthesise

log = logging.getLogger("jarvis.api")

STATE: dict[str, Any] = {"model": "", "booted_at": time.time()}


def boot_payload() -> dict:
    """Everything the HUD needs to render itself."""
    return {
        "identity": {
            "name": cfg.get("identity.name", "JARVIS"),
            "expansion": cfg.get("identity.expansion", ""),
            "address": cfg.get("identity.address_user_as", "Sir"),
        },
        "model": STATE["model"],
        "security": describe_mode(),
        "voice": {
            "tts": bool(cfg.get("voice.tts_enabled", True)),
            "stt": bool(cfg.get("voice.stt_enabled", True)),
            "wake_word": cfg.get("voice.wake_word", "jarvis"),
            "prefer": cfg.get("voice.voice_prefer", []),
            "engine": cfg.get("voice.engine", "gemini"),
            "gemini_voice": cfg.get("voice.gemini_voice", "Charon"),
            "neural_voice": cfg.get("voice.neural_voice", "en-GB-RyanNeural"),
            "always_listening": bool(cfg.get("voice.always_listening", False)),
            "chime": bool(cfg.get("voice.chime", True)),
            "rate": cfg.get("voice.rate", 1.04),
            "pitch": cfg.get("voice.pitch", 0.92),
        },
        "ui": {
            "boot_sequence": bool(cfg.get("ui.boot_sequence", True)),
            "accent": cfg.get("ui.theme_accent", "#38e0ff"),
            "warn": cfg.get("ui.theme_warn", "#ffb648"),
            "alert": cfg.get("ui.theme_alert", "#ff4d5a"),
        },
        "capabilities": catalogue(),
        "memory": memory.stats(),
        "api_key_present": bool(cfg.api_key),
        "tools": len(REGISTRY),
    }


def history_payload() -> dict:
    return {"turns": memory.recent_turns(60)}


def settings_payload() -> dict:
    return cfg.export_for_ui()


async def _resolve_model_later(notify=None) -> None:
    """Re-resolve the model without making the user wait for the network."""
    try:
        STATE["model"] = await gemini.resolve_model()
        if notify:
            notify({"type": "model_changed", "model": STATE["model"]})
    except Exception as exc:
        log.warning("model re-resolution failed after settings change: %s", exc)
        if notify:
            notify({"type": "notice",
                    "message": f"Settings saved, but the model could not be resolved: {exc}"})


async def apply_settings(patch: dict, notify=None) -> dict:
    """Persist a settings patch.

    Changing the key or model needs a round trip to Google to pick a model.
    That used to run inline, so saving blocked on the network for as long as
    it took — up to three minutes on a bad connection, with the panel stuck on
    "Saving...". It now runs detached and the resolved name is pushed when it
    arrives.
    """
    changed = cfg.update(patch or {})
    if any(c.startswith(("model.", "secrets.")) for c in changed):
        gemini.invalidate()
        if cfg.api_key:
            asyncio.create_task(_resolve_model_later(notify))
    return {
        "ok": True,
        "changed": changed,
        "model": STATE["model"],
        "restart_required": [c for c in changed if c.startswith("server.")],
        "settings": cfg.export_for_ui(),
    }


async def test_key(candidate: str) -> dict:
    """Validate an API key against Google without committing to it."""
    candidate = (candidate or "").strip() or cfg.api_key
    if not candidate:
        return {"ok": False, "error": "No key supplied."}

    original = cfg.get("secrets.gemini_api_key")
    probe = Gemini()
    try:
        cfg.update({"secrets.gemini_api_key": candidate}, persist=False)
        models = await probe.list_models()
        chosen = await probe.resolve_model()
        return {
            "ok": True,
            "model_count": len(models),
            "would_use": chosen,
            "models": sorted(m for m in models if "flash" in m or "pro" in m)[:40],
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    finally:
        cfg.update({"secrets.gemini_api_key": original}, persist=False)
        await probe.aclose()


async def voices_payload() -> dict:
    try:
        return {"ok": True, "voices": await list_neural_voices()}
    except VoiceError as exc:
        return {"ok": False, "error": str(exc), "voices": []}


async def tts_bytes(text: str) -> tuple[bytes, str]:
    """Render speech with the configured engine.

    Gemini's voice carries the character; edge-tts is the dependable fallback.
    Any failure in the first drops to the second rather than going silent.
    """
    if not (text or "").strip():
        raise VoiceError("No text supplied.")

    engine = str(cfg.get("voice.engine", "gemini")).lower()

    if engine == "gemini" and cfg.api_key:
        try:
            from . import voice_gemini
            return await voice_gemini.synthesise(text)
        except VoiceError as exc:
            log.warning("gemini speech failed (%s) — falling back to edge-tts", exc)
        except Exception as exc:
            log.warning("gemini speech error (%s) — falling back to edge-tts", exc)

    return await synthesise(text), "audio/mpeg"


async def enrol_face(name: str = "", samples: int = 5) -> dict:
    """Enrol the operator's face directly.

    Deliberately does not go through the model: setting yourself up as the
    recognised operator is a settings action, and routing it through a
    conversation made it slow and unreliable.
    """
    from . import faceid
    from .bridge import NoClient, bridge

    if not faceid.models_present():
        try:
            await asyncio.to_thread(faceid.download_models)
        except faceid.FaceError as exc:
            return {"ok": False, "error": str(exc)}

    n = max(3, min(int(samples or 5), 8))
    try:
        clip = await bridge.request(
            "camera_clip", {"frames": n, "interval_ms": 900}, timeout=n * 3 + 40)
    except NoClient as exc:
        return {"ok": False, "error": str(exc)}
    except TimeoutError:
        return {"ok": False, "error": "The camera did not deliver frames in time."}
    except RuntimeError as exc:
        return {"ok": False, "error": f"Camera unavailable: {exc}"}

    images = (clip or {}).get("images") or []
    if not images:
        return {"ok": False, "error": "The camera returned no frames."}

    # Start clean so re-enrolling replaces rather than blends with an old face.
    await asyncio.to_thread(faceid.forget)

    stored, rejected = 0, 0
    for img in images:
        try:
            info = await asyncio.to_thread(faceid.add_sample, img, name)
            stored = info["samples"]
        except faceid.FaceError:
            rejected += 1

    if not stored:
        return {"ok": False,
                "error": "No face was visible in any frame. Face the camera, "
                         "make sure the room is lit, and try again."}

    return {"ok": True, "name": name, "samples": stored, "rejected": rejected,
            "status": faceid.status()}


def telemetry() -> dict:
    """One snapshot for the HUD gauges. Degrades quietly without psutil."""
    try:
        import psutil
    except ImportError:
        return {"available": False}

    vm = psutil.virtual_memory()
    snap: dict = {
        "available": True,
        "cpu": psutil.cpu_percent(interval=None),
        "cpu_cores": psutil.cpu_percent(interval=None, percpu=True)[:16],
        "memory": vm.percent,
        "memory_used_gb": round(vm.used / 1e9, 1),
        "memory_total_gb": round(vm.total / 1e9, 1),
        "uptime_h": round((time.time() - psutil.boot_time()) / 3600, 1),
    }
    try:
        snap["disk"] = psutil.disk_usage("C:\\" if Path("C:\\").exists() else "/").percent
    except Exception:
        snap["disk"] = 0
    try:
        if batt := psutil.sensors_battery():
            snap["battery"] = round(batt.percent)
            snap["charging"] = batt.power_plugged
    except Exception:
        pass
    try:
        net = psutil.net_io_counters()
        snap["net_sent"] = net.bytes_sent
        snap["net_recv"] = net.bytes_recv
    except Exception:
        pass
    snap["processes"] = len(psutil.pids())
    return snap


async def resolve_startup_model() -> str:
    """Called once at boot by whichever shell is running."""
    if not cfg.api_key:
        return ""
    try:
        STATE["model"] = await gemini.resolve_model()
    except Exception as exc:
        log.error("could not resolve a model: %s", exc)
        STATE["model"] = cfg.get("model.name") or "(unresolved)"
    return STATE["model"]
