"""The gate every tool call passes through before it touches your machine.

Three verdicts: ALLOW (run it), CONFIRM (ask the user, then run), DENY (never).
The blocklist is checked in *every* mode including `open` — those are the
commands there is no coming back from.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .config import cfg
from .registry import DANGEROUS, SAFE, SENSITIVE, REGISTRY

ALLOW, CONFIRM, DENY = "allow", "confirm", "deny"


@dataclass
class Verdict:
    action: str
    reason: str = ""

    @property
    def blocked(self) -> bool:
        return self.action == DENY


def _blocklist() -> list[re.Pattern]:
    pats = cfg.get("security.shell_blocklist", []) or []
    out = []
    for p in pats:
        try:
            out.append(re.compile(p, re.IGNORECASE))
        except re.error:
            continue
    return out


def scan_command(command: str) -> str | None:
    """Return the blocklist pattern a command trips, or None if it's clean."""
    for pat in _blocklist():
        if pat.search(command or ""):
            return pat.pattern
    return None


def is_protected(path: str | Path) -> bool:
    """True if `path` sits inside a directory the user marked off-limits."""
    try:
        target = Path(path).expanduser().resolve()
    except (OSError, ValueError):
        return True  # unresolvable -> treat as hostile
    for prot in cfg.get("security.protected_paths", []) or []:
        try:
            root = Path(prot).resolve()
        except (OSError, ValueError):
            continue
        if target == root or root in target.parents:
            return True
    return False


# Arguments that carry a filesystem destination, by tool name.
_PATH_ARGS = ("path", "dest", "destination", "target", "file", "filepath", "directory")


def check(tool_name: str, args: dict) -> Verdict:
    tool = REGISTRY.get(tool_name)
    if tool is None:
        return Verdict(DENY, f"unknown tool '{tool_name}'")

    mode = cfg.mode
    args = args or {}

    # 1. Hard blocklist — applies in every mode, no override.
    for key in ("command", "code", "script"):
        val = args.get(key)
        if isinstance(val, str):
            hit = scan_command(val)
            if hit:
                return Verdict(DENY, f"matches protected pattern /{hit}/ — refused in all modes")

    # 2. Protected filesystem roots, for anything that isn't purely observational.
    if tool.risk != SAFE:
        for key in _PATH_ARGS:
            val = args.get(key)
            if isinstance(val, str) and val.strip() and is_protected(val):
                return Verdict(DENY, f"'{val}' is inside a protected system directory")

    # 3. Mode policy.
    if mode == "readonly":
        if tool.risk == SAFE:
            return Verdict(ALLOW)
        return Verdict(DENY, "read-only mode is engaged — no changes permitted")

    if mode == "open":
        return Verdict(ALLOW)

    # guarded (default)
    if tool.risk == SAFE:
        return Verdict(ALLOW)
    return Verdict(CONFIRM, tool.confirm_hint or f"{tool.risk} operation requires your authorisation")


def describe_mode() -> dict:
    m = cfg.mode
    return {
        "mode": m,
        "label": {
            "readonly": "OBSERVE ONLY",
            "guarded": "GUARDED",
            "open": "FULL AUTONOMY",
        }.get(m, m.upper()),
        "detail": {
            "readonly": "I may look, but I may not touch.",
            "guarded": "I will ask before altering anything.",
            "open": "I will act without asking. The blocklist still stands.",
        }.get(m, ""),
    }
