"""HTTP transport — the browser fallback.

The native window does not use this: it talks to Python in-process and binds no
socket at all. This exists so the HUD can also be opened in a browser, which is
useful for debugging with devtools and for machines without WebView2.

Every endpoint is a thin wrapper over jarvis.api, which the native bridge also
calls, so the two transports cannot drift apart.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from contextlib import asynccontextmanager

from fastapi import Body, FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import api
from .brain import Brain
from .bridge import bridge
from .config import UI_DIR, cfg
from .gemini import gemini
from .memory import memory
from .permissions import describe_mode
from .registry import REGISTRY, load_all_skills
from .voice import VoiceError

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("jarvis")


@asynccontextmanager
async def lifespan(app: FastAPI):
    n_modules = load_all_skills()
    log.info("loaded %d skill modules, %d tools", n_modules, len(REGISTRY))

    if cfg.api_key:
        model = await api.resolve_startup_model()
        log.info("model: %s", model)
    else:
        log.warning("No API key configured — add one in Settings")

    log.info("HUD ready at http://%s:%s", cfg.host, cfg.port)
    yield
    await gemini.aclose()


app = FastAPI(title="J.A.R.V.I.S.", lifespan=lifespan)


# ── pages and read-only state ───────────────────────────────────────
@app.get("/")
async def index():
    return FileResponse(UI_DIR / "index.html")


@app.get("/api/boot")
async def boot():
    return JSONResponse(api.boot_payload())


@app.get("/api/history")
async def history():
    return JSONResponse(api.history_payload())


@app.get("/api/face")
async def face_state():
    try:
        from .faceid import status
        return JSONResponse(status())
    except Exception as exc:
        return JSONResponse({"enrolled": False, "models_downloaded": False,
                             "error": str(exc)})


# ── settings ────────────────────────────────────────────────────────
@app.get("/api/settings")
async def get_settings():
    return JSONResponse(api.settings_payload())


@app.post("/api/settings")
async def post_settings(patch: dict = Body(...)):
    return JSONResponse(await api.apply_settings(patch))


@app.post("/api/test-key")
async def test_key(payload: dict = Body(default={})):
    return JSONResponse(await api.test_key((payload or {}).get("key", "")))


@app.post("/api/enrol-face")
async def enrol_face(payload: dict = Body(default={})):
    return JSONResponse(await api.enrol_face(
        (payload or {}).get("name", ""), (payload or {}).get("samples", 5)))


@app.post("/api/forget-face")
async def forget_face():
    from .faceid import forget
    return JSONResponse({"ok": forget()})


# ── speech ──────────────────────────────────────────────────────────
@app.post("/api/tts")
async def tts(payload: dict = Body(...)):
    try:
        audio, mime = await api.tts_bytes((payload or {}).get("text", ""))
    except VoiceError as exc:
        return JSONResponse({"error": str(exc)}, status_code=503)
    return Response(content=audio, media_type=mime,
                    headers={"Cache-Control": "no-store"})


@app.get("/api/voices")
async def voices():
    return JSONResponse(await api.voices_payload())


# ── WebSocket ───────────────────────────────────────────────────────
@app.websocket("/ws")
async def socket(ws: WebSocket):
    await ws.accept()
    send_lock = asyncio.Lock()
    alive = True

    async def emit(payload: dict) -> None:
        nonlocal alive
        if not alive:
            return
        try:
            async with send_lock:
                await ws.send_text(json.dumps(payload, default=str))
        except (WebSocketDisconnect, RuntimeError):
            alive = False

    brain = Brain(emit)
    bridge.attach(emit)          # lets tools ask the HUD for camera frames

    async def telemetry_loop():
        interval = float(cfg.get("ui.telemetry_interval", 2.0))
        try:
            import psutil
            psutil.cpu_percent(interval=None)      # prime
        except ImportError:
            pass
        while alive:
            await emit({"type": "telemetry", "data": api.telemetry()})
            await asyncio.sleep(interval)

    pump = asyncio.create_task(telemetry_loop())
    await emit({"type": "online", "model": api.STATE["model"],
                "tools": len(REGISTRY), "security": describe_mode()})

    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            kind = msg.get("type")

            if kind == "user_message":
                text = (msg.get("text") or "").strip()
                if text:
                    asyncio.create_task(brain.handle(text))

            elif kind == "confirm_response":
                brain.resolve_confirmation(
                    msg.get("id", ""), bool(msg.get("approved")),
                    bool(msg.get("remember")),
                )

            elif kind == "interrupt":
                brain.interrupt()

            elif kind == "set_mode":
                try:
                    cfg.mode = str(msg.get("mode", "guarded"))
                    await emit({"type": "mode_changed", "mode": cfg.mode,
                                "security": describe_mode()})
                except ValueError as exc:
                    await emit({"type": "error", "message": str(exc)})

            elif kind == "clear_history":
                n = memory.clear_conversation()
                await emit({"type": "notice",
                            "message": f"Conversation log cleared ({n} entries)."})

            elif kind == "client_response":
                bridge.resolve(msg.get("id", ""), msg.get("data"), msg.get("error"))

            elif kind == "ping":
                await emit({"type": "pong", "t": time.time()})

    except WebSocketDisconnect:
        pass
    finally:
        alive = False
        bridge.detach(emit)
        pump.cancel()


# Mounted last so it does not shadow the API routes.
app.mount("/", StaticFiles(directory=UI_DIR, html=True), name="ui")
