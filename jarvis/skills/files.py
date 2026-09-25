"""Filesystem access — read, write, search, organise."""
from __future__ import annotations

import fnmatch
import os
import shutil
import time
from datetime import datetime
from pathlib import Path

from ..config import cfg
from ..registry import SAFE, SENSITIVE, tool

# Where "documents", "downloads" etc. resolve to.
HOME = Path.home()
SHORTCUTS = {
    "home": HOME, "desktop": HOME / "Desktop", "documents": HOME / "Documents",
    "downloads": HOME / "Downloads", "pictures": HOME / "Pictures",
    "videos": HOME / "Videos", "music": HOME / "Music",
}

SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".idea",
             "AppData", "$Recycle.Bin", "System Volume Information", ".cache"}


def _resolve(path: str) -> Path:
    raw = (path or ".").strip().strip('"')
    key = raw.lower().lstrip("~/\\")
    if key in SHORTCUTS:
        return SHORTCUTS[key]
    return Path(os.path.expandvars(raw)).expanduser()


def _human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}PB"


@tool(
    description=(
        "List the contents of a directory with sizes and modification times. "
        "Accepts shortcuts: home, desktop, documents, downloads, pictures, videos."
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Directory path or shortcut name. Default current directory."},
            "pattern": {"type": "string", "description": "Optional glob filter, e.g. '*.pdf'."},
            "show_hidden": {"type": "boolean", "description": "Include dotfiles and hidden entries."},
        },
    },
    category="files",
    risk=SAFE,
)
def list_directory(path: str = ".", pattern: str = "", show_hidden: bool = False) -> dict:
    d = _resolve(path)
    if not d.exists():
        return {"error": f"No such directory: {d}"}
    if not d.is_dir():
        return {"error": f"{d} is a file, not a directory."}

    dirs, files, total = [], [], 0
    try:
        for entry in sorted(d.iterdir(), key=lambda p: p.name.lower()):
            if not show_hidden and entry.name.startswith("."):
                continue
            if pattern and entry.is_file() and not fnmatch.fnmatch(entry.name.lower(), pattern.lower()):
                continue
            try:
                st = entry.stat()
                modified = datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")
            except OSError:
                continue
            if entry.is_dir():
                dirs.append({"name": entry.name, "modified": modified})
            else:
                total += st.st_size
                files.append({"name": entry.name, "size": _human(st.st_size),
                              "bytes": st.st_size, "modified": modified})
    except PermissionError:
        return {"error": f"Access denied to {d}"}

    return {"path": str(d), "directories": dirs[:200], "files": files[:300],
            "counts": {"dirs": len(dirs), "files": len(files)}, "total_size": _human(total)}


@tool(
    description=(
        "Read the contents of a text file. Use for source code, config, notes, logs, "
        "CSV — anything textual. Returns line numbers for easy reference."
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path to the file."},
            "start_line": {"type": "integer", "description": "1-indexed first line. Default 1."},
            "max_lines": {"type": "integer", "description": "How many lines to read. Default 400."},
        },
        "required": ["path"],
    },
    category="files",
    risk=SAFE,
)
def read_file(path: str, start_line: int = 1, max_lines: int = 400) -> dict:
    f = _resolve(path)
    if not f.exists():
        return {"error": f"No such file: {f}"}
    if f.is_dir():
        return {"error": f"{f} is a directory — use list_directory."}
    if f.stat().st_size > 12_000_000:
        return {"error": f"File is {_human(f.stat().st_size)} — too large to read wholesale."}

    try:
        text = f.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        return {"error": f"Could not read {f}: {exc}"}

    lines = text.splitlines()
    start = max(1, start_line)
    chunk = lines[start - 1: start - 1 + max(1, max_lines)]
    numbered = "\n".join(f"{i:>5} | {ln}" for i, ln in enumerate(chunk, start=start))
    return {
        "path": str(f), "total_lines": len(lines),
        "showing": f"{start}-{start + len(chunk) - 1}",
        "truncated": start - 1 + len(chunk) < len(lines),
        "content": numbered,
    }


