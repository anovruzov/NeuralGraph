"""The lab's model manifest and its lock: which models a request may name, and the pins that were trusted.

``lab/models.json`` (unknown keys are refused at every level)::

    {"schema_version": 1,
     "hf_base": "https://<host>",                      # https, no path, query or credentials
     "server": null | {"program": "<name>", "tag": "<release tag>", "asset": "<file>.tar.gz",
                       "url": "https://github.com/<owner>/<repo>/releases/download/<tag>/<asset>",
                       "archive_root": "<relative dir or empty>", "binary": "<relative path>", "format": "tar.gz",
                       "args": [], "cache_ram_mib": 1024, "threads": "physical" | "logical"},
     "models": {"<key>": {"kind": "fake", "alias": "<served name>", "persona": "valid",
                          "response_format": "json_schema", "transport_schema": "full" | "reduced"}
                         | {"kind": "gguf", "alias": "<served name>",
                            "gguf": {"repo": "<owner>/<name>", "file": "<path>.gguf", "revision": "<40 hex>",
                                     "license": "apache-2.0" | "mit"},
                            "response_format": "json_schema" | "json_object" | "none",
                            "transport_schema": "full" | "reduced", "ctx_per_slot": 16384,
                            "e3_ctx_per_slot": 4096, "e2_ctx_per_slot": 32768, "server_args": []}}}

``server`` is required when any gguf model is listed; a request's provider is ``fake`` or ``server.program``.
Model keys match ``[a-z0-9][a-z0-9-]{0,23}``; ``none`` is reserved for model-free shard groups. A fake entry must
use ``json_schema``, because the fake responder dispatches on the schema name. ``args`` and ``server_args`` must be
empty: server arguments are allowlisted once the pinned binary's options have been read (G2).

The lock sits next to the manifest as ``<stem>.lock.json``::

    {"schema_version": 1, "server": null | {"tag": "<tag>", "asset": "<asset>", "sha256": "<64 hex>"},
     "models": {"<gguf key>": {"repo": "...", "file": "...", "commit": "<40 hex>", "sha256": "<64 hex>",
                               "size": <bytes>}}}

A lock entry must match the manifest (server tag and asset; model repo, file and revision), else the manifest was
changed without re-pinning. A missing entry is allowed: the first download pins it (trust on first use, G2).

Errors are :class:`ManifestError`; a lock error's path starts with ``lock `` (``lock $.models``). No message holds a
manifest value.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from mycelic.collective.inference.fakeserver import PERSONAS
from mycelic.collective.jsonio import StrictJsonError, sha256_hex, strict_load

from . import LabError, check_keys, display_path, safe_path

MAX_BYTES = 1048576
MODEL_KEY_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,23}", re.ASCII)
RESERVED_KEY = "none"
PROGRAM_RE = re.compile(r"[a-z][a-z0-9-]{0,31}", re.ASCII)
TAG_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", re.ASCII)
ASSET_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\.tar\.gz", re.ASCII)
SEGMENT_RE = re.compile(r"[A-Za-z0-9._-]+", re.ASCII)
ALIAS_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,127}", re.ASCII)
REPO_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}/[A-Za-z0-9][A-Za-z0-9._-]{0,95}", re.ASCII)
REVISION_RE = re.compile(r"[0-9a-f]{40}", re.ASCII)
SHA256_RE = re.compile(r"[0-9a-f]{64}", re.ASCII)
PERMISSIVE_LICENSES = ("apache-2.0", "mit")
KINDS = ("gguf", "fake")
RESPONSE_FORMATS = ("json_schema", "json_object", "none")
TRANSPORT_SCHEMAS = ("full", "reduced")
THREADS = ("physical", "logical")
CTX_DEFAULTS = {"ctx_per_slot": 16384, "e3_ctx_per_slot": 4096, "e2_ctx_per_slot": 32768}
CTX_RANGE = (2048, 131072)

_TOP_KEYS = ("schema_version", "hf_base", "server", "models")
_SERVER_KEYS = ("program", "tag", "asset", "url", "archive_root", "binary", "format", "args", "cache_ram_mib",
                "threads")
_SERVER_REQUIRED = _SERVER_KEYS[:8]
_FAKE_KEYS = ("kind", "alias", "persona", "response_format", "transport_schema")
_GGUF_KEYS = ("kind", "alias", "gguf", "response_format", "transport_schema", *CTX_DEFAULTS, "server_args")
_GGUF_REF_KEYS = ("repo", "file", "revision", "license")
_LOCK_KEYS = ("schema_version", "server", "models")
_LOCK_SERVER_KEYS = ("tag", "asset", "sha256")
_LOCK_MODEL_KEYS = ("repo", "file", "commit", "sha256", "size")
REPIN = "does not match the manifest (re-pin)"
NO_ARGS = "must be [] (server arguments are allowlisted in G2)"


class ManifestError(LabError):
    @property
    def source(self) -> str:
        return "lock" if self.path.startswith("lock ") else "manifest"

    @property
    def json_path(self) -> str:
        return self.path[len("lock "):] if self.path.startswith("lock ") else self.path


@dataclass(frozen=True)
class Lock:
    path: str
    sha256: str
    server: dict[str, Any] | None
    models: dict[str, dict[str, Any]]


@dataclass(frozen=True)
class Manifest:
    path: str
    sha256: str
    hf_base: str
    server: dict[str, Any] | None
    models: dict[str, dict[str, Any]]
    lock: Lock

    def providers(self) -> tuple[str, ...]:
        return ("fake",) if self.server is None else ("fake", self.server["program"])


def lock_path(manifest_path: str | Path) -> Path:
    path = Path(manifest_path)
    return path.with_name(f"{path.stem}.lock.json")


# --------------------------------------------------------------------------------------------------- leaves

def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _match(value: Any, pattern: re.Pattern[str], path: str, problem: str) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise ManifestError(path, problem) from None
    return value


def _choice(value: Any, choices: tuple[str, ...], path: str) -> str:
    if not isinstance(value, str) or value not in choices:
        raise ManifestError(path, f"must be one of {', '.join(choices)}") from None
    return value


def _int(value: Any, low: int, high: int, path: str) -> int:
    if not _is_int(value) or not low <= value <= high:
        raise ManifestError(path, f"must be an int in [{low}, {high}]") from None
    return value


def _object(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ManifestError(path, "must be an object") from None
    return value


def _rel_path(value: Any, path: str, *, allow_empty: bool) -> str:
    if value == "" and allow_empty:
        return value
    ok = isinstance(value, str) and value != "" and all(
        SEGMENT_RE.fullmatch(s) is not None and s not in (".", "..") for s in value.split("/"))
    if not ok:
        raise ManifestError(path, "must be a relative POSIX path of [A-Za-z0-9._-] segments") from None
    return value


def _empty_list(value: Any, path: str) -> list[Any]:
    if value != [] or not isinstance(value, list):
        raise ManifestError(path, NO_ARGS) from None
    return []


def _printable(value: str) -> bool:
    return all(0x21 <= ord(c) <= 0x7e for c in value)


def _hf_base(value: Any) -> str:
    path = "$.hf_base"
    problem = "must be an https URL with no path, query or credentials"
    if not isinstance(value, str) or not _printable(value) or "?" in value or "#" in value:
        raise ManifestError(path, problem) from None
    parts = urlsplit(value)
    try:
        port_ok = parts.port is None or parts.port > 0
    except ValueError:
        port_ok = False
    if (parts.scheme != "https" or not parts.hostname or "@" in parts.netloc or parts.path not in ("", "/")
            or not port_ok):
        raise ManifestError(path, problem) from None
    return value.rstrip("/")


def _server_url(value: Any, tag: str, asset: str) -> str:
    path = "$.server.url"
    problem = "must be https://github.com/<owner>/<repo>/releases/download/<tag>/<asset>"
    if not isinstance(value, str) or not _printable(value) or "?" in value or "#" in value:
        raise ManifestError(path, problem) from None
    parts = urlsplit(value)
    pattern = rf"/[A-Za-z0-9._-]+/[A-Za-z0-9._-]+/releases/download/{re.escape(tag)}/{re.escape(asset)}"
    if parts.scheme != "https" or parts.netloc != "github.com" or re.fullmatch(pattern, parts.path) is None:
        raise ManifestError(path, problem) from None
    return value


# --------------------------------------------------------------------------------------------------- manifest

def _server(raw: Any) -> dict[str, Any] | None:
    if raw is None:
        return None
    path = "$.server"
    raw = _object(raw, path)
    check_keys(raw, _SERVER_KEYS, _SERVER_REQUIRED, path, ManifestError)
    program = _match(raw["program"], PROGRAM_RE, f"{path}.program", "must match [a-z][a-z0-9-]{0,31}")
    tag = _match(raw["tag"], TAG_RE, f"{path}.tag", "must be a release tag of [A-Za-z0-9._-]")
    asset = _match(raw["asset"], ASSET_RE, f"{path}.asset", "must be a .tar.gz file name of [A-Za-z0-9._-]")
    return {
        "program": program, "tag": tag, "asset": asset, "url": _server_url(raw["url"], tag, asset),
        "archive_root": _rel_path(raw["archive_root"], f"{path}.archive_root", allow_empty=True),
        "binary": _rel_path(raw["binary"], f"{path}.binary", allow_empty=False),
        "format": _choice(raw["format"], ("tar.gz",), f"{path}.format"),
        "args": _empty_list(raw["args"], f"{path}.args"),
        "cache_ram_mib": _int(raw.get("cache_ram_mib", 1024), 0, 65536, f"{path}.cache_ram_mib"),
        "threads": _choice(raw.get("threads", "physical"), THREADS, f"{path}.threads"),
    }


def _model(key: str, raw: Any) -> dict[str, Any]:
    path = f"$.models.{key}"
    raw = _object(raw, path)
    kind = raw.get("kind")
    if kind not in KINDS:
        raise ManifestError(f"{path}.kind", f"must be one of {', '.join(KINDS)}") from None
    required = ("kind", "alias", "gguf") if kind == "gguf" else ("kind", "alias")
    check_keys(raw, _GGUF_KEYS if kind == "gguf" else _FAKE_KEYS, required, path, ManifestError)
    entry = {
        "kind": kind,
        "alias": _match(raw["alias"], ALIAS_RE, f"{path}.alias", "must match [A-Za-z0-9][A-Za-z0-9._:/@+-]{0,127}"),
        "response_format": _choice(raw.get("response_format", "json_schema"), RESPONSE_FORMATS,
                                   f"{path}.response_format"),
        "transport_schema": _choice(raw.get("transport_schema", "full"), TRANSPORT_SCHEMAS,
                                    f"{path}.transport_schema"),
    }
    if kind == "fake":
        if entry["response_format"] != "json_schema":
            raise ManifestError(f"{path}.response_format",
                                "a fake model needs json_schema (the responder dispatches on the schema name)")
        entry["persona"] = _choice(raw.get("persona", "valid"), PERSONAS, f"{path}.persona")
        return entry
    ref_path = f"{path}.gguf"
    ref = _object(raw["gguf"], ref_path)
    check_keys(ref, _GGUF_REF_KEYS, _GGUF_REF_KEYS, ref_path, ManifestError)
    gguf_file = _rel_path(ref["file"], f"{ref_path}.file", allow_empty=False)
    if not gguf_file.endswith(".gguf"):
        raise ManifestError(f"{ref_path}.file", "must end with .gguf") from None
    entry["gguf"] = {
        "repo": _match(ref["repo"], REPO_RE, f"{ref_path}.repo", "must be <owner>/<name> of [A-Za-z0-9._-]"),
        "file": gguf_file,
        "revision": _match(ref["revision"], REVISION_RE, f"{ref_path}.revision", "must be a 40-hex commit id"),
        "license": _choice(ref["license"], PERMISSIVE_LICENSES, f"{ref_path}.license"),
    }
    for name, default in CTX_DEFAULTS.items():
        entry[name] = _int(raw.get(name, default), *CTX_RANGE, f"{path}.{name}")
    entry["server_args"] = _empty_list(raw.get("server_args", []), f"{path}.server_args")
    return entry


def _models(raw: Any) -> dict[str, dict[str, Any]]:
    raw = _object(raw, "$.models")
    if not raw:
        raise ManifestError("$.models", "must list at least one model") from None
    models = {}
    for key in sorted(raw):
        if key == RESERVED_KEY:
            raise ManifestError("$.models.none", "reserved for model-free shard groups") from None
        if MODEL_KEY_RE.fullmatch(key) is None:
            raise ManifestError("$.models", "model keys must match [a-z0-9][a-z0-9-]{0,23} (name not shown)") \
                from None
        models[key] = _model(key, raw[key])
    return models


def parse_manifest(obj: Any) -> tuple[str, dict[str, Any] | None, dict[str, dict[str, Any]]]:
    obj = _object(obj, "$")
    check_keys(obj, _TOP_KEYS, _TOP_KEYS, "$", ManifestError)
    if not _is_int(obj["schema_version"]) or obj["schema_version"] != 1:
        raise ManifestError("$.schema_version", "must be 1") from None
    hf_base = _hf_base(obj["hf_base"])
    server = _server(obj["server"])
    models = _models(obj["models"])
    if server is None and any(m["kind"] == "gguf" for m in models.values()):
        raise ManifestError("$.server", "required when a gguf model is listed") from None
    return hf_base, server, models


# --------------------------------------------------------------------------------------------------- lock

def parse_lock(obj: Any, server: dict[str, Any] | None,
               models: dict[str, dict[str, Any]]) -> tuple[dict[str, Any] | None, dict[str, dict[str, Any]]]:
    obj = _object(obj, "$")
    check_keys(obj, _LOCK_KEYS, _LOCK_KEYS, "$", ManifestError)
    if not _is_int(obj["schema_version"]) or obj["schema_version"] != 1:
        raise ManifestError("$.schema_version", "must be 1") from None
    locked_server = None
    if obj["server"] is not None:
        raw = _object(obj["server"], "$.server")
        check_keys(raw, _LOCK_SERVER_KEYS, _LOCK_SERVER_KEYS, "$.server", ManifestError)
        if server is None or raw["tag"] != server["tag"] or raw["asset"] != server["asset"]:
            raise ManifestError("$.server", REPIN) from None
        locked_server = {"tag": raw["tag"], "asset": raw["asset"],
                         "sha256": _match(raw["sha256"], SHA256_RE, "$.server.sha256", "must be 64 hex")}
    raw_models = _object(obj["models"], "$.models")
    locked: dict[str, dict[str, Any]] = {}
    for key in sorted(raw_models):
        entry = models.get(key)
        if entry is None or entry["kind"] != "gguf":
            raise ManifestError("$.models", f"{REPIN}: a locked key is not a gguf model of the manifest") from None
        path = f"$.models.{key}"
        raw = _object(raw_models[key], path)
        check_keys(raw, _LOCK_MODEL_KEYS, _LOCK_MODEL_KEYS, path, ManifestError)
        ref = entry["gguf"]
        if raw["repo"] != ref["repo"] or raw["file"] != ref["file"] or raw["commit"] != ref["revision"]:
            raise ManifestError(path, REPIN) from None
        locked[key] = {"repo": ref["repo"], "file": ref["file"], "commit": ref["revision"],
                       "sha256": _match(raw["sha256"], SHA256_RE, f"{path}.sha256", "must be 64 hex"),
                       "size": _int(raw["size"], 1, 2 ** 63 - 1, f"{path}.size")}
    return locked_server, locked


# --------------------------------------------------------------------------------------------------- files

def _read(path: Path) -> tuple[bytes, Any]:
    try:
        data = path.read_bytes()
    except OSError:
        raise ManifestError("$", "cannot read the file") from None
    if len(data) > MAX_BYTES:
        raise ManifestError("$", "larger than 1 MiB") from None
    try:
        obj = strict_load(data)
    except StrictJsonError as err:
        raise ManifestError(safe_path(err.path), f"invalid JSON ({err.reason})") from None
    return data, obj


def load_manifest(path: str | Path) -> Manifest:
    path = Path(path)
    data, obj = _read(path)
    hf_base, server, models = parse_manifest(obj)
    lpath = lock_path(path)
    try:
        lock_data, lock_obj = _read(lpath)
        locked_server, locked_models = parse_lock(lock_obj, server, models)
    except ManifestError as err:
        raise ManifestError(f"lock {err.path}", err.problem) from None
    lock = Lock(path=display_path(lpath), sha256=sha256_hex(lock_data), server=locked_server, models=locked_models)
    return Manifest(path=display_path(path), sha256=sha256_hex(data), hf_base=hf_base, server=server, models=models,
                    lock=lock)
