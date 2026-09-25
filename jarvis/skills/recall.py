"""Memory tools — what JARVIS chooses to keep, and self-inspection."""
from __future__ import annotations

from ..memory import memory
from ..registry import SAFE, SENSITIVE, catalogue, tool


@tool(
    description=(
        "Commit a fact to long-term memory. Use unprompted whenever the user tells you "
        "something durably true — their name, their setup, project details, preferences, "
        "recurring people or places. Facts persist across restarts and are shown to you "
        "on every request. Keep the topic short and specific; overwriting an existing "
        "topic updates it."
    ),
    parameters={
        "type": "object",
        "properties": {
            "topic": {"type": "string", "description": "Short key, e.g. 'name', 'main project', 'coffee order'."},
            "fact": {"type": "string", "description": "What to remember about that topic."},
        },
        "required": ["topic", "fact"],
    },
    category="memory",
    risk=SAFE,
)
def remember(topic: str, fact: str) -> dict:
    action = memory.remember(topic, fact)
    return {"ok": True, "action": action, "topic": topic.lower(), "fact": fact}


@tool(
    description="Search long-term memory for anything relevant to a query.",
    parameters={
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
    },
    category="memory",
    risk=SAFE,
)
def recall(query: str) -> dict:
    hits = memory.search_facts(query, limit=10)
    return {"query": query, "found": len(hits), "facts": hits}


@tool(
    description="List everything currently held in long-term memory.",
    category="memory",
    risk=SAFE,
)
def list_memory() -> dict:
    facts = memory.all_facts()
    return {"count": len(facts), "facts": facts, **memory.stats()}


@tool(
    description="Delete a fact from long-term memory by its topic.",
    parameters={
        "type": "object",
        "properties": {"topic": {"type": "string"}},
        "required": ["topic"],
    },
    category="memory",
    risk=SENSITIVE,
    confirm_hint="Permanently removes a stored fact.",
)
def forget(topic: str) -> dict:
    ok = memory.forget(topic)
    return {"ok": ok, "topic": topic,
            "message": "Forgotten." if ok else "No fact stored under that topic."}


@tool(
    description=(
        "List your own capabilities — every tool available to you, grouped by category. "
        "Use when the user asks what you can do, or when you're unsure whether a "
        "capability exists before saying you cannot do something."
    ),
    category="meta",
    risk=SAFE,
)
def list_capabilities() -> dict:
    tools = catalogue()
    grouped: dict[str, list] = {}
    for t in tools:
        grouped.setdefault(t["category"], []).append(
            {"name": t["name"], "risk": t["risk"], "does": t["description"]}
        )
    return {"total": len(tools), "by_category": grouped}
