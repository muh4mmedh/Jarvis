"""Window geometry memory.

Remembers where and how big the window was, and whether it was maximised, so
reopening puts it back exactly where you left it.

Saved geometry is validated against the monitors actually attached before it is
used: unplugging a second screen, or a resolution change, would otherwise
restore the window to coordinates that no longer exist and it would open
invisibly off the edge of the desktop.
"""
from __future__ import annotations

import logging
import threading

from .config import cfg

log = logging.getLogger("jarvis.geometry")

DEFAULT_W, DEFAULT_H = 1560, 940
MIN_W, MIN_H = 1040, 700

# Writes are debounced: dragging a window fires a move event per pixel and
# there is no sense touching the disk for each one.
_SAVE_DELAY = 1.2
_timer: threading.Timer | None = None
_lock = threading.Lock()

_state: dict = {"x": None, "y": None, "width": DEFAULT_W, "height": DEFAULT_H,
                "maximised": False}


def _scale() -> float:
    """Display scale factor, e.g. 2.0 at 200%."""
    try:
        import ctypes

        hdc = ctypes.windll.user32.GetDC(0)
        try:
            LOGPIXELSX = 88
            dpi = ctypes.windll.gdi32.GetDeviceCaps(hdc, LOGPIXELSX)
        finally:
            ctypes.windll.user32.ReleaseDC(0, hdc)
        if dpi:
            return max(1.0, dpi / 96.0)
    except Exception:
        pass
    return 1.0


def _virtual_screen() -> tuple[int, int, int, int]:
    """Desktop bounds in LOGICAL pixels: (left, top, right, bottom).

    GetSystemMetrics reports physical pixels once the process is DPI-aware,
    but the window manager places and sizes windows in logical ones. Mixing
    the two made the bounds look twice as large as they are on a 200% display,
    so a position saved on a big monitor was judged visible on a small one.
    """
    try:
        import ctypes

        user32 = ctypes.windll.user32
        SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN = 76, 77
        SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 78, 79
        scale = _scale()
        left = int(user32.GetSystemMetrics(SM_XVIRTUALSCREEN) / scale)
        top = int(user32.GetSystemMetrics(SM_YVIRTUALSCREEN) / scale)
        width = int(user32.GetSystemMetrics(SM_CXVIRTUALSCREEN) / scale)
        height = int(user32.GetSystemMetrics(SM_CYVIRTUALSCREEN) / scale)
        if width > 0 and height > 0:
            return left, top, left + width, top + height
    except Exception:
        pass
    return 0, 0, 1920, 1080


def load() -> dict:
    """Geometry to open with, clamped to somewhere actually visible."""
    saved = cfg.get("ui.window_geometry") or {}
    if not isinstance(saved, dict):
        saved = {}

    left, top, right, bottom = _virtual_screen()
    screen_w, screen_h = right - left, bottom - top

    # On a first run, open a little inside the screen instead of larger than
    # it — an oversized window is silently maximised by Windows, which is not
    # what someone who has never touched the window would expect.
    default_w = min(DEFAULT_W, max(MIN_W, screen_w - 120))
    default_h = min(DEFAULT_H, max(MIN_H, screen_h - 120))

    width = int(saved.get("width") or default_w)
    height = int(saved.get("height") or default_h)

    width = max(MIN_W, min(width, screen_w))
    height = max(MIN_H, min(height, screen_h))

    x, y = saved.get("x"), saved.get("y")
    if x is None or y is None:
        x = y = None                       # let the platform centre it
    else:
        x, y = int(x), int(y)
        # Insist that a decent slice of the window is on a real monitor.
        visible = (min(x + width, right) - max(x, left)) > 220 and \
                  (min(y + height, bottom) - max(y, top)) > 120
        if not visible:
            log.info("saved window position is off-screen — centring instead")
            x = y = None

    _state.update({"x": x, "y": y, "width": width, "height": height,
                   "maximised": bool(saved.get("maximised"))})
    return dict(_state)


def _flush() -> None:
    with _lock:
        payload = {k: _state[k] for k in ("x", "y", "width", "height", "maximised")}
    try:
        cfg.update({"ui.window_geometry": payload})
    except Exception as exc:
        log.debug("could not persist window geometry: %s", exc)


def _schedule() -> None:
    global _timer
    with _lock:
        if _timer is not None:
            _timer.cancel()
        _timer = threading.Timer(_SAVE_DELAY, _flush)
        _timer.daemon = True
        _timer.start()


def on_resized(width: int, height: int) -> None:
    # While maximised the reported size is the screen, not the size to restore
    # to, so it must not overwrite the remembered one.
    with _lock:
        if _state["maximised"]:
            return
        _state["width"], _state["height"] = int(width), int(height)
    _schedule()


def on_moved(x: int, y: int) -> None:
    with _lock:
        if _state["maximised"]:
            return
        _state["x"], _state["y"] = int(x), int(y)
    _schedule()


def set_maximised(value: bool) -> None:
    with _lock:
        _state["maximised"] = bool(value)
    _schedule()


def maximised() -> bool:
    return bool(_state["maximised"])


def save_now(window=None) -> None:
    """Flush on close, when the debounce timer may not have fired yet.

    Deliberately does not re-read the window. The move and resize events
    already keep the state current, and a frameless window reports a slightly
    smaller size than it was given — feeding that back in shaved a few pixels
    off on every single launch until the window had shrunk noticeably.
    """
    global _timer
    with _lock:
        if _timer is not None:
            _timer.cancel()
            _timer = None
    _flush()
