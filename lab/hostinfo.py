"""What the machine that ran a shard was: OS, kernel, CPUs, memory, disk, CPU flags and the runner's identity.

:func:`collect` never raises; any probe that fails gives null. Keys: ``os_release`` (``ID``, ``VERSION_ID``,
``PRETTY_NAME``), ``kernel``, ``nproc_logical``, ``nproc_available``, ``mem_total_bytes``, ``mem_available_bytes``,
``disk_free_bytes`` (of the given path), ``cpu`` (``model_name``, ``cores_per_socket``, ``sockets``,
``threads_per_core``, ``physical_cores``, from ``lscpu`` under ``LC_ALL=C``; :func:`physical_cores` alone),
``cpu_flags`` (``avx2``, ``avx512f``, ``avx512_vnni``, ``amx_tile`` from the first ``flags`` line of
``/proc/cpuinfo``), ``env`` (exactly
:data:`ENV_NAMES`; no token or secret is among them) and ``python``. :func:`mem_available` and
:func:`nproc_available` are what the provision preflight and the server's thread counts read.
"""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

ENV_NAMES = ("ImageOS", "ImageVersion", "RUNNER_OS", "RUNNER_ARCH", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT",
             "GITHUB_SHA", "GITHUB_REF", "GITHUB_REPOSITORY")
CPU_FLAGS = ("avx2", "avx512f", "avx512_vnni", "amx_tile")
OS_RELEASE_KEYS = ("ID", "VERSION_ID", "PRETTY_NAME")


def _safe(probe: Callable[[], Any]) -> Any:
    try:
        return probe()
    except Exception:  # noqa: BLE001 - a host probe must never fail the shard; null says it was unavailable
        return None


def _os_release() -> dict[str, str | None]:
    values: dict[str, str | None] = dict.fromkeys(OS_RELEASE_KEYS)
    for line in Path("/etc/os-release").read_text(encoding="utf-8", errors="replace").splitlines():
        key, sep, value = line.partition("=")
        if sep and key in values:
            values[key] = value.strip().strip('"')
    return values


def _meminfo(name: str) -> int | None:
    for line in Path("/proc/meminfo").read_text(encoding="ascii", errors="replace").splitlines():
        if line.startswith(name + ":"):
            return int(line.split()[1]) * 1024
    return None


def _lscpu() -> dict[str, Any]:
    env = {"LC_ALL": "C", "PATH": os.environ.get("PATH", "/usr/bin:/bin")}
    r = subprocess.run(["lscpu"], capture_output=True, text=True, timeout=5, env=env, stdin=subprocess.DEVNULL)
    fields = {}
    for line in r.stdout.splitlines() if r.returncode == 0 else []:
        key, sep, value = line.partition(":")
        if sep:
            fields[key.strip()] = value.strip()

    def number(key: str) -> int | None:
        value = fields.get(key)
        return int(value) if value and value.isdigit() else None

    cores, sockets = number("Core(s) per socket"), number("Socket(s)")
    return {"model_name": fields.get("Model name"), "cores_per_socket": cores, "sockets": sockets,
            "threads_per_core": number("Thread(s) per core"),
            "physical_cores": cores * sockets if cores is not None and sockets is not None else None}


def _cpu_flags() -> dict[str, bool] | None:
    for line in Path("/proc/cpuinfo").read_text(encoding="ascii", errors="replace").splitlines():
        if line.startswith("flags"):
            flags = set(line.partition(":")[2].split())
            return {flag: flag in flags for flag in CPU_FLAGS}
    return None


def mem_available() -> int | None:
    """``MemAvailable`` in bytes, or None when ``/proc/meminfo`` cannot be read."""
    return _safe(lambda: _meminfo("MemAvailable"))


def physical_cores() -> int | None:
    """Cores per socket times sockets from ``lscpu``, or None when unknown."""
    cpu = _safe(_lscpu)
    return cpu["physical_cores"] if isinstance(cpu, dict) else None


def nproc_available() -> int:
    """The CPUs this process may run on; ``os.cpu_count()`` when affinity is unknown; at least 1."""
    return _safe(lambda: len(os.sched_getaffinity(0))) or os.cpu_count() or 1


def collect(disk_path: str | os.PathLike[str]) -> dict[str, Any]:
    cpu = _safe(_lscpu)
    return {
        "os_release": _safe(_os_release),
        "kernel": _safe(platform.release),
        "nproc_logical": _safe(os.cpu_count),
        "nproc_available": _safe(lambda: len(os.sched_getaffinity(0))),
        "mem_total_bytes": _safe(lambda: _meminfo("MemTotal")),
        "mem_available_bytes": mem_available(),
        "disk_free_bytes": _safe(lambda: shutil.disk_usage(disk_path).free),
        "cpu": cpu,
        "cpu_flags": _safe(_cpu_flags),
        "env": {name: os.environ.get(name) for name in ENV_NAMES},
        "python": sys.version,
    }
