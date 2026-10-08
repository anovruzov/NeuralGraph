"""Where a live result came from: machine, server build, model file, URL kind.

Every row ``run.py`` writes carries ``run_env`` (built here), so results from
a MacBook (llama.cpp + Metal) and from a Linux container (llama.cpp on CPU)
are distinguishable and reproducible.  Pure stdlib, Python 3.10+, macOS and
Linux.
"""
from __future__ import annotations

import ipaddress
import json
import os
import platform
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Dict, Optional


def _run(cmd) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def machine_info() -> Dict[str, object]:
    """Platform, CPU / chip, core count and memory of this machine."""
    cpu = ""
    mem_gb: Optional[float] = None
    if sys.platform == "darwin":
        cpu = _run(["sysctl", "-n", "machdep.cpu.brand_string"])
        m = _run(["sysctl", "-n", "hw.memsize"])
        mem_gb = round(int(m) / 2**30, 1) if m.isdigit() else None
        model = _run(["sysctl", "-n", "hw.model"])
    else:
        model = ""
        try:
            with open("/proc/cpuinfo") as fh:
                for ln in fh:
                    if ln.lower().startswith("model name"):
                        cpu = ln.split(":", 1)[1].strip()
                        break
            with open("/proc/meminfo") as fh:
                for ln in fh:
                    if ln.startswith("MemTotal"):
                        mem_gb = round(int(ln.split()[1]) / 2**20, 1)
                        break
        except OSError:
            pass
    return {"platform": platform.platform(), "system": platform.system(),
            "machine": platform.machine(), "cpu": cpu or platform.processor(),
            "hw_model": model, "n_cpus": os.cpu_count(), "mem_gb": mem_gb,
            "python": platform.python_version()}


def url_kind(url: Optional[str]) -> str:
    """loopback | private-network | remote | none."""
    if not url:
        return "none"
    host = urllib.parse.urlparse(url).hostname or ""
    if host in ("localhost",):
        return "loopback"
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return "remote"
    if ip.is_loopback:
        return "loopback"
    return "private-network" if ip.is_private else "remote"


def server_props(url: str, timeout: float = 10.0) -> Dict[str, object]:
    with urllib.request.urlopen(url.rstrip("/") + "/props", timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def preflight(url: str, role: str) -> Dict[str, object]:
    """Check that ``url`` is llama.cpp's llama-server (the harness needs its
    per-request GBNF ``grammar`` and ``chat_template_kwargs``) and return what
    it reports about itself.  Ollama and LM Studio expose OpenAI-compatible
    endpoints too, but ignore ``grammar``; a run against them would be
    unconstrained, so it is refused here."""
    try:
        p = server_props(url)
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise SystemExit(f"{role} endpoint {url} is not reachable as llama-server "
                         f"(GET /props failed: {e}). Start it with live/scripts/serve.sh "
                         f"or live/mac_setup.sh.")
    if "default_generation_settings" not in p or "build_info" not in p:
        raise SystemExit(f"{role} endpoint {url} does not look like llama.cpp's llama-server "
                         f"(no build_info in /props); this harness needs llama-server's "
                         f"grammar-constrained output.")
    return {"url": url, "url_kind": url_kind(url), "backend_kind": "llama-server",
            "llama_cpp": str(p.get("build_info", "")),
            "model_path": str(p.get("model_path", "")),
            "total_slots": int(p.get("total_slots", 0) or 0)}
