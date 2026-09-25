"""Applications and windows — launching, focusing, arranging."""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
import webbrowser
from pathlib import Path

from ..registry import SAFE, SENSITIVE, tool

IS_WIN = platform.system() == "Windows"

# Friendly name -> what to actually launch. Extend freely.
KNOWN_APPS = {
    "notepad": "notepad.exe", "calculator": "calc.exe", "calc": "calc.exe",
    "paint": "mspaint.exe", "explorer": "explorer.exe", "file explorer": "explorer.exe",
    "task manager": "taskmgr.exe", "cmd": "cmd.exe", "command prompt": "cmd.exe",
    "powershell": "powershell.exe", "terminal": "wt.exe", "windows terminal": "wt.exe",
    "settings": "ms-settings:", "control panel": "control.exe",
    "chrome": "chrome.exe", "google chrome": "chrome.exe",
    "edge": "msedge.exe", "microsoft edge": "msedge.exe",
    "firefox": "firefox.exe", "brave": "brave.exe",
    "vscode": "code", "vs code": "code", "visual studio code": "code",
    "spotify": "spotify.exe", "discord": "discord.exe", "steam": "steam.exe",
    "word": "winword.exe", "excel": "excel.exe", "powerpoint": "powerpnt.exe",
    "outlook": "outlook.exe", "snipping tool": "snippingtool.exe",
}


@tool(
    description=(
        "Launch an application by name. Understands common names like 'chrome', "
        "'notepad', 'spotify', 'vs code', or a full path to an executable. "
        "Use whenever asked to open, start or run a program."
    ),
    parameters={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "App name or full path to the executable."},
            "arguments": {"type": "string", "description": "Optional command-line arguments, e.g. a file to open."},
        },
        "required": ["name"],
    },
    category="apps",
    risk=SENSITIVE,
    confirm_hint="Starts a program on your machine.",
)
def open_application(name: str, arguments: str = "") -> dict:
    raw = (name or "").strip().strip('"')
    target = KNOWN_APPS.get(raw.lower(), raw)

    # A path to a real file or folder? Let the shell decide how to open it.
    p = Path(os.path.expandvars(raw)).expanduser()
    if p.exists() and not p.suffix.lower() == ".exe":
        try:
            os.startfile(str(p))  # type: ignore[attr-defined]
            return {"ok": True, "opened": str(p), "via": "shell association"}
        except Exception as exc:
            return {"error": f"{type(exc).__name__}: {exc}"}

    argv = [target] + ([arguments] if arguments else [])
    try:
        if target.endswith(":") or target.startswith("ms-"):
            os.startfile(target)  # type: ignore[attr-defined]
            return {"ok": True, "launched": target, "via": "protocol handler"}
        subprocess.Popen(
            argv, shell=False,
            creationflags=(subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP) if IS_WIN else 0,
        )
        return {"ok": True, "launched": target, "arguments": arguments,
                "resolved_from": raw if raw.lower() in KNOWN_APPS else None}
    except FileNotFoundError:
        # Fall back to the shell's own resolution (handles Store apps, PATHless installs)
        if IS_WIN:
            r = subprocess.run(["cmd", "/c", "start", "", target] + ([arguments] if arguments else []),
                               capture_output=True, text=True, timeout=15)
            if r.returncode == 0:
                return {"ok": True, "launched": target, "via": "shell start"}
            return {"error": f"Could not find '{raw}'. Give me the full path to the executable."}
        return {"error": f"Could not find '{raw}'."}
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


@tool(
    description=(
        "Open a URL in the default web browser. Use for 'pull up X', 'open youtube', "
        "'search the web for X in my browser'."
    ),
    parameters={
        "type": "object",
        "properties": {"url": {"type": "string", "description": "Full URL or a bare domain."}},
        "required": ["url"],
    },
    category="apps",
    risk=SENSITIVE,
    confirm_hint="Opens a page in your default browser.",
)
def open_url(url: str) -> dict:
    u = (url or "").strip()
    if not u:
        return {"error": "No URL given."}
    if not u.startswith(("http://", "https://", "file://")):
        u = "https://" + u.lstrip("/")
    webbrowser.open(u)
    return {"ok": True, "opened": u}


@tool(
    description=(
        "List every open application window with its title. Use to see what the user "
        "currently has open, or to find the exact title of a window before focusing it."
    ),
    category="apps",
    risk=SAFE,
)
def list_windows() -> dict:
    try:
        import pygetwindow as gw
    except ImportError:
        return {"error": "Window management needs pygetwindow — run: pip install pygetwindow"}
    windows = []
    for w in gw.getAllWindows():
        title = (w.title or "").strip()
        if not title or w.width < 50:
            continue
        windows.append({
            "title": title,
            "size": f"{w.width}x{w.height}",
            "position": f"{w.left},{w.top}",
            "minimised": bool(w.isMinimized),
            "active": bool(w.isActive),
        })
    return {"count": len(windows), "windows": windows[:60]}


@tool(
    description=(
        "Bring a window to the front and focus it, matching on part of its title. "
        "Can also minimise, maximise or close it."
    ),
    parameters={
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "Any distinctive part of the window title."},
            "action": {"type": "string", "enum": ["focus", "minimise", "maximise", "close"],
                       "description": "Default focus."},
        },
        "required": ["title"],
    },
    category="apps",
    risk=SENSITIVE,
    confirm_hint="Manipulates an application window.",
)
def control_window(title: str, action: str = "focus") -> dict:
    try:
        import pygetwindow as gw
    except ImportError:
        return {"error": "Window management needs pygetwindow — run: pip install pygetwindow"}

    needle = (title or "").lower()
    matches = [w for w in gw.getAllWindows() if needle in (w.title or "").lower() and w.title.strip()]
    if not matches:
        return {"error": f"No open window matching {title!r}.",
                "hint": "Call list_windows to see what's actually open."}
    w = matches[0]
    try:
        if action == "close":
            w.close()
        elif action == "minimise":
            w.minimize()
        elif action == "maximise":
            w.maximize()
        else:
            if w.isMinimized:
                w.restore()
            w.activate()
        return {"ok": True, "window": w.title, "action": action,
                "other_matches": [m.title for m in matches[1:5]]}
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}", "window": w.title}


@tool(
    description=(
        "Open a folder in File Explorer, optionally selecting a specific file inside it. "
        "Use for 'show me that file', 'open the downloads folder'."
    ),
    parameters={
        "type": "object",
        "properties": {"path": {"type": "string", "description": "Folder or file path."}},
        "required": ["path"],
    },
    category="apps",
    risk=SENSITIVE,
    confirm_hint="Opens a File Explorer window.",
)
def reveal_in_explorer(path: str) -> dict:
    p = Path(os.path.expandvars(path)).expanduser()
    if not p.exists():
        return {"error": f"No such path: {p}"}
    if not IS_WIN:
        return {"error": "Explorer is Windows-only."}
    if p.is_file():
        subprocess.Popen(["explorer", "/select,", str(p)])
    else:
        subprocess.Popen(["explorer", str(p)])
    return {"ok": True, "revealed": str(p)}
