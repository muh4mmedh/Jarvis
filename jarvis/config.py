"""Configuration.

Four layers, highest priority first:

  1. data/settings.json   written by the in-app Settings panel     (git-ignored)
  2. .env                 environment / shell                      (git-ignored)
  3. config.yaml          shipped defaults, safe to commit
  4. built-in defaults    so a fresh clone runs with no files at all

Nothing sensitive is ever committed. There are no keys in source — a fresh
clone starts with an empty key and asks for one in the UI on first run.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import threading
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

# ── where things live ───────────────────────────────────────────────
# Running from source, everything sits in the repo. Running as a packaged
# .exe, read-only resources are extracted to a temp directory that is wiped
# on exit, so anything writable has to live beside the executable instead.
FROZEN = bool(getattr(sys, "frozen", False))

if FROZEN:
    RESOURCES = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    APP_DIR = Path(sys.executable).resolve().parent
else:
    RESOURCES = Path(__file__).resolve().parent.parent
    APP_DIR = RESOURCES

def _data_root() -> Path:
    """Where the key, memory and face profile live.

    A packaged build keeps them in %LOCALAPPDATA%\\JARVIS, not beside the
    executable. Sitting next to the .exe meant that replacing the .exe — a
    rebuild, an upgrade, moving it to another folder — silently took the API
    key and enrolled face with it. Per-user app data is also what Windows
    expects, and it works when the executable sits somewhere read-only.

    Running from source it stays in the repo, so a checkout is self-contained.
    Set JARVIS_DATA to override either way.
    """
    if override := os.getenv("JARVIS_DATA"):
        return Path(override).expanduser()

    if FROZEN:
        base = os.getenv("LOCALAPPDATA") or os.getenv("APPDATA")
        if base:
            return Path(base) / "JARVIS"
    return APP_DIR / "data"


DATA_DIR = _data_root()
ROOT = APP_DIR                      # where the executable or repo lives
UI_DIR = RESOURCES / "ui"           # read-only, bundled
SETTINGS_FILE = DATA_DIR / "settings.json"
CONFIG_FILE = APP_DIR / "config.yaml"

# Carry across anything an older build left beside the executable, so nobody
# loses a key or an enrolled face on upgrading.
if FROZEN and DATA_DIR != APP_DIR / "data":
    legacy = APP_DIR / "data"
    if legacy.is_dir() and not SETTINGS_FILE.exists():
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            for item in legacy.iterdir():
                target = DATA_DIR / item.name
                if target.exists():
                    continue
                if item.is_file():
                    shutil.copy2(item, target)
                elif item.is_dir() and item.name != "webview":
                    shutil.copytree(item, target, dirs_exist_ok=True)
        except OSError:
            pass

# A packaged build ships config.yaml inside the bundle. Copy it out on first
# run so it stays editable — otherwise the user could never change the
# blocklist or protected paths.
if FROZEN and not CONFIG_FILE.exists():
    bundled = RESOURCES / "config.yaml"
    if bundled.exists():
        try:
            APP_DIR.mkdir(parents=True, exist_ok=True)
            shutil.copy2(bundled, CONFIG_FILE)
        except OSError:
            CONFIG_FILE = bundled   # read-only location (e.g. Program Files)

load_dotenv(APP_DIR / ".env")

_DEFAULTS: dict[str, Any] = {
    "identity": {
        "name": "JARVIS",
        "expansion": "Just A Rather Very Intelligent System",
        "address_user_as": "Sir",
        "wit": 6,
    },
    "model": {
        "name": "",
        "prefer": ["gemini-3-flash", "gemini-2.5-flash", "gemini-2.0-flash", "gemini-flash-latest"],
        "temperature": 0.85,
        "max_output_tokens": 8192,
        "max_tool_steps": 12,
    },
    "security": {
        "mode": "guarded",
        "shell_blocklist": [],
        "protected_paths": [],
        "shell_timeout": 45,
        "max_tool_output": 24000,
        # Face recognition as an approval shortcut.
        #   off       : always click to approve                        [default]
        #   dangerous : a confident face match approves shell/code execution
        #   all       : a match approves every sensitive operation too
        "face_approval": "off",
    },
    "memory": {"enabled": True, "db_path": "data/memory.db", "context_turns": 24, "max_facts": 60},
    "voice": {"tts_enabled": True, "stt_enabled": True, "wake_word": "jarvis",
              "voice_prefer": [], "rate": 1.04, "pitch": 0.92,
              # "neural" = Edge online neural voices, "browser" = OS voices.
              # "gemini" performs the line with a directed style; "neural" is
              # edge-tts (dependable, flat); "browser" is the OS voice.
              "engine": "gemini", "neural_voice": "en-GB-RyanNeural",
              "gemini_voice": "Charon", "gemini_model": "", "style": "",
              # Always-on "hey jarvis" listening.
              "always_listening": False, "chime": True},
    "ui": {"boot_sequence": True, "theme_accent": "#38e0ff", "theme_warn": "#ffb648",
           "theme_alert": "#ff4d5a", "telemetry_interval": 2.0,
           # Where the window was last time. Written by the app, not the panel.
           "window_geometry": {}},
    "server": {"host": "127.0.0.1", "port": 8765, "window": "native"},
    # Never written to config.yaml. Lives only in data/settings.json or .env.
    "secrets": {"gemini_api_key": ""},
}

# Keys the Settings panel is allowed to write. Anything else is ignored, so a
# malformed or hostile POST cannot reach into unrelated configuration.
WRITABLE = {
    "identity.name", "identity.expansion", "identity.address_user_as", "identity.wit",
    "model.name", "model.temperature", "model.max_output_tokens", "model.max_tool_steps",
    "security.mode", "security.shell_timeout", "security.max_tool_output",
    "security.protected_paths", "security.face_approval",
    "memory.enabled", "memory.context_turns", "memory.max_facts",
    "voice.tts_enabled", "voice.stt_enabled", "voice.wake_word", "voice.voice_prefer",
    "voice.rate", "voice.pitch", "voice.engine", "voice.neural_voice",
    "voice.gemini_voice", "voice.style",
    "voice.always_listening", "voice.chime",
    "ui.boot_sequence", "ui.theme_accent", "ui.theme_warn", "ui.theme_alert",
    "ui.telemetry_interval", "ui.window_geometry",
    "server.host", "server.port", "server.window",
    "secrets.gemini_api_key",
}


def _deep_merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _coerce(current: Any, value: Any) -> Any:
    """Keep types stable so the UI can't turn an int setting into a string."""
    if isinstance(current, dict) or isinstance(value, dict):
        return value if isinstance(value, dict) else current
    if isinstance(current, bool):
        return value if isinstance(value, bool) else str(value).lower() in ("1", "true", "yes", "on")
    if isinstance(current, int) and not isinstance(current, bool):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return current
    if isinstance(current, float):
        try:
            return float(value)
        except (TypeError, ValueError):
            return current
    if isinstance(current, list):
        if isinstance(value, list):
            return value
        return [s.strip() for s in str(value).split(",") if s.strip()]
    return value


