"""J.A.R.V.I.S.

Usage:
  python -m jarvis              open the native desktop app (default)
  python -m jarvis native       force the native window
  python -m jarvis serve        run the server only, no window
  python -m jarvis browser      open in your default browser instead
  python -m jarvis models       list Gemini models available on your key
  python -m jarvis doctor       check the installation and dependencies
  python -m jarvis key <KEY>    store your Gemini API key
"""
from __future__ import annotations

import asyncio
import sys

from .config import cfg


def _serve_headless() -> None:
    import uvicorn

    from .server import app

    print(f"  JARVIS server on http://{cfg.host}:{cfg.port}  (no window)")
    uvicorn.run(app, host=cfg.host, port=cfg.port,
                log_level="warning", access_log=False)


async def _models() -> None:
    from .gemini import gemini

    if not cfg.api_key:
        print("  No API key set. Add one in Settings, or: python -m jarvis key YOUR_KEY")
        return
    print("  Querying models available on your key…\n")
    try:
        names = await gemini.list_models()
        chosen = await gemini.resolve_model()
    except Exception as exc:
        print(f"  Failed: {exc}")
        return
    for n in sorted(names):
        print(f"    {n}" + ("   <- would be selected" if n == chosen else ""))
    await gemini.aclose()


async def _doctor() -> None:
    from .registry import REGISTRY, load_all_skills

    print("\n  JARVIS self-check\n  " + "─" * 52)
    print(f"    API key            {'configured' if cfg.api_key else 'NOT SET — add it in Settings'}")
    print(f"    Authority mode     {cfg.mode}")
    from .config import DATA_DIR
    print(f"    Data directory     {DATA_DIR}")
    print(f"    Settings file      {'present' if (DATA_DIR / 'settings.json').exists() else 'not created yet'}")

    mods = load_all_skills()
    print(f"    Skill modules      {mods} loaded")
    print(f"    Tools registered   {len(REGISTRY)}")

    print("\n    Optional dependencies:")
    for mod, purpose in [
        ("psutil", "system telemetry"), ("mss", "fast screen capture"),
        ("PIL", "image handling"), ("pyautogui", "keyboard/mouse control"),
        ("pygetwindow", "window management"), ("pyperclip", "clipboard"),
        ("win32com", "recycle bin"), ("webview", "native window (optional)"),
    ]:
        try:
            __import__(mod)
            print(f"      [ok]      {mod:<13} {purpose}")
        except Exception as exc:
            # Report the reason — a packaged build can fail to import for
            # reasons other than the package simply being missing.
            print(f"      [absent]  {mod:<13} {purpose} — degraded ({exc})")

    from .voice import available as voice_available
    health = await voice_available()
    if health.get("neural"):
        print(f"\n    Neural voice       {health['using']}  ({health['count']} available)")
    else:
        print(f"\n    Neural voice       unavailable ({health.get('reason')}) "
              f"— will fall back to your system voices")

    try:
        import webview  # noqa: F401
        native = "pywebview + WebView2"
    except ImportError:
        native = "pywebview NOT installed — will fall back to a browser window"
    print(f"    Native window      {native}")

    from .desktop import _find_browser
    browser = _find_browser()
    print(f"\n    App window         {browser or 'no Chromium browser found — will use default browser'}")

    from .skills.claude_agent import _claude_bin
    claude = _claude_bin()
    print(f"    Claude Code CLI    {claude or 'not installed (npm i -g @anthropic-ai/claude-code)'}")

    if cfg.api_key:
        from .gemini import gemini
        try:
            print(f"    Model resolved     {await gemini.resolve_model()}")
        except Exception as exc:
            print(f"    Model resolution   FAILED: {exc}")
        await gemini.aclose()
    print("  " + "─" * 52 + "\n")


def _selftest() -> int:
    """Import everything and load every skill. Used by build.py to prove a
    packaged executable is actually complete before it is shipped — a build
    made with the wrong interpreter looks fine until it is run."""
    try:
        import fastapi, httpx, uvicorn, yaml            # noqa: F401
        from dotenv import load_dotenv                  # noqa: F401

        from . import brain, bridge, gemini, memory, permissions, persona, server, voice  # noqa: F401
        from .registry import REGISTRY, load_all_skills

        modules = load_all_skills()
        if not REGISTRY:
            print("SELFTEST FAIL: no tools registered")
            return 1
        print(f"SELFTEST OK: {modules} skill modules, {len(REGISTRY)} tools")
        return 0
    except Exception as exc:
        print(f"SELFTEST FAIL: {type(exc).__name__}: {exc}")
        return 1


def _store_key(key: str) -> None:
    if not key:
        print("  Usage: python -m jarvis key YOUR_API_KEY")
        return
    cfg.update({"secrets.gemini_api_key": key.strip()})
    print("  Key saved to data/settings.json (git-ignored).")


def main() -> None:
    cmd = (sys.argv[1] if len(sys.argv) > 1 else "app").lower()

    if cmd in ("app", "start", "run"):
        mode = str(cfg.get("server.window", "native")).lower()
        if mode == "native":
            from .native import launch
            launch()
        else:
            from .desktop import launch as browser_launch
            browser_launch(window=mode)
    elif cmd == "native":
        from .native import launch
        launch()
    elif cmd == "serve":
        _serve_headless()
    elif cmd == "browser":
        from .desktop import launch
        launch(window="browser")
    elif cmd == "models":
        asyncio.run(_models())
    elif cmd == "doctor":
        asyncio.run(_doctor())
    elif cmd == "key":
        _store_key(sys.argv[2] if len(sys.argv) > 2 else "")
    elif cmd == "selftest":
        sys.exit(_selftest())
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
