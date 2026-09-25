"""True native Windows application shell.

A frameless Win32 window hosting Edge WebView2 — its own HWND, its own taskbar
entry and icon, its own process, a tray icon.

There is no web server. The HUD is served to the window through a WebView2
virtual host mapped straight at the ui/ folder, and it talks to Python through
the in-process bridge rather than HTTP. Nothing binds a socket: no port, no
loopback origin, nothing for netstat to show.
"""
from __future__ import annotations

import logging
import sys
import threading
import time
from pathlib import Path

from . import geometry, hostapi
from .config import APP_DIR, DATA_DIR, RESOURCES, UI_DIR, cfg

log = logging.getLogger("jarvis.native")

MIN_W, MIN_H = 1040, 700
VIRTUAL_HOST = "jarvis.local"

_WINDOW = None
_MAXIMISED = False


def _icon_path() -> Path | None:
    for candidate in (RESOURCES / "ui" / "assets" / "jarvis.ico",
                      APP_DIR / "ui" / "assets" / "jarvis.ico"):
        if candidate.exists():
            return candidate
    return None


def _work_area() -> tuple[int, int]:
    try:
        import ctypes

        user32 = ctypes.windll.user32
        user32.SetProcessDPIAware()
        return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1) - 48
    except Exception:
        return 1560, 940


# ── window controls, called across the bridge ───────────────────────
def window_minimize() -> None:
    if _WINDOW:
        _WINDOW.minimize()


def window_toggle_maximize() -> bool:
    global _MAXIMISED
    if not _WINDOW:
        return False
    try:
        if _MAXIMISED:
            if hasattr(_WINDOW, "restore"):
                _WINDOW.restore()
            else:
                _WINDOW.resize(1560, 940)
        else:
            if hasattr(_WINDOW, "maximize"):
                _WINDOW.maximize()
            else:
                _WINDOW.resize(*_work_area())
        _MAXIMISED = not _MAXIMISED
        geometry.set_maximised(_MAXIMISED)
    except Exception as exc:
        log.warning("maximize toggle failed: %s", exc)
    return _MAXIMISED


def window_close() -> None:
    if _WINDOW:
        geometry.save_now(_WINDOW)
        _WINDOW.destroy()


# ── WebView2 configuration ──────────────────────────────────────────
def _configure_webview() -> bool:
    """Map the virtual host and strip the browser tells.

    Two things betray a web page to the user: the microphone/camera permission
    toast, and the browser context menu and shortcuts. This app *is* the thing
    asking for the microphone — there is no untrusted content here — so the
    permission is answered in code and no prompt appears.

    CoreWebView2 is COM and may only be touched from the UI thread, so the work
    is marshalled onto it with Control.Invoke.
    """
    if _WINDOW is None:
        return False

    done = threading.Event()
    ok = {"value": False}

    try:
        from System import Action  # type: ignore  (pythonnet)

        control = getattr(_WINDOW.native, "webview", None)
        if control is None:
            return False

        def setup():
            try:
                from Microsoft.Web.WebView2.Core import (  # type: ignore
                    CoreWebView2HostResourceAccessKind,
                    CoreWebView2PermissionKind,
                    CoreWebView2PermissionState,
                )

                core = control.CoreWebView2
                if core is None:
                    return

                # Serve the HUD from a virtual host instead of a local server.
                core.SetVirtualHostNameToFolderMapping(
                    VIRTUAL_HOST, str(UI_DIR),
                    CoreWebView2HostResourceAccessKind.Allow,
                )

                allowed = {
                    CoreWebView2PermissionKind.Microphone,
                    CoreWebView2PermissionKind.Camera,
                }

                def on_permission(_sender, args):
                    # Grant capture devices; refuse everything else
                    # (geolocation, notifications, clipboard reads...).
                    args.State = (CoreWebView2PermissionState.Allow
                                  if args.PermissionKind in allowed
                                  else CoreWebView2PermissionState.Deny)
                    args.Handled = True

                core.PermissionRequested += on_permission

                s = core.Settings
                s.AreDefaultContextMenusEnabled = False     # no Reload / View source
                s.AreBrowserAcceleratorKeysEnabled = False  # no Ctrl+R, F5, Ctrl+P
                s.IsStatusBarEnabled = False                # no link-hover URL strip
                s.IsZoomControlEnabled = False
                s.IsSwipeNavigationEnabled = False
                try:
                    s.IsGeneralAutofillEnabled = False
                    s.IsPasswordAutosaveEnabled = False
                except AttributeError:
                    pass                                    # older runtime
                ok["value"] = True
            except Exception as exc:
                log.warning("could not configure webview: %s", exc)
            finally:
                done.set()

        control.Invoke(Action(setup))
        done.wait(timeout=10)
    except Exception as exc:
        log.warning("webview configuration unavailable: %s", exc)
    return ok["value"]


