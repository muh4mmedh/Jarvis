"""The agent loop.

One user message in; a stream of text, tool executions and approval requests out.
Loops model -> tools -> model until the model stops asking for tools or hits the
step ceiling.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any, Awaitable, Callable

from .bridge import bridge
from .config import cfg
from .gemini import GeminiError, gemini
from .memory import memory
from .permissions import ALLOW, CONFIRM, DENY, check
from .persona import system_prompt
from .registry import REGISTRY, declarations, invoke

log = logging.getLogger("jarvis.brain")

Emit = Callable[[dict], Awaitable[None]]


class Interrupted(Exception):
    pass


def _summarise(value: Any, limit: int = 320) -> str:
    """Short human line for the HUD activity log."""
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, default=str)
        except Exception:
            text = str(value)
    text = " ".join(text.split())
    return text[:limit] + ("…" if len(text) > limit else "")


def _truncate(value: Any) -> Any:
    """Keep a tool result from blowing the context window."""
    cap = int(cfg.get("security.max_tool_output", 24000))
    if isinstance(value, str) and len(value) > cap:
        return value[:cap] + f"\n\n[... truncated, {len(value) - cap} more characters]"
    if isinstance(value, (dict, list)):
        blob = json.dumps(value, default=str)
        if len(blob) > cap:
            return {"truncated": True, "preview": blob[:cap]}
    return value


class Brain:
    def __init__(self, emit: Emit) -> None:
        self.emit = emit
        self._pending: dict[str, asyncio.Future] = {}
        self._cancel = asyncio.Event()
        self.busy = False

    # ── interaction with the socket layer ───────────────────────────
    def resolve_confirmation(self, req_id: str, approved: bool, remember: bool = False) -> None:
        fut = self._pending.pop(req_id, None)
        if fut and not fut.done():
            fut.set_result({"approved": approved, "remember": remember})

    def interrupt(self) -> None:
        self._cancel.set()
        for fut in list(self._pending.values()):
            if not fut.done():
                fut.set_result({"approved": False, "remember": False})
        self._pending.clear()

    async def _face_check(self, tool_name: str) -> dict | None:
        """Try to confirm the user's face before asking them to approve something.

        Purely additive: a match lets the approval auto-complete, anything else
        falls through to the normal click-to-approve dialog. A camera failure
        must never block the user from authorising their own machine.
        """
        gate = str(cfg.get("security.face_approval", "off")).lower()
        if gate == "off":
            return None

        risk = REGISTRY[tool_name].risk if tool_name in REGISTRY else "dangerous"
        if gate == "dangerous" and risk != "dangerous":
            return None
        if gate not in ("dangerous", "all"):
            return None

        try:
            from . import faceid
        except ImportError:
            return None
        if not faceid.enrolled():
            return None

        await self.emit({"type": "face_check", "state": "scanning"})
        try:
            frame = await bridge.request("camera_frame", {"purpose": "identity check"},
                                         timeout=20)
            image = (frame or {}).get("image", "")
            if not image:
                raise RuntimeError("empty frame")
            result = await asyncio.to_thread(faceid.verify, image)
        except Exception as exc:
            log.info("face check unavailable (%s) — falling back to manual approval", exc)
            await self.emit({"type": "face_check", "state": "unavailable",
                             "detail": str(exc)})
            return None

        await self.emit({
            "type": "face_check",
            "state": "match" if result.get("match") else "no_match",
            "name": result.get("name", ""),
            "confidence": result.get("confidence", 0),
            "score": result.get("score", 0),
        })
        return result

    async def _ask_permission(self, tool_name: str, args: dict, reason: str) -> bool:
        req_id = uuid.uuid4().hex[:12]
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[req_id] = fut
        t = REGISTRY.get(tool_name)
        await self.emit({
            "type": "confirm_request",
            "id": req_id,
            "tool": tool_name,
            "risk": t.risk if t else "unknown",
            "category": t.category if t else "",
            "args": args,
            "reason": reason,
        })
        try:
            answer = await asyncio.wait_for(fut, timeout=300)
        except asyncio.TimeoutError:
            self._pending.pop(req_id, None)
            await self.emit({"type": "confirm_timeout", "id": req_id})
            return False
        if answer.get("remember"):
            # "allow everything for the rest of this session"
            cfg.mode = "open"
            await self.emit({"type": "mode_changed", "mode": "open"})
        return bool(answer.get("approved"))

    # ── the loop ────────────────────────────────────────────────────
    async def handle(self, user_text: str) -> None:
        # A new instruction supersedes whatever is running. Refusing it meant
        # you could not correct yourself or cut JARVIS off mid-answer — you had
        # to sit and wait for something you had already changed your mind about.
        if self.busy:
            self.interrupt()
            for _ in range(60):                 # let the running turn unwind
                if not self.busy:
                    break
                await asyncio.sleep(0.05)
            self._cancel.clear()

        self.busy = True
        self._cancel.clear()
        started = time.time()
        memory.log_turn("user", user_text)

        contents: list[dict] = []
        for turn in memory.recent_turns()[:-1]:  # the message we just logged is added below
            role = "model" if turn["role"] == "assistant" else "user"
            contents.append({"role": role, "parts": [{"text": turn["content"]}]})

        # Surface anything relevant we've been told before.
        if recalled := memory.search_facts(user_text):
            note = "Relevant to this request, from memory:\n" + "\n".join(
                f"- {r['topic']}: {r['fact']}" for r in recalled
            )
            contents.append({"role": "user", "parts": [{"text": note}]})
            contents.append({"role": "model", "parts": [{"text": "Noted."}]})

        contents.append({"role": "user", "parts": [{"text": user_text}]})

        sys_prompt = system_prompt()
        tools = declarations()
        max_steps = int(cfg.get("model.max_tool_steps", 12))
        spoken = ""

        try:
            for step in range(max_steps):
                if self._cancel.is_set():
                    raise Interrupted()

                await self.emit({"type": "status", "state": "thinking", "step": step + 1})

                result = None
                opened = False
                async for event in gemini.stream(contents, sys_prompt, tools):
                    if self._cancel.is_set():
                        raise Interrupted()
                    if event["type"] == "text":
                        if not opened:
                            await self.emit({"type": "message_start"})
                            opened = True
                        await self.emit({"type": "delta", "text": event["delta"]})
                    else:
                        result = event["result"]

                if result is None:
                    raise GeminiError("empty response from model")

                if result.text:
                    spoken += ("\n\n" if spoken else "") + result.text
                if opened:
                    await self.emit({"type": "message_end", "usage": result.usage})

                if not result.calls:
                    break  # model is finished

                # Echo the model turn back verbatim — preserves thought signatures.
                contents.append({"role": "model", "parts": result.parts})

                response_parts = []
                for call in result.calls:
                    outcome = await self._run_tool(call["name"], call["args"])
                    response_parts.append({
                        "functionResponse": {"name": call["name"], "response": outcome}
                    })
                contents.append({"role": "user", "parts": response_parts})
            else:
                await self.emit({
                    "type": "notice",
                    "message": f"Stopped after {max_steps} tool steps — the safety ceiling.",
                })

            if spoken:
                memory.log_turn("assistant", spoken)
                await self.emit({"type": "speak", "text": spoken})

            await self.emit({
                "type": "status", "state": "idle",
                "elapsed": round(time.time() - started, 2),
            })

        except Interrupted:
            await self.emit({"type": "notice", "message": "Stopped, Sir."})
            await self.emit({"type": "status", "state": "idle"})
        except GeminiError as exc:
            await self.emit({"type": "error", "message": str(exc)})
            await self.emit({"type": "status", "state": "idle"})
        except Exception as exc:
            log.exception("brain failure")
            await self.emit({"type": "error", "message": f"Internal fault: {exc}"})
            await self.emit({"type": "status", "state": "idle"})
        finally:
            self.busy = False

    async def _run_tool(self, name: str, args: dict) -> dict:
        """Permission-check, execute, report. Always returns a dict for the model."""
        verdict = check(name, args)

        await self.emit({
            "type": "tool_start", "tool": name, "args": args,
            "risk": REGISTRY[name].risk if name in REGISTRY else "unknown",
            "verdict": verdict.action,
        })

        if verdict.action == DENY:
            msg = f"Refused by the permission layer: {verdict.reason}"
            await self.emit({"type": "tool_end", "tool": name, "ok": False, "summary": msg})
            return {"error": msg, "denied": True}

        if verdict.action == CONFIRM:
            await self.emit({"type": "status", "state": "awaiting_approval"})

            face = await self._face_check(name)
            if face and face.get("match"):
                who = face.get("name") or "you"
                await self.emit({
                    "type": "tool_end", "tool": name, "ok": True,
                    "summary": f"Identity confirmed ({who}, "
                               f"{face.get('confidence', 0)}%) — approved without prompting.",
                    "ms": 0,
                })
                await self.emit({"type": "status", "state": "executing"})
                approved = True
            else:
                approved = await self._ask_permission(name, args, verdict.reason)
            if not approved:
                msg = "Declined by the user."
                await self.emit({"type": "tool_end", "tool": name, "ok": False, "summary": msg})
                return {"error": msg, "denied": True}
            await self.emit({"type": "status", "state": "executing"})

        t0 = time.time()
        try:
            raw = await invoke(name, args)
            payload = _truncate(raw)
            result = payload if isinstance(payload, dict) else {"result": payload}
            await self.emit({
                "type": "tool_end", "tool": name, "ok": True,
                "ms": int((time.time() - t0) * 1000),
                "summary": _summarise(raw),
            })
            return result
        except Exception as exc:
            log.exception("tool %s failed", name)
            msg = f"{type(exc).__name__}: {exc}"
            await self.emit({
                "type": "tool_end", "tool": name, "ok": False,
                "ms": int((time.time() - t0) * 1000), "summary": msg,
            })
            return {"error": msg}