@tool(
    description=(
        "Create a new file or completely overwrite an existing one. For small edits "
        "to an existing file prefer edit_file — this replaces the whole thing."
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Where to write."},
            "content": {"type": "string", "description": "Full file contents."},
            "append": {"type": "boolean", "description": "Append instead of overwriting."},
        },
        "required": ["path", "content"],
    },
    category="files",
    risk=SENSITIVE,
    confirm_hint="Writes to disk. An existing file at this path will be overwritten.",
)
def write_file(path: str, content: str, append: bool = False) -> dict:
    f = _resolve(path)
    f.parent.mkdir(parents=True, exist_ok=True)
    existed = f.exists()
    # Keep one backup of anything we clobber.
    backup = None
    if existed and not append:
        try:
            backup = f.with_suffix(f.suffix + ".jarvis-bak")
            shutil.copy2(f, backup)
        except Exception:
            backup = None
    with f.open("a" if append else "w", encoding="utf-8", newline="") as fh:
        fh.write(content)
    return {"ok": True, "path": str(f), "action": "appended" if append else
            ("overwritten" if existed else "created"),
            "bytes": f.stat().st_size, "backup": str(backup) if backup else None}


@tool(
    description=(
        "Replace an exact string inside an existing file. Safer than rewriting the "
        "whole file. The old text must appear exactly once unless replace_all is set."
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "old_text": {"type": "string", "description": "Exact text to find, including indentation."},
            "new_text": {"type": "string", "description": "Replacement text."},
            "replace_all": {"type": "boolean", "description": "Replace every occurrence."},
        },
        "required": ["path", "old_text", "new_text"],
    },
    category="files",
    risk=SENSITIVE,
    confirm_hint="Modifies an existing file in place.",
)
def edit_file(path: str, old_text: str, new_text: str, replace_all: bool = False) -> dict:
    f = _resolve(path)
    if not f.exists():
        return {"error": f"No such file: {f}"}
    text = f.read_text(encoding="utf-8", errors="replace")
    count = text.count(old_text)
    if count == 0:
        return {"error": "That exact text does not appear in the file. Check whitespace."}
    if count > 1 and not replace_all:
        return {"error": f"Found {count} occurrences. Give me more surrounding context, "
                         f"or set replace_all."}
    f.write_text(text.replace(old_text, new_text) if replace_all
                 else text.replace(old_text, new_text, 1), encoding="utf-8")
    return {"ok": True, "path": str(f), "replacements": count if replace_all else 1}


