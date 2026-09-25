"""Execution — shell, PowerShell, Python. The sharp end of the toolset."""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
import textwrap

from ..config import cfg
from ..registry import DANGEROUS, tool

IS_WIN = platform.system() == "Windows"


def _python_exe() -> str | None:
    """A real Python interpreter to hand scripts to.

    In a packaged build sys.executable is JARVIS.exe itself — running a script
    with it would relaunch the assistant instead of executing the code, so we
    go looking for a genuine interpreter on PATH instead.
    """
    if not getattr(sys, "frozen", False):
        return sys.executable
    for name in ("python", "python3", "py"):
        if found := shutil.which(name):
            return found
    return None


def _run(argv: list[str], cwd: str | None, timeout: int) -> dict:
    try:
        proc = subprocess.run(
            argv, capture_output=True, text=True, timeout=timeout,
            cwd=cwd or None, encoding="utf-8", errors="replace",
            creationflags=(subprocess.CREATE_NO_WINDOW if IS_WIN else 0),
        )
    except subprocess.TimeoutExpired:
        return {"error": f"Timed out after {timeout}s and was killed.", "timed_out": True}
    except FileNotFoundError as exc:
        return {"error": f"Interpreter not found: {exc}"}

    out = {
        "exit_code": proc.returncode,
        "stdout": (proc.stdout or "").strip(),
        "stderr": (proc.stderr or "").strip(),
        "success": proc.returncode == 0,
    }
    if not out["stdout"] and not out["stderr"]:
        out["note"] = "Command produced no output."
    return out


@tool(
    description=(
        "Run a PowerShell command and return its output. This is the primary way to "
        "do anything on Windows that has no dedicated tool: querying the registry, "
        "managing services, winget/choco installs, scheduled tasks, network config, "
        "Windows settings. Prefer a purpose-built tool when one exists."
    ),
    parameters={
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "The PowerShell command to run."},
            "working_directory": {"type": "string", "description": "Directory to run in."},
            "timeout": {"type": "integer", "description": "Seconds before it is killed."},
        },
        "required": ["command"],
    },
    category="execution",
    risk=DANGEROUS,
    confirm_hint="Executes a PowerShell command with your user privileges.",
)
def run_powershell(command: str, working_directory: str = "", timeout: int = 0) -> dict:
    if not IS_WIN:
        return {"error": "PowerShell is Windows-only on this machine."}
    t = timeout or int(cfg.get("security.shell_timeout", 45))
    result = _run(
        ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-Command", command],
        working_directory, t,
    )
    result["command"] = command
    return result


@tool(
    description=(
        "Run a command in the classic Windows command prompt (cmd.exe). Use only when "
        "something specifically needs cmd syntax; otherwise prefer run_powershell."
    ),
    parameters={
        "type": "object",
        "properties": {
            "command": {"type": "string"},
            "working_directory": {"type": "string"},
            "timeout": {"type": "integer"},
        },
        "required": ["command"],
    },
    category="execution",
    risk=DANGEROUS,
    confirm_hint="Executes a cmd.exe command with your user privileges.",
)
def run_command(command: str, working_directory: str = "", timeout: int = 0) -> dict:
    t = timeout or int(cfg.get("security.shell_timeout", 45))
    argv = ["cmd", "/c", command] if IS_WIN else ["/bin/sh", "-c", command]
    result = _run(argv, working_directory, t)
    result["command"] = command
    return result


@tool(
    description=(
        "Execute Python code and capture its output. Use for calculations, data "
        "processing, quick scripting, parsing, or anything easier expressed as code "
        "than as a shell command. The code runs in a fresh subprocess; print() to "
        "return values."
    ),
    parameters={
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "Python source. Use print() for output."},
            "timeout": {"type": "integer", "description": "Seconds. Default 60."},
        },
        "required": ["code"],
    },
    category="execution",
    risk=DANGEROUS,
    confirm_hint="Runs arbitrary Python code on your machine.",
)
def run_python(code: str, timeout: int = 60) -> dict:
    python = _python_exe()
    if not python:
        return {"error": "No Python interpreter found on this machine. Install Python from "
                         "python.org (tick 'Add Python to PATH') to enable code execution."}

    scratch = cfg.resolve("data/scratch")
    scratch.mkdir(parents=True, exist_ok=True)
    script = scratch / "_exec.py"
    script.write_text(textwrap.dedent(code), encoding="utf-8")

    result = _run([python, "-u", str(script)], str(scratch), max(5, timeout))
    result["code_lines"] = len(code.splitlines())
    return result


@tool(
    description=(
        "Install a Python package with pip. Use when a task needs a library that "
        "isn't available yet."
    ),
    parameters={
        "type": "object",
        "properties": {
            "package": {"type": "string", "description": "Package name, optionally pinned e.g. 'requests==2.31'."},
            "upgrade": {"type": "boolean"},
        },
        "required": ["package"],
    },
    category="execution",
    risk=DANGEROUS,
    confirm_hint="Downloads and installs a package from PyPI into your Python environment.",
)
def pip_install(package: str, upgrade: bool = False) -> dict:
    # Refuse anything that smells like shell injection via the package name.
    if any(c in package for c in ";&|`$\n<>"):
        return {"error": "That package name contains shell metacharacters. Refused."}
    if getattr(sys, "frozen", False):
        return {"error": "This is a packaged build — its Python libraries are fixed at build "
                         "time and cannot be extended with pip. Run JARVIS from source if you "
                         "need to add packages."}
    argv = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check"]
    if upgrade:
        argv.append("--upgrade")
    argv.extend(package.split())
    result = _run(argv, None, 300)
    result["package"] = package
    return result
