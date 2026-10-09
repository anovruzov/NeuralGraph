"""The narrow interface between HQ and one site: ``request(kind, body_bytes) -> body_bytes``.

Two kinds cross it (:data:`KINDS`):

* ``describe``: an empty body in, the site's capability descriptor out (canonical JSON in the closed schema
  :func:`descriptor_problem` checks). The descriptor holds exactly the fields of the coordination package's
  ``CapabilityDescriptor``; it is fixed per site and holds nothing derived from records;
* ``question``: the bytes of a pushdown question in, the bytes of the site's verdict out (both the shipped closed
  artifacts of ``edge/egress.py``, which the site's own Boundary checks).

Framing: one request is one line of canonical JSON ``{"body": <text>, "kind": <kind>}``; one response is one line
``{"body": <text>, "ok": true}`` or ``{"error": <code>, "ok": false}``. A body is UTF-8 text. An error code is one of
:data:`ERRORS` and never holds a value. The in-process endpoint passes every request and response through the same
framing as the child-process endpoint, so HQ receives bytes and only bytes in both modes.

Standard library only (and ``mycelic.collective.jsonio``, which is). Nothing here imports a site module.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from mycelic.collective.jsonio import StrictJsonError, canonical_bytes, canonical_dumps, sha256_hex, strict_load

KINDS = ("describe", "question")
ERRORS = ("bad_request", "unknown_kind", "refused", "error")
DESCRIPTOR_KEYS = ("availability", "capability_id", "description", "node_id", "policy_scope", "query_types")
DESCRIPTION = "Answers pushdown questions over this site's own records."
POLICY_SCOPE = ("pushdown:question",)
AVAILABILITIES = ("available", "degraded", "unavailable")
SITE_ID_RE = re.compile(r"[a-z0-9][a-z0-9_.-]{0,63}", re.ASCII)
HEX64_RE = re.compile(r"[0-9a-f]{64}", re.ASCII)
PACK_ID_RE = re.compile(r"[a-z][a-z0-9_]{0,63}", re.ASCII)
ROOT = Path(__file__).resolve().parents[2]


class WireError(Exception):
    """A frame that could not be read or a request the site refused. ``code`` is one of :data:`ERRORS` or
    ``closed``/``framing``; the text never holds a value."""

    def __init__(self, code: str) -> None:
        super().__init__(f"wire: {code}")
        self.code = code


# --------------------------------------------------------------------------------------------------- descriptor

def capability_id(pack_id: str, config_hash: str) -> str:
    return f"pushdown:{pack_id}:{config_hash}"


def descriptor(site_id: str, pack_id: str, config_hash: str, template_ids: Sequence[str]) -> dict[str, Any]:
    """The fixed descriptor of a site: no field is derived from the site's records."""
    return {"availability": "available", "capability_id": capability_id(pack_id, config_hash),
            "description": DESCRIPTION, "node_id": site_id, "policy_scope": list(POLICY_SCOPE),
            "query_types": sorted(template_ids)}


def descriptor_problem(obj: Any, *, site_id: str, pack_id: str, config_hash: str,
                       template_ids: Sequence[str]) -> tuple[str, str] | None:
    """The first problem ``(path, keyword)`` of a parsed descriptor against the closed schema, or None. Checked on
    both sides of the wire."""
    if not isinstance(obj, dict):
        return "$", "type"
    if sorted(obj) != list(DESCRIPTOR_KEYS):
        return "$", "keys"
    if obj["availability"] not in AVAILABILITIES:
        return "$.availability", "enum"
    if obj["capability_id"] != capability_id(pack_id, config_hash):
        return "$.capability_id", "const"
    if obj["description"] != DESCRIPTION:
        return "$.description", "const"
    if not isinstance(obj["node_id"], str) or SITE_ID_RE.fullmatch(obj["node_id"]) is None:
        return "$.node_id", "pattern"
    if obj["node_id"] != site_id:
        return "$.node_id", "const"
    if obj["policy_scope"] != list(POLICY_SCOPE):
        return "$.policy_scope", "const"
    if obj["query_types"] != sorted(template_ids):
        return "$.query_types", "const"
    return None


def parse_descriptor(data: bytes, *, site_id: str, pack_id: str, config_hash: str,
                     template_ids: Sequence[str]) -> dict[str, Any]:
    """A descriptor read from bytes, or :class:`WireError` (``refused``) when it is not canonical JSON in the closed
    schema."""
    try:
        obj = strict_load(data)
    except StrictJsonError:
        raise WireError("refused") from None
    if canonical_bytes(obj) != data:
        raise WireError("refused") from None
    if descriptor_problem(obj, site_id=site_id, pack_id=pack_id, config_hash=config_hash,
                          template_ids=template_ids) is not None:
        raise WireError("refused") from None
    return obj


# --------------------------------------------------------------------------------------------------- framing

def encode_request(kind: str, body: bytes) -> bytes:
    if kind not in KINDS:
        raise WireError("unknown_kind")
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        raise WireError("framing") from None
    return canonical_dumps({"body": text, "kind": kind}).encode("utf-8") + b"\n"


