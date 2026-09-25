"""Direct machine control — keyboard, mouse, clipboard.

The most invasive tools in the set, and deliberately the ones with the loudest
confirmation prompts. A failsafe is armed: slamming the pointer into the
top-left corner aborts any pyautogui action in flight.
"""
from __future__ import annotations

import time

from ..registry import SAFE, SENSITIVE, tool


def _gui():
    try:
        import pyautogui
    except ImportError:
        raise RuntimeError("Input control needs pyautogui — run: pip install pyautogui")
    pyautogui.FAILSAFE = True       # pointer to top-left corner = emergency stop
    pyautogui.PAUSE = 0.05
    return pyautogui


@tool(
    description=(
        "Type text as if from the keyboard, into whatever window currently has focus. "
        "Focus the right window first. Use for filling forms, writing into an editor, "
        "or any app with no other automation route."
    ),
    parameters={
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "The text to type."},
            "press_enter": {"type": "boolean", "description": "Hit Enter afterwards."},
            "delay": {"type": "number", "description": "Seconds to wait before typing. Default 0.5."},
        },
        "required": ["text"],
    },
    category="control",
    risk=SENSITIVE,
    confirm_hint="Types directly into whichever window is focused right now.",
)
def type_text(text: str, press_enter: bool = False, delay: float = 0.5) -> dict:
    g = _gui()
    time.sleep(max(0.0, min(delay, 5.0)))
    g.typewrite(text, interval=0.012)
    if press_enter:
        g.press("enter")
    return {"ok": True, "characters": len(text), "entered": press_enter}


@tool(
    description=(
        "Press a key or a keyboard shortcut. Combine keys with '+', e.g. 'ctrl+s', "
        "'alt+tab', 'win+d', 'ctrl+shift+esc'. Use for shortcuts, media keys, navigation."
    ),
    parameters={
        "type": "object",
        "properties": {
            "keys": {"type": "string", "description": "Key or combination, e.g. 'ctrl+c' or 'f5'."},
            "repeat": {"type": "integer", "description": "How many times. Default 1."},
        },
        "required": ["keys"],
    },
    category="control",
    risk=SENSITIVE,
    confirm_hint="Sends a keystroke to the focused window.",
)
def press_keys(keys: str, repeat: int = 1) -> dict:
    g = _gui()
    combo = [k.strip().lower() for k in keys.replace(" ", "").split("+") if k.strip()]
    if not combo:
        return {"error": "No keys given."}
    n = max(1, min(repeat, 50))
    for _ in range(n):
        if len(combo) == 1:
            g.press(combo[0])
        else:
            g.hotkey(*combo)
        time.sleep(0.04)
    return {"ok": True, "keys": "+".join(combo), "times": n}


@tool(
    description=(
        "Move the mouse and click. Coordinates are absolute screen pixels — take a "
        "screenshot with see_screen first if you need to know where something is."
    ),
    parameters={
        "type": "object",
        "properties": {
            "x": {"type": "integer", "description": "Screen X in pixels."},
            "y": {"type": "integer", "description": "Screen Y in pixels."},
            "button": {"type": "string", "enum": ["left", "right", "middle"]},
            "clicks": {"type": "integer", "description": "1 for single, 2 for double-click."},
        },
        "required": ["x", "y"],
    },
    category="control",
    risk=SENSITIVE,
    confirm_hint="Moves your mouse and clicks at a specific point on screen.",
)
def click_at(x: int, y: int, button: str = "left", clicks: int = 1) -> dict:
    g = _gui()
    w, h = g.size()
    if not (0 <= x < w and 0 <= y < h):
        return {"error": f"({x},{y}) is off-screen. Display is {w}x{h}."}
    g.moveTo(x, y, duration=0.25)
    g.click(button=button, clicks=max(1, min(clicks, 3)))
    return {"ok": True, "at": [x, y], "button": button, "clicks": clicks, "screen": [w, h]}


@tool(
    description="Scroll the mouse wheel at the current pointer position, or at a given point.",
    parameters={
        "type": "object",
        "properties": {
            "amount": {"type": "integer", "description": "Positive scrolls up, negative down. ~3 per notch."},
            "x": {"type": "integer"}, "y": {"type": "integer"},
        },
        "required": ["amount"],
    },
    category="control",
    risk=SENSITIVE,
    confirm_hint="Scrolls the window under the pointer.",
)
def scroll(amount: int, x: int = -1, y: int = -1) -> dict:
    g = _gui()
    if x >= 0 and y >= 0:
        g.moveTo(x, y, duration=0.2)
    g.scroll(int(amount) * 40)
    return {"ok": True, "scrolled": amount}


@tool(
    description="Read whatever is currently on the clipboard.",
    category="control",
    risk=SAFE,
)
def read_clipboard() -> dict:
    try:
        import pyperclip
    except ImportError:
        return {"error": "Clipboard access needs pyperclip — run: pip install pyperclip"}
    content = pyperclip.paste() or ""
    return {"content": content, "length": len(content),
            "empty": not content.strip()}


@tool(
    description="Put text onto the clipboard, ready to paste.",
    parameters={
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    },
    category="control",
    risk=SENSITIVE,
    confirm_hint="Replaces the current clipboard contents.",
)
def write_clipboard(text: str) -> dict:
    try:
        import pyperclip
    except ImportError:
        return {"error": "Clipboard access needs pyperclip — run: pip install pyperclip"}
    pyperclip.copy(text)
    return {"ok": True, "copied_characters": len(text)}


@tool(
    description="Get the current mouse position and the screen dimensions.",
    category="control",
    risk=SAFE,
)
def pointer_info() -> dict:
    g = _gui()
    pos = g.position()
    size = g.size()
    return {"mouse": {"x": pos.x, "y": pos.y}, "screen": {"width": size.width, "height": size.height}}
