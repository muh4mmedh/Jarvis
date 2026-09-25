"""Tool registry.

Every capability JARVIS has is a Python function wrapped in @tool. The decorator
records a JSON-schema description that gets handed to Gemini as a function
declaration, plus a risk tier the permission layer enforces.

Adding a new capability is one function + one decorator. Nothing else to wire.
"""
from __future__ import annotations

import asyncio
import inspect
import logging
from dataclasses import dataclass, field
from typing import Any, Callable

log = logging.getLogger("jarvis.registry")

# Risk tiers -------------------------------------------------------------
# SAFE      : observation only. Runs in every mode, never asks.
# SENSITIVE : writes files, changes system state, controls input. Asks in guarded.
# DANGEROUS : arbitrary code / shell execution. Asks in guarded, blocked in readonly.
SAFE, SENSITIVE, DANGEROUS = "safe", "sensitive", "dangerous"


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict
    fn: Callable
    risk: str = SAFE
    category: str = "misc"
    # Human-readable one-liner shown in the confirmation dialog.
    confirm_hint: str | None = None

    @property
    def is_async(self) -> bool:
        return inspect.iscoroutinefunction(self.fn)

    def declaration(self) -> dict:
        """Gemini function-declaration form."""
        params = self.parameters or {"type": "object", "properties": {}}
        return {
            "name": self.name,
            "description": self.description.strip(),
            "parameters": params,
        }


REGISTRY: dict[str, Tool] = {}


def tool(
    *,
    description: str,
    parameters: dict | None = None,
    risk: str = SAFE,
    category: str = "misc",
    name: str | None = None,
    confirm_hint: str | None = None,
):
    def deco(fn: Callable) -> Callable:
        tname = name or fn.__name__
        REGISTRY[tname] = Tool(
            name=tname,
            description=description,
            parameters=parameters or {"type": "object", "properties": {}},
            fn=fn,
            risk=risk,
            category=category,
            confirm_hint=confirm_hint,
        )
        return fn

    return deco


def declarations() -> list[dict]:
    return [t.declaration() for t in REGISTRY.values()]


def catalogue() -> list[dict]:
    """Capability grid for the HUD."""
    out: list[dict] = []
    for t in REGISTRY.values():
        out.append({
            "name": t.name,
            "category": t.category,
            "risk": t.risk,
            "description": t.description.strip().split("\n")[0][:120],
        })
    return sorted(out, key=lambda x: (x["category"], x["name"]))


async def invoke(name: str, args: dict) -> Any:
    """Run a tool. Sync tools are pushed to a worker thread so the event loop
    (and therefore the HUD's live telemetry) never stalls behind a slow call."""
    t = REGISTRY.get(name)
    if t is None:
        raise KeyError(f"No such tool: {name}")
    args = args or {}
    # Drop keys the function doesn't accept rather than exploding — models
    # occasionally hallucinate an extra argument and one bad key shouldn't
    # kill an otherwise valid call.
    sig = inspect.signature(t.fn)
    accepts_kwargs = any(p.kind is p.VAR_KEYWORD for p in sig.parameters.values())
    if not accepts_kwargs:
        allowed = set(sig.parameters)
        dropped = [k for k in args if k not in allowed]
        if dropped:
            log.warning("tool %s: dropping unexpected args %s", name, dropped)
        args = {k: v for k, v in args.items() if k in allowed}

    if t.is_async:
        return await t.fn(**args)
    return await asyncio.to_thread(t.fn, **args)


def load_all_skills() -> int:
    """Import every module in jarvis/skills so its @tool decorators fire."""
    import importlib
    import pkgutil

    from . import skills

    count = 0
    for mod in pkgutil.iter_modules(skills.__path__):
        if mod.name.startswith("_"):
            continue
        try:
            importlib.import_module(f"{skills.__name__}.{mod.name}")
            count += 1
        except Exception as exc:  # a broken optional dep must not kill boot
            log.error("skill module %r failed to load: %s", mod.name, exc)
    return count
