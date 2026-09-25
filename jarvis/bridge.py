"""A channel from server-side tools back to the connected HUD.

Some capabilities need something only the browser can provide — a webcam
frame, for instance. Rather than adding an OS-level camera dependency (and
taking the picture with no visible prompt), the tool asks the HUD, the browser
shows its own permission dialog, and the frame comes back over the socket.

Single-user, local app: one attached client at a time is all this needs.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any, Awaitable, Callable

log = logging.getLogger("jarvis.bridge")

Emit = Callable[[dict], Awaitable[None]]


class NoClient(RuntimeError):
    """Raised when a tool needs the HUD but nothing is connected."""


class ClientBridge:
    def __init__(self) -> None:
        self._emit: Emit | None = None
        self._pending: dict[str, asyncio.Future] = {}

    def attach(self, emit: Emit) -> None:
        self._emit = emit

    def detach(self, emit: Emit) -> None:
        # Only clear if this is still the live connection — a reconnect may
        # already have installed a newer one.
        if self._emit is emit:
            self._emit = None
        for fut in list(self._pending.values()):
            if not fut.done():
                fut.set_exception(NoClient("The HUD disconnected mid-request."))
        self._pending.clear()

    @property
    def connected(self) -> bool:
        return self._emit is not None

    async def request(self, kind: str, payload: dict | None = None,
                      timeout: float = 45.0) -> Any:
        """Ask the HUD for something and wait for its reply."""
        if self._emit is None:
            raise NoClient(
                "No HUD is connected, so I cannot reach your camera or microphone."
            )
        req_id = uuid.uuid4().hex[:12]
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[req_id] = fut
        await self._emit({"type": "client_request", "id": req_id,
                          "kind": kind, **(payload or {})})
        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            raise TimeoutError(f"The HUD did not answer the {kind} request in time.")
        finally:
            self._pending.pop(req_id, None)

    def resolve(self, req_id: str, data: Any, error: str | None = None) -> None:
        fut = self._pending.pop(req_id, None)
        if fut is None or fut.done():
            return
        if error:
            fut.set_exception(RuntimeError(error))
        else:
            fut.set_result(data)


bridge = ClientBridge()
