"""System awareness — the machine's vital signs."""
from __future__ import annotations

import os
import platform
import shutil
import socket
import subprocess
import time
from datetime import datetime

from ..registry import SAFE, SENSITIVE, tool

try:
    import psutil
except ImportError:
    psutil = None


def _need_psutil():
    if psutil is None:
        raise RuntimeError("psutil is not installed — run: pip install psutil")


@tool(
    description=(
        "Read live system vitals: CPU load per core, memory, disk usage, uptime, "
        "battery and network throughput. Use this whenever asked how the machine "
        "is doing, whether something is slow, or for a status report."
    ),
    category="system",
    risk=SAFE,
)
def system_status() -> dict:
    _need_psutil()
    vm = psutil.virtual_memory()
    boot = psutil.boot_time()
    out: dict = {
        "host": platform.node(),
        "os": f"{platform.system()} {platform.release()} ({platform.version()})",
        "cpu": {
            "percent": psutil.cpu_percent(interval=0.4),
            "per_core": psutil.cpu_percent(interval=0.0, percpu=True),
            "cores_physical": psutil.cpu_count(logical=False),
            "cores_logical": psutil.cpu_count(logical=True),
        },
        "memory": {
            "total_gb": round(vm.total / 1e9, 2),
            "used_gb": round(vm.used / 1e9, 2),
            "available_gb": round(vm.available / 1e9, 2),
            "percent": vm.percent,
        },
        "uptime_hours": round((time.time() - boot) / 3600, 1),
        "booted": datetime.fromtimestamp(boot).strftime("%Y-%m-%d %H:%M"),
        "local_time": datetime.now().strftime("%A %d %B %Y, %H:%M:%S"),
    }

    disks = []
    for part in psutil.disk_partitions(all=False):
        try:
            u = psutil.disk_usage(part.mountpoint)
        except (PermissionError, OSError):
            continue
        disks.append({
            "mount": part.mountpoint,
            "fs": part.fstype,
            "total_gb": round(u.total / 1e9, 1),
            "free_gb": round(u.free / 1e9, 1),
            "percent": u.percent,
        })
    out["disks"] = disks

    try:
        if (batt := psutil.sensors_battery()):
            out["battery"] = {
                "percent": round(batt.percent, 1),
                "plugged_in": batt.power_plugged,
                "minutes_left": (round(batt.secsleft / 60) if batt.secsleft > 0 else None),
            }
    except Exception:
        pass

    try:
        net = psutil.net_io_counters()
        out["network"] = {
            "sent_gb": round(net.bytes_sent / 1e9, 2),
            "recv_gb": round(net.bytes_recv / 1e9, 2),
            "hostname": socket.gethostname(),
        }
    except Exception:
        pass

    return out


@tool(
    description=(
        "List running processes sorted by CPU or memory. Use to find what is "
        "eating resources, or to check whether a specific program is running."
    ),
    parameters={
        "type": "object",
        "properties": {
            "sort_by": {"type": "string", "enum": ["cpu", "memory"],
                        "description": "Resource to rank by. Default cpu."},
            "limit": {"type": "integer", "description": "How many to return. Default 15."},
            "name_filter": {"type": "string",
                            "description": "Only include processes whose name contains this text."},
        },
    },
    category="system",
    risk=SAFE,
)
def list_processes(sort_by: str = "cpu", limit: int = 15, name_filter: str = "") -> dict:
    _need_psutil()
    psutil.cpu_percent(interval=None)  # prime the counters
    time.sleep(0.3)

    rows = []
    for p in psutil.process_iter(["pid", "name", "cpu_percent", "memory_info", "username"]):
        try:
            info = p.info
            name = info.get("name") or "?"
            if name_filter and name_filter.lower() not in name.lower():
                continue
            rows.append({
                "pid": info["pid"],
                "name": name,
                "cpu_percent": round(info.get("cpu_percent") or 0.0, 1),
                "memory_mb": round((info["memory_info"].rss if info.get("memory_info") else 0) / 1e6, 1),
                "user": (info.get("username") or "").split("\\")[-1],
            })
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    key = "cpu_percent" if sort_by == "cpu" else "memory_mb"
    rows.sort(key=lambda r: -r[key])
    return {"sorted_by": sort_by, "count": len(rows), "processes": rows[: max(1, min(limit, 60))]}


