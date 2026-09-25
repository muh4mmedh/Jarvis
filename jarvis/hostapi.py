"""The native transport.

Replaces the HTTP server and WebSocket with direct in-process calls between
the page and Python. The page reaches Python through `window.pywebview.api`;
Python reaches the page by evaluating a single push function. Nothing binds a
socket, so there is no port, no origin and nothing on the network stack.

Everything the HUD asks for is served by jarvis.api, exactly as the HTTP
transport serves it, so the two cannot diverge.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import threading
import time

from . import api
from .brain import Brain
from .bridge import bridge
from .config import cfg
from .memory import memory
from .permissions import describe_mode
from .registry import REGISTRY
from .voice import VoiceError

log = logging.getLogger("jarvis.hostapi")

# Module-level state. Nothing here may become an attribute of HostAPI —
# pywebview introspects that object and will walk into anything it finds.
_LOOP: asyncio.AbstractEventLoop | None = None
_BRAIN: Brain | None = None
_WINDOW = None
_ALIVE = False


# ── Python -> page ──────────────────────────────────────────────────
def push(payload: dict) -> None:
    """Deliver an event to the HUD. Mirrors a WebSocket server->client frame."""
    if _WINDOW is None or not _ALIVE:
        return
    try:
        blob = json.dumps(payload, default=str)
        _WINDOW.evaluate_js(f"window.__jarvisPush({blob})")
    except Exception as exc:
        # A push failing must never take down the turn that produced it.
        log.debug("push failed (%s): %s", payload.get("type"), exc)


async def _emit(payload: dict) -> None:
    """Async emit signature the Brain expects."""
    push(payload)


# ── background event loop ───────────────────────────────────────────
def _run_loop() -> None:
    global _LOOP
    loop = asyncio.new_event_loop()
    _LOOP = loop
    asyncio.set_event_loop(loop)
    loop.run_forever()


def _submit(coro):
    """Schedule a coroutine on the worker loop from a pywebview thread."""
    if _LOOP is None:
        raise RuntimeError("event loop is not running")
    return asyncio.run_coroutine_threadsafe(coro, _LOOP)


def _await(coro, timeout: float = 180):
    return _submit(coro).result(timeout)


def _telemetry_loop() -> None:
    interval = float(cfg.get("ui.telemetry_interval", 2.0))
    try:
        import psutil
        psutil.cpu_percent(interval=None)      # prime the counters
    except ImportError:
        pass
    while _ALIVE:
        push({"type": "telemetry", "data": api.telemetry()})
        time.sleep(interval)


def start(window) -> None:
    """Bring the bridge up. Called once the window exists."""
    global _WINDOW, _BRAIN, _ALIVE

    _WINDOW = window
    _ALIVE = True

    threading.Thread(target=_run_loop, daemon=True, name="jarvis-loop").start()
    for _ in range(100):                        # wait for the loop to exist
        if _LOOP is not None:
            break
        time.sleep(0.02)

    _BRAIN = Brain(_emit)
    bridge.attach(_emit)                        # lets tools request camera frames

    try:
        _await(api.resolve_startup_model(), timeout=60)
    except Exception as exc:
        log.warning("startup model resolution failed: %s", exc)

    threading.Thread(target=_telemetry_loop, daemon=True, name="jarvis-telemetry").start()
    push({"type": "online", "model": api.STATE["model"], "tools": len(REGISTRY),
          "security": describe_mode()})
    log.info("native bridge ready — %d tools, no network transport", len(REGISTRY))


def stop() -> None:
    global _ALIVE
    _ALIVE = False
    bridge.detach(_emit)


class HostAPI:
    """Exposed to the page as `window.pywebview.api`.

    Keep this class free of instance attributes — see the module docstring.
    Method names are the wire protocol; the JS side mirrors them exactly.
    """

    # ── request/response (replaces GET/POST) ────────────────────────
    def boot(self) -> dict:
        return api.boot_payload()

    def history(self) -> dict:
        return api.history_payload()

    def get_settings(self) -> dict:
        return api.settings_payload()

    def save_settings(self, patch: dict) -> dict:
        return _await(api.apply_settings(patch or {}, notify=push))

    def test_key(self, key: str = "") -> dict:
        return _await(api.test_key(key), timeout=90)

    def voices(self) -> dict:
        return _await(api.voices_payload(), timeout=60)

    def face(self) -> dict:
        """Face enrolment status for the Settings panel."""
        try:
            from .faceid import status
            return status()
        except Exception as exc:
            return {"enrolled": False, "models_downloaded": False, "error": str(exc)}

    def enrol_face(self, name: str = "", samples: int = 5) -> dict:
        return _await(api.enrol_face(name, samples), timeout=180)

    def forget_face(self) -> dict:
        try:
            from .faceid import forget
            return {"ok": forget()}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def tts(self, text: str) -> dict:
        """Audio comes back base64-encoded; the page turns it into a blob."""
        try:
            audio, mime = _await(api.tts_bytes(text), timeout=120)
        except VoiceError as exc:
            return {"ok": False, "error": str(exc)}
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        return {"ok": True, "mime": mime,
                "b64": base64.b64encode(audio).decode()}

    # ── fire-and-forget (replaces WebSocket client->server) ─────────
    def send(self, message: dict) -> dict:
        kind = (message or {}).get("type")

        if kind == "user_message":
            text = (message.get("text") or "").strip()
            if text and _BRAIN:
                _submit(_BRAIN.handle(text))

        elif kind == "confirm_response":
            if _BRAIN:
                _BRAIN.resolve_confirmation(
                    message.get("id", ""), bool(message.get("approved")),
                    bool(message.get("remember")),
                )

        elif kind == "interrupt":
            if _BRAIN:
                _BRAIN.interrupt()

        elif kind == "set_mode":
            try:
                cfg.mode = str(message.get("mode", "guarded"))
                push({"type": "mode_changed", "mode": cfg.mode,
                      "security": describe_mode()})
            except ValueError as exc:
                push({"type": "error", "message": str(exc)})

        elif kind == "clear_history":
            n = memory.clear_conversation()
            push({"type": "notice", "message": f"Conversation log cleared ({n} entries)."})

        elif kind == "client_response":
            bridge.resolve(message.get("id", ""), message.get("data"),
                           message.get("error"))

        elif kind == "ping":
            push({"type": "pong", "t": time.time()})

        return {"ok": True}

    # ── window controls, folded in so there is one bridge object ────
    def window_minimize(self) -> None:
        from .native import window_minimize
        window_minimize()

    def window_toggle_maximize(self) -> bool:
        from .native import window_toggle_maximize
        return window_toggle_maximize()

    def window_close(self) -> None:
        from .native import window_close
        window_close()