@tool(
    description=(
        "Find files by name pattern anywhere under a directory. Use for "
        "'where is my X file', 'find all PDFs from last month'."
    ),
    parameters={
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Glob such as '*.pdf' or '*invoice*'."},
            "path": {"type": "string", "description": "Root to search from. Default home."},
            "max_results": {"type": "integer", "description": "Default 50."},
            "modified_within_days": {"type": "integer", "description": "Only files touched this recently."},
        },
        "required": ["pattern"],
    },
    category="files",
    risk=SAFE,
)
def find_files(pattern: str, path: str = "home", max_results: int = 50,
               modified_within_days: int = 0) -> dict:
    root = _resolve(path)
    if not root.exists():
        return {"error": f"No such directory: {root}"}
    pat = pattern.lower()
    if "*" not in pat and "?" not in pat:
        pat = f"*{pat}*"
    cutoff = time.time() - modified_within_days * 86400 if modified_within_days else 0

    hits, scanned, deadline = [], 0, time.time() + 25
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        scanned += 1
        if time.time() > deadline or len(hits) >= max_results:
            break
        for fn in filenames:
            if fnmatch.fnmatch(fn.lower(), pat):
                p = Path(dirpath) / fn
                try:
                    st = p.stat()
                except OSError:
                    continue
                if cutoff and st.st_mtime < cutoff:
                    continue
                hits.append({"path": str(p), "size": _human(st.st_size),
                             "modified": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")})
                if len(hits) >= max_results:
                    break

    hits.sort(key=lambda h: h["modified"], reverse=True)
    return {"pattern": pattern, "root": str(root), "dirs_scanned": scanned,
            "found": len(hits), "results": hits}


@tool(
    description=(
        "Search for text INSIDE files — grep. Use to find where a function is "
        "defined, which config holds a setting, or any phrase across a project."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Text to search for."},
            "path": {"type": "string", "description": "Directory to search. Default current."},
            "file_pattern": {"type": "string", "description": "Restrict to e.g. '*.py'."},
            "max_results": {"type": "integer", "description": "Default 40."},
        },
        "required": ["query"],
    },
    category="files",
    risk=SAFE,
)
def search_in_files(query: str, path: str = ".", file_pattern: str = "*",
                    max_results: int = 40) -> dict:
    root = _resolve(path)
    if not root.exists():
        return {"error": f"No such directory: {root}"}
    needle = query.lower()
    hits, deadline = [], time.time() + 25

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        if time.time() > deadline or len(hits) >= max_results:
            break
        for fn in filenames:
            if not fnmatch.fnmatch(fn.lower(), file_pattern.lower()):
                continue
            p = Path(dirpath) / fn
            try:
                if p.stat().st_size > 3_000_000:
                    continue
                with p.open(encoding="utf-8", errors="ignore") as fh:
                    for lineno, line in enumerate(fh, 1):
                        if needle in line.lower():
                            hits.append({"file": str(p), "line": lineno,
                                         "text": line.strip()[:200]})
                            if len(hits) >= max_results:
                                break
            except (OSError, PermissionError):
                continue

    return {"query": query, "root": str(root), "matches": len(hits), "results": hits}


@tool(
    description="Create a directory, including any missing parent directories.",
    parameters={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
    category="files",
    risk=SENSITIVE,
    confirm_hint="Creates a new folder on disk.",
)
def make_directory(path: str) -> dict:
    d = _resolve(path)
    existed = d.exists()
    d.mkdir(parents=True, exist_ok=True)
    return {"ok": True, "path": str(d), "already_existed": existed}


@tool(
    description="Move or rename a file or folder.",
    parameters={
        "type": "object",
        "properties": {
            "source": {"type": "string"},
            "destination": {"type": "string", "description": "New path, or a target directory."},
        },
        "required": ["source", "destination"],
    },
    category="files",
    risk=SENSITIVE,
    confirm_hint="Moves or renames an item on disk.",
)
def move_item(source: str, destination: str) -> dict:
    src, dst = _resolve(source), _resolve(destination)
    if not src.exists():
        return {"error": f"No such item: {src}"}
    if dst.is_dir():
        dst = dst / src.name
    if dst.exists():
        return {"error": f"{dst} already exists. Move it aside first."}
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))
    return {"ok": True, "from": str(src), "to": str(dst)}


@tool(
    description="Copy a file or an entire folder.",
    parameters={
        "type": "object",
        "properties": {"source": {"type": "string"}, "destination": {"type": "string"}},
        "required": ["source", "destination"],
    },
    category="files",
    risk=SENSITIVE,
    confirm_hint="Copies data to a new location on disk.",
)
def copy_item(source: str, destination: str) -> dict:
    src, dst = _resolve(source), _resolve(destination)
    if not src.exists():
        return {"error": f"No such item: {src}"}
    if dst.is_dir() and src.is_file():
        dst = dst / src.name
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.is_dir():
        shutil.copytree(src, dst, dirs_exist_ok=True)
    else:
        shutil.copy2(src, dst)
    return {"ok": True, "from": str(src), "to": str(dst)}


@tool(
    description=(
        "Send a file or folder to the Recycle Bin. This is recoverable — it is NOT "
        "a permanent delete, which is deliberate."
    ),
    parameters={
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
    },
    category="files",
    risk=SENSITIVE,
    confirm_hint="Sends this item to the Recycle Bin. Recoverable, but still a deletion.",
)
def recycle_item(path: str) -> dict:
    p = _resolve(path)
    if not p.exists():
        return {"error": f"No such item: {p}"}
    try:
        # Shell delete puts it in the bin rather than destroying it.
        from win32com.shell import shell, shellcon  # type: ignore
        shell.SHFileOperation((
            0, shellcon.FO_DELETE, str(p), None,
            shellcon.FOF_ALLOWUNDO | shellcon.FOF_NOCONFIRMATION | shellcon.FOF_SILENT,
            None, None,
        ))
        return {"ok": True, "recycled": str(p)}
    except ImportError:
        return {"error": "Recycle Bin support needs pywin32 (pip install pywin32). "
                         "I will not hard-delete as a fallback — move it instead if you're sure."}
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}