@tool(
    description=(
        "Terminate a running process by PID or by exact name. Use when the user "
        "asks to kill, close or force-quit a program that is misbehaving."
    ),
    parameters={
        "type": "object",
        "properties": {
            "pid": {"type": "integer", "description": "Process ID to terminate."},
            "name": {"type": "string",
                     "description": "Process name (e.g. 'chrome.exe'). Kills all matches."},
            "force": {"type": "boolean", "description": "Kill instead of asking politely first."},
        },
    },
    category="system",
    risk=SENSITIVE,
    confirm_hint="This will terminate a running program. Unsaved work in it will be lost.",
)
def kill_process(pid: int = 0, name: str = "", force: bool = False) -> dict:
    _need_psutil()
    if not pid and not name:
        return {"error": "Give me a pid or a name."}

    targets = []
    if pid:
        try:
            targets.append(psutil.Process(pid))
        except psutil.NoSuchProcess:
            return {"error": f"No process with pid {pid}."}
    else:
        for p in psutil.process_iter(["pid", "name"]):
            if (p.info.get("name") or "").lower() == name.lower():
                targets.append(p)
        if not targets:
            return {"error": f"No running process named {name!r}."}

    killed, failed = [], []
    for p in targets:
        try:
            label = f"{p.name()} (pid {p.pid})"
            p.kill() if force else p.terminate()
            killed.append(label)
        except (psutil.NoSuchProcess, psutil.AccessDenied) as exc:
            failed.append(f"{p.pid}: {type(exc).__name__}")

    psutil.wait_procs(targets, timeout=3)
    return {"terminated": killed, "failed": failed}


@tool(
    description=(
        "Get network configuration and connectivity: local IP addresses, active "
        "interfaces, and whether the internet is actually reachable."
    ),
    category="system",
    risk=SAFE,
)
def network_info() -> dict:
    _need_psutil()
    interfaces = {}
    stats = psutil.net_if_stats()
    for iface, addrs in psutil.net_if_addrs().items():
        st = stats.get(iface)
        if st and not st.isup:
            continue
        ips = [a.address for a in addrs if a.family == socket.AF_INET]
        if ips:
            interfaces[iface] = {"ipv4": ips, "speed_mbps": st.speed if st else None}

    online = False
    try:
        with socket.create_connection(("1.1.1.1", 53), timeout=2.5):
            online = True
    except OSError:
        pass

    return {"internet_reachable": online, "interfaces": interfaces,
            "hostname": socket.gethostname()}


@tool(
    description=(
        "Set the system master volume, mute or unmute. Windows only. "
        "Use for 'turn it down', 'mute', 'volume to 30 percent'."
    ),
    parameters={
        "type": "object",
        "properties": {
            "level": {"type": "integer", "description": "Target volume 0-100."},
            "mute": {"type": "boolean", "description": "True to mute, False to unmute."},
        },
    },
    category="system",
    risk=SENSITIVE,
    confirm_hint="Changes the system audio output level.",
)
def set_volume(level: int = -1, mute: bool | None = None) -> dict:
    if platform.system() != "Windows":
        return {"error": "Volume control is implemented for Windows only."}
    try:
        # Media-key simulation via PowerShell WScript.Shell — no extra dependency.
        actions = []
        if mute is not None:
            subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "(New-Object -ComObject WScript.Shell).SendKeys([char]173)"],
                capture_output=True, timeout=10, check=False,
            )
            actions.append("toggled mute")
        if 0 <= level <= 100:
            # 50 down-steps zeroes it, then step up. Each step is 2%.
            script = (
                "$w=New-Object -ComObject WScript.Shell;"
                "1..50|%{$w.SendKeys([char]174)};"
                f"1..{round(level / 2)}|%{{$w.SendKeys([char]175)}}"
            )
            subprocess.run(["powershell", "-NoProfile", "-Command", script],
                           capture_output=True, timeout=25, check=False)
            actions.append(f"volume set to ~{level}%")
        if not actions:
            return {"error": "Give me a level 0-100 or a mute flag."}
        return {"ok": True, "actions": actions}
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


@tool(
    description=(
        "Check whether a command-line program is installed and where it lives. "
        "Use before suggesting the user run something, or to answer 'do I have X installed'."
    ),
    parameters={
        "type": "object",
        "properties": {"name": {"type": "string", "description": "Executable name, e.g. 'git', 'node', 'ffmpeg'."}},
        "required": ["name"],
    },
    category="system",
    risk=SAFE,
)
def which(name: str) -> dict:
    path = shutil.which(name)
    if not path:
        return {"installed": False, "name": name}
    version = ""
    for flag in ("--version", "-version", "/?"):
        try:
            r = subprocess.run([path, flag], capture_output=True, text=True, timeout=8)
            if r.returncode == 0 and (r.stdout or r.stderr).strip():
                version = (r.stdout or r.stderr).strip().splitlines()[0]
                break
        except Exception:
            continue
    return {"installed": True, "name": name, "path": path, "version": version}


@tool(
    description="Get the current date, time, day of week and timezone.",
    category="system",
    risk=SAFE,
)
def current_time() -> dict:
    now = datetime.now()
    return {
        "iso": now.isoformat(timespec="seconds"),
        "readable": now.strftime("%A, %d %B %Y at %H:%M:%S"),
        "day_of_week": now.strftime("%A"),
        "timezone": str(now.astimezone().tzinfo),
        "unix": int(time.time()),
    }