def _start_tray() -> None:
    """A tray icon, because a real desktop application has one."""
    try:
        import pystray
        from PIL import Image
    except ImportError:
        return

    icon_file = _icon_path()
    if not icon_file:
        return

    def show(_icon=None, _item=None):
        if _WINDOW:
            _WINDOW.show()

    def quit_app(icon, _item=None):
        icon.stop()
        if _WINDOW:
            _WINDOW.destroy()

    try:
        image = Image.open(icon_file)
        menu = pystray.Menu(
            pystray.MenuItem("Show JARVIS", show, default=True),
            pystray.MenuItem("Quit", quit_app),
        )
        icon = pystray.Icon("jarvis", image, "J.A.R.V.I.S.", menu)
        threading.Thread(target=icon.run, daemon=True, name="jarvis-tray").start()
    except Exception as exc:
        log.warning("tray icon unavailable: %s", exc)


def launch() -> None:
    """Open the native window. Blocks until it is closed."""
    global _WINDOW, _MAXIMISED

    try:
        import webview
    except ImportError:
        print("\n  The native window needs pywebview:\n"
              "      pip install pywebview\n"
              "  Falling back to the browser app window.\n")
        from .desktop import launch as fallback
        fallback(window="app")
        return

    from .registry import load_all_skills
    load_all_skills()

    print(f"""
    ╔══════════════════════════════════════════════════════════╗
    ║   J . A . R . V . I . S .                                ║
    ║   Just A Rather Very Intelligent System                  ║
    ╠══════════════════════════════════════════════════════════╣
    ║   Window               native (WebView2)                 ║
    ║   Transport            in-process — no server, no port   ║
    ║   Authority            {cfg.mode:<34}║
    ║   API key              {'configured' if cfg.api_key else 'not set — add it in Settings':<34}║
    ╚══════════════════════════════════════════════════════════╝
    """)

    webview.settings["ALLOW_DOWNLOADS"] = False
    webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True

    # Start on a placeholder: the virtual host does not exist until
    # CoreWebView2 is alive, which only happens once the window is up.
    geo = geometry.load()
    _MAXIMISED = bool(geo["maximised"])

    _WINDOW = webview.create_window(
        "J.A.R.V.I.S.",
        html='<body style="margin:0;background:#03070d"></body>',
        width=geo["width"], height=geo["height"],
        x=geo["x"], y=geo["y"],
        min_size=(MIN_W, MIN_H),
        background_color="#03070d",
        frameless=True,
        easy_drag=False,               # the title bar handles dragging
        js_api=hostapi.HostAPI(),
        text_select=False,
        confirm_close=False,
    )

    storage = DATA_DIR / "webview"
    storage.mkdir(parents=True, exist_ok=True)

    # Remember where the window ends up, so next launch opens there.
    def _bind_geometry():
        ev = getattr(_WINDOW, "events", None)
        if ev is None:
            return
        for name, handler in (("resized", geometry.on_resized),
                              ("moved", geometry.on_moved),
                              ("maximized", lambda *_: geometry.set_maximised(True)),
                              ("restored", lambda *_: geometry.set_maximised(False)),
                              ("closing", lambda *_: geometry.save_now(_WINDOW))):
            event = getattr(ev, name, None)
            if event is None:
                continue
            try:
                # pywebview's Event subscribes through += and mutates in place.
                event += handler
            except Exception as exc:
                log.debug("could not bind window event %s: %s", name, exc)

    def _on_ready():
        time.sleep(1.0)                # let CoreWebView2 finish initialising
        if _configure_webview():
            log.info("virtual host %s -> %s", VIRTUAL_HOST, UI_DIR)
        else:
            log.warning("virtual host unavailable — the HUD may not load")

        hostapi.start(_WINDOW)
        _WINDOW.load_url(f"https://{VIRTUAL_HOST}/index.html?shell=native")
        _bind_geometry()

        # Restore a maximised session once the window is actually up.
        if _MAXIMISED:
            try:
                _WINDOW.maximize()
                log.info("restored maximised")
            except Exception as exc:
                log.debug("could not restore maximised state: %s", exc)

        _start_tray()

    try:
        webview.start(_on_ready, gui="edgechromium", private_mode=False,
                      storage_path=str(storage))
    finally:
        geometry.save_now(_WINDOW)
        hostapi.stop()
