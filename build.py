"""Build JARVIS into a standalone Windows executable.

    python build.py            single-file JARVIS.exe          (default)
    python build.py --dir      one-folder build, starts faster
    python build.py --console  keep a console window for debugging

Single-file is one tidy artifact but unpacks itself to a temp directory on
every launch, which costs a few seconds of startup. The folder build starts
almost instantly and is the better choice if you run it often.

Output lands in dist/.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ICON = ROOT / "ui" / "assets" / "jarvis.ico"

# Modules PyInstaller's static analysis cannot see: uvicorn resolves its
# protocol and loop backends by string at runtime, and the skills package is
# loaded by pkgutil scan rather than direct import.
HIDDEN = [
    "uvicorn.logging", "uvicorn.loops", "uvicorn.loops.auto", "uvicorn.loops.asyncio",
    "uvicorn.protocols", "uvicorn.protocols.http", "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl", "uvicorn.protocols.http.httptools_impl",
    "uvicorn.protocols.websockets", "uvicorn.protocols.websockets.auto",
    "uvicorn.protocols.websockets.websockets_impl",
    "uvicorn.lifespan", "uvicorn.lifespan.on", "uvicorn.lifespan.off",
    "websockets", "websockets.legacy", "httptools", "h11",
    # Core runtime. These are plain top-level imports that PyInstaller finds on
    # its own, but naming them explicitly means a bad build fails loudly at
    # analysis time rather than silently shipping without them.
    "yaml", "fastapi", "uvicorn", "httpx", "dotenv", "pydantic",
    "anyio", "starlette", "sniffio", "click",
    "jarvis.server", "jarvis.brain", "jarvis.desktop",
    "jarvis.skills.system", "jarvis.skills.files", "jarvis.skills.shell",
    "jarvis.skills.apps", "jarvis.skills.vision", "jarvis.skills.control",
    "jarvis.skills.web", "jarvis.skills.recall", "jarvis.skills.claude_agent",
    "psutil", "mss", "mss.windows", "pyperclip",
    # Neural voice. edge-tts talks over aiohttp and needs certifi's CA bundle.
    "edge_tts", "aiohttp", "certifi", "tabulate", "multidict", "yarl",
    "aiosignal", "frozenlist", "attrs", "propcache",
    "jarvis.voice", "jarvis.bridge", "jarvis.native",
    "jarvis.api", "jarvis.hostapi", "jarvis.voice_gemini", "jarvis.geometry",
    # Native window shell. pywebview's Windows backend is built on pythonnet,
    # which PyInstaller cannot discover by static analysis alone.
    "webview", "webview.platforms", "webview.platforms.edgechromium",
    "clr", "clr_loader", "pythonnet", "bottle", "proxy_tools",
    "pystray", "pystray._win32",
    "pyautogui", "pygetwindow", "pyrect", "pyscreeze", "pytweening", "mouseinfo",
    "pymsgbox", "PIL", "PIL.Image", "PIL.ImageGrab",
    # On-device face recognition.
    "cv2", "numpy", "jarvis.faceid", "jarvis.skills.identity",
    "win32com", "win32com.shell", "win32com.shell.shell", "win32com.shell.shellcon",
    "encodings.idna",
]

# Big things we never use — dropping them saves ~40 MB.
# Do NOT add "unittest" or "test" here: pyrect (which pygetwindow depends on)
# imports unittest at module level, and excluding it silently kills all window
# management in the packaged build.
EXCLUDE = [
    "tkinter", "matplotlib", "scipy", "pandas", "PyQt5", "PyQt6",
    "PySide2", "PySide6", "IPython", "jupyter", "notebook", "pytest",
    "setuptools", "pip", "wheel",
]


def make_icon() -> Path | None:
    """Draw the arc reactor as a multi-resolution .ico."""
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        print("  Pillow not available — building without a custom icon.")
        return None

    ICON.parent.mkdir(parents=True, exist_ok=True)
    size = 256
    scale = 4                       # supersample, then downscale for smooth edges
    s = size * scale
    img = Image.new("RGBA", (s, s), (3, 7, 13, 255))
    d = ImageDraw.Draw(img)
    cx = cy = s / 2
    cyan = (56, 224, 255, 255)

    def ring(radius, width, alpha=255, segments=1, gap_deg=0):
        box = [cx - radius, cy - radius, cx + radius, cy + radius]
        if segments == 1:
            d.ellipse(box, outline=cyan[:3] + (alpha,), width=int(width))
            return
        step = 360 / segments
        for i in range(segments):
            a0 = i * step
            d.arc(box, a0, a0 + step - gap_deg, fill=cyan[:3] + (alpha,), width=int(width))

    ring(s * 0.44, s * 0.012, 90, segments=48, gap_deg=3.5)
    ring(s * 0.38, s * 0.022, 190, segments=6, gap_deg=14)
    ring(s * 0.30, s * 0.016, 140, segments=12, gap_deg=8)
    ring(s * 0.23, s * 0.030, 255, segments=3, gap_deg=26)

    # triangular core
    import math
    pts = [(cx + math.cos(math.radians(a)) * s * 0.115,
            cy + math.sin(math.radians(a)) * s * 0.115) for a in (-90, 30, 150)]
    d.polygon(pts, outline=cyan, width=int(s * 0.014))

    # glowing centre
    for r, a in ((s * 0.085, 60), (s * 0.065, 130), (s * 0.045, 255)):
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=cyan[:3] + (a,))

    img = img.resize((size, size), Image.LANCZOS)
    img.save(ICON, sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print(f"  icon      {ICON.relative_to(ROOT)}")
    return ICON


# Everything the app needs at runtime. PyInstaller bundles only what the
# BUILDING interpreter can import, so if any of these are missing it will
# happily produce an executable that is broken in a way you only discover
# when you double-click it.
REQUIRED = [
    ("yaml", "PyYAML"), ("fastapi", "fastapi"), ("uvicorn", "uvicorn"),
    ("httpx", "httpx"), ("dotenv", "python-dotenv"), ("pydantic", "pydantic"),
]
RECOMMENDED = [
    ("psutil", "psutil"), ("mss", "mss"), ("PIL", "Pillow"),
    ("edge_tts", "edge-tts"), ("pyperclip", "pyperclip"),
    ("pyautogui", "pyautogui"), ("pygetwindow", "pygetwindow"),
    ("webview", "pywebview"), ("pystray", "pystray"),
    ("cv2", "opencv-python"),
]


def _missing(mods: list[tuple[str, str]]) -> list[str]:
    import importlib.util
    out = []
    for module, package in mods:
        if importlib.util.find_spec(module) is None:
            out.append(package)
    return out


def _venv_python() -> Path | None:
    for candidate in (ROOT / ".venv" / "Scripts" / "python.exe",
                      ROOT / ".venv" / "bin" / "python"):
        if candidate.exists():
            return candidate
    return None


def preflight() -> int:
    """Refuse to build from an interpreter that cannot import the app.

    If the project venv can, re-run there automatically rather than making
    the user work it out.
    """
    missing = _missing(REQUIRED)
    if not missing:
        return 0

    print(f"\n  This Python cannot import JARVIS — missing: {', '.join(missing)}")
    print(f"  ({sys.executable})")

    venv = _venv_python()
    if venv and Path(venv) != Path(sys.executable):
        probe = subprocess.run(
            [str(venv), "-c", "import yaml, fastapi, uvicorn, httpx, dotenv"],
            capture_output=True,
        )
        if probe.returncode == 0:
            print(f"\n  The project virtual environment has them. Building there instead:")
            print(f"      {venv}\n")
            child = subprocess.run([str(venv), str(Path(__file__).resolve()), *sys.argv[1:]])
            # Hand the child's result straight to the shell. This must exit,
            # not return — falling through would let this interpreter run a
            # second build and overwrite the child's good executable with a
            # broken one.
            raise SystemExit(child.returncode)

    print("\n  Install the dependencies first:\n")
    print("      python -m venv .venv")
    print("      .venv\\Scripts\\python.exe -m pip install -r requirements.txt pyinstaller")
    print("      .venv\\Scripts\\python.exe build.py\n")
    print("  Or just run  build.bat  which does all of that for you.\n")
    return 1


def verify(exe: Path) -> bool:
    """Run the built executable and make it prove it is complete.

    Catches the whole class of packaging faults that are invisible at build
    time — a missing module only surfaces when the program actually runs.
    """
    print("  Verifying the executable…")
    try:
        result = subprocess.run([str(exe), "selftest"], capture_output=True,
                                text=True, timeout=180)
    except subprocess.TimeoutExpired:
        print("    FAILED — the executable hung on startup.")
        return False
    except OSError as exc:
        print(f"    FAILED — could not run it: {exc}")
        return False

    output = ((result.stdout or "") + (result.stderr or "")).strip()
    if result.returncode == 0 and "SELFTEST OK" in output:
        print(f"    {output.splitlines()[-1]}")
        return True

    print(f"    FAILED (exit {result.returncode})")
    for line in output.splitlines()[-12:]:
        print(f"      {line}")
    return False


def main() -> int:
    if (code := preflight()) != 0:
        return code

    onefile = "--dir" not in sys.argv
    console = "--console" in sys.argv

    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("\n  PyInstaller is not installed. Run:\n"
              f"      {sys.executable} -m pip install pyinstaller\n")
        return 1

    if absent := _missing(RECOMMENDED):
        print(f"\n  Note: building without {', '.join(absent)} — the features that "
              f"depend on them will be missing from this executable.")

    icon = make_icon()

    # Clear build artefacts only. dist/data holds the API key and enrolled
    # face on older layouts; wiping it on every rebuild was why those kept
    # disappearing.
    shutil.rmtree(ROOT / "build", ignore_errors=True)
    dist = ROOT / "dist"
    if dist.exists():
        for item in dist.iterdir():
            if item.name == "data":
                continue
            shutil.rmtree(item, ignore_errors=True) if item.is_dir() else item.unlink(missing_ok=True)

    sep = ";" if sys.platform == "win32" else ":"
    argv = [
        sys.executable, "-m", "PyInstaller",
        "--name", "JARVIS",
        "--onefile" if onefile else "--onedir",
        "--console" if console else "--noconsole",
        "--noconfirm", "--clean",
        # The HUD and the default config travel inside the bundle.
        "--add-data", f"{ROOT / 'ui'}{sep}ui",
        "--add-data", f"{ROOT / 'config.yaml'}{sep}.",
        "--collect-submodules", "jarvis.skills",
        "--collect-all", "pygetwindow",
        "--collect-all", "edge_tts",
        "--collect-all", "certifi",
        "--collect-all", "webview",
        "--collect-all", "clr_loader",
        "--collect-all", "pystray",
        "--paths", str(ROOT),
    ]
    if icon:
        argv += ["--icon", str(icon)]
    for h in HIDDEN:
        argv += ["--hidden-import", h]
    for e in EXCLUDE:
        argv += ["--exclude-module", e]
    argv.append(str(ROOT / "launch.py"))

    print(f"\n  Building {'single-file' if onefile else 'folder'} "
          f"{'console' if console else 'windowed'} executable…\n")
    result = subprocess.run(argv, cwd=ROOT)
    if result.returncode != 0:
        print("\n  Build failed. See the PyInstaller output above.")
        return result.returncode

    exe = ROOT / "dist" / ("JARVIS.exe" if onefile else "JARVIS/JARVIS.exe")
    if not exe.exists():
        print("\n  Build reported success but no executable was produced.")
        return 1

    print()
    if not verify(exe):
        print("""
  ─────────────────────────────────────────────────────
    The executable was built but does not work.

    This almost always means the interpreter that ran
    PyInstaller could not import every dependency, so
    they were left out of the bundle. Use build.bat, or
    build from the project virtual environment.
  ─────────────────────────────────────────────────────
""")
        return 1

    mb = exe.stat().st_size / 1e6
    print(f"""
  ─────────────────────────────────────────────────────
    Built:  {exe.relative_to(ROOT)}   ({mb:.0f} MB)

    JARVIS keeps its settings, memory and screenshots in
    a data/ folder created beside the executable, so put
    it somewhere writable — not Program Files.
  ─────────────────────────────────────────────────────
""")
    return 0


if __name__ == "__main__":
    sys.exit(main())