class Config:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.reload()

    # ── loading ─────────────────────────────────────────────────────
    def reload(self) -> None:
        with self._lock:
            merged = json.loads(json.dumps(_DEFAULTS))  # deep copy

            # layer 3: config.yaml
            if CONFIG_FILE.exists():
                try:
                    merged = _deep_merge(merged, yaml.safe_load(CONFIG_FILE.read_text(encoding="utf-8")) or {})
                except yaml.YAMLError:
                    pass

            # layer 2: environment
            env_map = {
                "GEMINI_API_KEY": "secrets.gemini_api_key",
                "GEMINI_MODEL": "model.name",
                "JARVIS_HOST": "server.host",
                "JARVIS_PORT": "server.port",
            }
            for env_key, dotted in env_map.items():
                if val := os.getenv(env_key):
                    _set_in(merged, dotted, val)

            # layer 1: settings.json written by the UI
            self._user: dict = {}
            if SETTINGS_FILE.exists():
                try:
                    self._user = json.loads(SETTINGS_FILE.read_text(encoding="utf-8")) or {}
                    merged = _deep_merge(merged, self._user)
                except (json.JSONDecodeError, OSError):
                    self._user = {}

            self._d = merged

    # ── reading ─────────────────────────────────────────────────────
    def get(self, path: str, default: Any = None) -> Any:
        node: Any = self._d
        for part in path.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def __getitem__(self, key: str) -> Any:
        return self._d[key]

    @property
    def api_key(self) -> str:
        return str(self.get("secrets.gemini_api_key", "") or "").strip()

    @property
    def host(self) -> str:
        return str(self.get("server.host", "127.0.0.1"))

    @property
    def port(self) -> int:
        return int(self.get("server.port", 8765))

    @property
    def mode(self) -> str:
        return str(self.get("security.mode", "guarded")).lower()

    @mode.setter
    def mode(self, value: str) -> None:
        if value not in ("readonly", "guarded", "open"):
            raise ValueError(f"unknown security mode: {value}")
        with self._lock:
            self._d["security"]["mode"] = value

    def resolve(self, p: str | Path) -> Path:
        """Absolute paths pass through; "data/..." lands in the per-user data
        directory; anything else is relative to the install."""
        p = Path(p)
        if p.is_absolute():
            return p
        parts = p.parts
        if parts and parts[0] == "data":
            return DATA_DIR.joinpath(*parts[1:])
        return ROOT / p

    # ── writing ─────────────────────────────────────────────────────
    def update(self, patch: dict[str, Any], persist: bool = True) -> list[str]:
        """Apply a flat {dotted.key: value} patch. Returns the keys actually changed."""
        changed: list[str] = []
        with self._lock:
            for dotted, value in (patch or {}).items():
                if dotted not in WRITABLE:
                    continue
                current = self.get(dotted)
                new = _coerce(current, value)
                if new == current:
                    continue
                _set_in(self._d, dotted, new)
                # A non-persisting update (e.g. probing a candidate API key) must not
                # leak into the user layer, or a later save would write it to disk.
                if persist:
                    _set_in(self._user, dotted, new)
                changed.append(dotted)

            if changed and persist:
                SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
                tmp = SETTINGS_FILE.with_suffix(".tmp")
                tmp.write_text(json.dumps(self._user, indent=2), encoding="utf-8")
                tmp.replace(SETTINGS_FILE)
        return changed

    def export_for_ui(self) -> dict:
        """Everything the Settings panel renders. The key is never sent back in full."""
        key = self.api_key
        return {
            "identity": dict(self.get("identity", {})),
            "model": {k: self.get(f"model.{k}") for k in
                      ("name", "temperature", "max_output_tokens", "max_tool_steps")},
            "security": {k: self.get(f"security.{k}") for k in
                         ("mode", "shell_timeout", "max_tool_output",
                          "protected_paths", "face_approval")},
            "memory": dict(self.get("memory", {})),
            "voice": dict(self.get("voice", {})),
            "ui": dict(self.get("ui", {})),
            "server": dict(self.get("server", {})),
            "secrets": {
                "gemini_api_key_set": bool(key),
                "gemini_api_key_hint": (key[:6] + "…" + key[-4:]) if len(key) > 12 else "",
                "from_env": bool(os.getenv("GEMINI_API_KEY")),
            },
        }


def _set_in(d: dict, dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    node = d
    for p in parts[:-1]:
        node = node.setdefault(p, {})
    node[parts[-1]] = value


cfg = Config()