def decode_request(line: bytes) -> tuple[str, bytes]:
    try:
        obj = strict_load(line.rstrip(b"\n"))
    except StrictJsonError:
        raise WireError("bad_request") from None
    if not isinstance(obj, dict) or sorted(obj) != ["body", "kind"] or not isinstance(obj["body"], str):
        raise WireError("bad_request")
    if obj["kind"] not in KINDS:
        raise WireError("unknown_kind")
    return obj["kind"], obj["body"].encode("utf-8")


def encode_response(body: bytes | None = None, error: str | None = None) -> bytes:
    if (body is None) == (error is None):
        raise ValueError("give exactly one of body and error") from None
    if error is not None:
        if error not in ERRORS:
            raise ValueError("unknown error code") from None
        return canonical_dumps({"error": error, "ok": False}).encode("utf-8") + b"\n"
    return canonical_dumps({"body": body.decode("utf-8"), "ok": True}).encode("utf-8") + b"\n"


def decode_response(line: bytes) -> bytes:
    if not line:
        raise WireError("closed")
    try:
        obj = strict_load(line.rstrip(b"\n"))
    except StrictJsonError:
        raise WireError("framing") from None
    if isinstance(obj, dict) and sorted(obj) == ["body", "ok"] and obj["ok"] is True and isinstance(obj["body"], str):
        return obj["body"].encode("utf-8")
    if isinstance(obj, dict) and sorted(obj) == ["error", "ok"] and obj["ok"] is False and obj["error"] in ERRORS:
        raise WireError(obj["error"])
    raise WireError("framing")


def serve_line(handler: Callable[[str, bytes], bytes], line: bytes) -> bytes:
    """The site side of one exchange: decode, call ``handler(kind, body)``, encode. A handler that raises
    :class:`WireError` answers with its code; any other exception answers ``error``."""
    try:
        kind, body = decode_request(line)
    except WireError as err:
        return encode_response(error=err.code if err.code in ERRORS else "bad_request")
    try:
        return encode_response(body=handler(kind, body))
    except WireError as err:
        return encode_response(error=err.code if err.code in ERRORS else "error")
    except Exception:            # nothing of a site-side failure crosses
        return encode_response(error="error")


# --------------------------------------------------------------------------------------------------- endpoints

class _Logged:
    """Every frame HQ sent or received, appended to ``hq/wire.jsonl`` (so the leakage scan reads every byte that
    crossed)."""

    def __init__(self, site_id: str, log_path: str | Path | None) -> None:
        self.site_id = site_id
        self.log_path = Path(log_path) if log_path is not None else None
        self._lock = threading.Lock()

    def log(self, direction: str, frame: bytes) -> None:
        if self.log_path is None:
            return
        row = canonical_dumps({"bytes": len(frame), "direction": direction, "frame": frame.decode("utf-8"),
                               "sha256": sha256_hex(frame), "site": self.site_id})
        with self._lock, open(self.log_path, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(row + "\n")


class InProcessEndpoint(_Logged):
    """A site object built by ``factory()`` on its own worker thread; every request runs on that thread, so the
    site's SQLite connections and event loop never cross threads. HQ holds no handle into the site object."""

    def __init__(self, site_id: str, factory: Callable[[], Any], *, log_path: str | Path | None = None) -> None:
        super().__init__(site_id, log_path)
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"site-{site_id}")
        self.__site = self._pool.submit(factory).result()

    def request(self, kind: str, body: bytes = b"") -> bytes:
        frame = encode_request(kind, body)
        self.log("request", frame)
        answer = self._pool.submit(serve_line, self.__site.handle, frame).result()
        self.log("response", answer)
        return decode_response(answer)

    def close(self) -> None:
        try:
            self._pool.submit(self.__site.close).result()
        finally:
            self._pool.shutdown(wait=True)


class ChildProcessEndpoint(_Logged):
    """``python -m research.routing_spike.site_process --config <file>``: one JSON request per line on stdin, one
    response per line on stdout. The child opens its own record store and graph."""

    def __init__(self, site_id: str, config_path: str | Path, *, log_path: str | Path | None = None,
                 python: str | None = None) -> None:
        super().__init__(site_id, log_path)
        env = dict(os.environ)
        env["PYTHONPATH"] = str(ROOT) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        self._proc = subprocess.Popen([python or sys.executable, "-m", "research.routing_spike.site_process",
                                       "--config", str(config_path)], cwd=str(ROOT), env=env,
                                      stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self._io = threading.Lock()

    def request(self, kind: str, body: bytes = b"") -> bytes:
        frame = encode_request(kind, body)
        with self._io:
            self.log("request", frame)
            assert self._proc.stdin is not None and self._proc.stdout is not None
            try:
                self._proc.stdin.write(frame)
                self._proc.stdin.flush()
            except OSError:
                raise WireError("closed") from None
            answer = self._proc.stdout.readline()
            self.log("response", answer)
        return decode_response(answer)

    def close(self) -> None:
        if self._proc.stdin is not None:
            try:
                self._proc.stdin.close()
            except OSError:
                pass
        try:
            self._proc.wait(timeout=60)
        except subprocess.TimeoutExpired:
            self._proc.kill()
            self._proc.wait()
        if self._proc.stdout is not None:
            self._proc.stdout.close()


def read_frames(path: str | Path) -> list[Mapping[str, Any]]:
    """The rows of a wire log; [] for an absent file."""
    path = Path(path)
    if not path.exists():
        return []
    return [strict_load(line) for line in path.read_bytes().split(b"\n") if line]
