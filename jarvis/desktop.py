"""Desktop app shell.

Runs the local server in-process and opens the HUD in a real application window
with no address bar, tabs or browser chrome. Nothing is hosted remotely; the
server binds to localhost and dies with the window.

Window strategy, in order of preference:
  1. Edge / Chrome in --app mode with a private profile   (default)
     Keeps the Web Speech API working, which is what voice control needs.
  2. pywebview, if installed                              (window = "webview")
     Native frame, but no speech recognition in WebView2.
  3. The default browser                                  (window = "browser")
"""
from __future__ import annotations

import os
import platform
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

from .config import APP_DIR, cfg

IS_WIN = platform.system() == "Windows"
PROFILE_DIR = APP_DIR / "data" / "window-profile"


def _server_thread() -> threading.Thread:
    import uvicorn

    from .server import app          # imported eagerly so PyInstaller sees it

    config = uvicorn.Config(
        app, host=cfg.host, port=cfg.port,
        log_level="warning", access_log=False,
    )
    server = uvicorn.Server(config)
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    t.server = server  # type: ignore[attr-defined]
    return t


def _wait_for_port(host: str, port: int, timeout: float = 25.0) -> bool:
    deadline = time.time() + timeout
    probe_host = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    while time.time() < deadline:
        try:
            with socket.create_connection((probe_host, port), timeout=0.6):
                return True
        except OSError:
            time.sleep(0.15)
    return False


def _find_browser() -> str | None:
    """Locate a Chromium browser that supports --app mode."""
    for name in ("msedge", "chrome", "brave", "chromium"):
        if found := shutil.which(name):
            return found
    if not IS_WIN:
        for name in ("google-chrome", "chromium-browser", "microsoft-edge"):
            if found := shutil.which(name):
                return found
        mac = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        return mac if Path(mac).exists() else None

    pf = os.environ.get("ProgramFiles", r"C:\Program Files")
    pf86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    local = os.environ.get("LOCALAPPDATA", "")
    for candidate in (
        rf"{pf86}\Microsoft\Edge\Application\msedge.exe",
        rf"{pf}\Microsoft\Edge\Application\msedge.exe",
        rf"{pf}\Google\Chrome\Application\chrome.exe",
        rf"{pf86}\Google\Chrome\Application\chrome.exe",
        rf"{local}\Google\Chrome\Application\chrome.exe",
        rf"{pf}\BraveSoftware\Brave-Browser\Application\brave.exe",
    ):
        if Path(candidate).exists():
            return candidate
    return None


def _open_app_window(url: str) -> subprocess.Popen | None:
    browser = _find_browser()
    if not browser:
        return None
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    argv = [
        browser,
        f"--app={url}",
        f"--user-data-dir={PROFILE_DIR}",   # own profile, so the process tracks the window
        "--window-size=1560,940",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-features=Translate,MediaRouter",
        "--autoplay-policy=no-user-gesture-required",
    ]
    try:
        return subprocess.Popen(argv)
    except Exception:
        return None


def _open_webview(url: str) -> bool:
    try:
        import webview  # type: ignore
    except ImportError:
        return False
    window = webview.create_window(
        "J.A.R.V.I.S.", url, width=1560, height=940,
        background_color="#03070d", min_size=(1000, 680),
    )
    webview.start()
    return True


def launch(window: str | None = None) -> None:
    mode = (window or cfg.get("server.window", "app")).lower()
    url = f"http://{'127.0.0.1' if cfg.host in ('0.0.0.0', '::') else cfg.host}:{cfg.port}"

    print(f"""
    ╔══════════════════════════════════════════════════════════╗
    ║   J . A . R . V . I . S .                                ║
    ║   Just A Rather Very Intelligent System                  ║
    ╠══════════════════════════════════════════════════════════╣
    ║   Running locally      {url:<34}║
    ║   Authority            {cfg.mode:<34}║
    ║   API key              {'configured' if cfg.api_key else 'not set — add it in Settings':<34}║
    ╚══════════════════════════════════════════════════════════╝

    Close the window to shut down.
    """)

    thread = _server_thread()
    if not _wait_for_port(cfg.host, cfg.port):
        print("  Server failed to start. Run  python -m jarvis doctor  to diagnose.")
        sys.exit(1)

    if mode == "webview" and _open_webview(url):
        return

    if mode in ("app", "webview"):
        proc = _open_app_window(url)
        if proc:
            try:
                proc.wait()          # blocks until the app window is closed
            except KeyboardInterrupt:
                proc.terminate()
            return
        print("  No Chromium browser found — falling back to your default browser.")

    import webbrowser
    webbrowser.open(url)
    print("  Press Ctrl+C to shut down.")
    try:
        while thread.is_alive():
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
