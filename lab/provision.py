"""Provision a run's pinned files once, verified, and prepare a shard from the records that says so.

    python -m lab.provision server --plan P --manifest M --cache-root C --out D [--cache-matched-key K]
        [--github-output F]
    python -m lab.provision gguf --model KEY --plan P --manifest M --cache-root C --out D [--cache-matched-key K]
        [--github-output F]
    python -m lab.provision merge-lock --manifest M --candidates DIR --out FILE

The workflow's provision matrix (``plan["provision"]``) runs one ``server`` entry and one ``gguf`` entry per model.
Each downloads and verifies its file exactly once per run, keeps it in the cache root under a key derived from its
sha256 (``manifest.cache_key``; actions/cache restores it by that key or by the key's prefix, ``--cache-matched-key``
says what was restored) and writes ``D/provision.json``, the record that is authoritative for the run. The lock is
always the manifest's sibling, and the manifest and lock must still hash to what the plan recorded.

``server``: with the server pinned in the lock, the lock's sha256 is expected and the GitHub API is not called;
otherwise ``GET https://<API_HOST>/repos/<owner>/<repo>/releases/tags/<tag>`` (:data:`API_HOST`; the only request
that may carry ``Authorization: Bearer $GH_TOKEN``, and only to that host) gives the asset's published ``digest``.
When the API is rate limited, failing or refusing the token, or the asset has no digest, the file is trusted on
first use: recorded as
``first-use`` with ``digest unavailable`` or ``no digest published``, never trusted from the cache, and pinned by the
lock candidate. The tarball is extracted to a fresh temporary directory (:func:`extract_server`: members checked
before ``extractall(filter="data")``) and ``<binary> --version`` must exit 0 with the four-variable environment; a
binary the kernel will not run (another machine's, an empty file) fails the same way.

``gguf``: ``GET <hf_base>/api/models/<repo>/revision/<lock commit or manifest revision>?blobs=true`` (anonymous)
must name a 40-hex commit (the lock's when locked), ``gated: false``, the manifest's licence, the file, and its
``lfs.sha256`` and ``lfs.size``; the file is downloaded from ``<hf_base>/<repo>/resolve/<commit>/<file>``, so every
shard of the run uses the same commit even when the revision is a branch, and must start with ``GGUF``.

Every download goes to ``<final>.part``, is verified (one full retry after a digest mismatch) and only then renamed;
on any failure nothing is left at the final path, and a cache directory only ever holds its one verified file.
Preflight: free disk in the cache root of twice the file plus 1 GiB, and (gguf) available memory of the file plus
2 GiB. ``D/provision.json`` is always written; ``D/lock-candidate.json`` (the lock format with only this target) only
when verified; ``--github-output`` gets ``verified``, ``sha256``, ``save_key`` and ``save`` (save only when verified
and the restored key is not already the save key). stdout is exactly one line::

    lab: provision <server|gguf> <key|-> verified <true|false> by <verified_by|none> sha256 <16 hex|none>
         source <cache|download|none> wall_s <s>

Exit 0 verified; 2 for a pin, verification, preflight, archive or binary failure and a refused, wrongly sized or
disk-full download; 3 for a network failure, an exceeded deadline, or a hub API that stays unavailable.

``merge-lock`` merges every ``lock-candidate.json`` under ``DIR`` (recursively) into the manifest's lock, by key and
independent of order; two values for one key (between candidates, or against the lock) exit 2. The result is
validated against the manifest and written as indented JSON for the team to commit.

:func:`prepare` is ``python -m lab.shard prepare`` (see ``shard.py``): it restores and re-verifies both files of a
gguf shard against the records (re-downloading from the record's URL when the cache is missing or corrupt), extracts
the server fresh into ``OUT/server/bin`` and writes ``OUT/provision/prepare.json``.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import zlib
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping
from urllib.parse import quote, urlsplit

from mycelic.collective.experiments.common import utc_clock, write_json_atomic
from mycelic.collective.jsonio import StrictJsonError, sha256_hex, strict_load

from . import EXIT_NETWORK, EXIT_OK, EXIT_USAGE, forbidden_root, gh_data, hostinfo
from .download import DownloadError, JsonReply, UrlMap, download, get_json, retry_delay, sha256_file
from .manifest import (ASSET_RE, COMMIT_RE, MODEL_KEY_RE, SEGMENT_RE, SHA256_RE, TAG_RE, Manifest, ManifestError,
                       cache_dir, cache_key, load_manifest, lock_path, parse_lock)
from .notes import (AMBIGUOUS_RECORDS, ASSET_MISSING, ASSET_NOT_UPLOADED, BINARY_MISSING, CACHE_INSIDE_OUT,
                    DIGEST_MISMATCH, DIGEST_UNAVAILABLE, FILE_MISSING, FILE_TOO_LARGE, FORBIDDEN_ROOT,
                    HUB_BAD_COMMIT, HUB_COMMIT_DIFFERS, HUB_DIFFERS_FROM_LOCK, HUB_REDIRECTED, HUB_REFUSED,
                    HUB_STATUS, HUB_UNAVAILABLE, LICENSE_DIFFERS, LOCK_CONFLICT, LOCK_MISMATCH, MODEL_NOT_IN_PLAN,
                    NO_DIGEST, NO_GGUF_IN_PLAN, NO_HUB_DIGEST, NOT_A_PLAN, NOT_ENOUGH_DISK, NOT_ENOUGH_MEMORY,
                    NOT_GGUF, NOT_GZIP_TAR, OUT_NOT_PREPARABLE, PLAN_CHANGED, PROVISION_FAILED_MODEL,
                    PROVISION_FAILED_SERVER, RECORD_OTHER_PLAN, RELEASE_TAG_MISSING, REPO_GATED, UNSAFE_MEMBER,
                    VERSION_FAILED)
from .plan import SHARD_ID_RE, write_github_output
from .server import run_version, server_env

API_HOST = "api.github.com"
API_VERSION = "2022-11-28"
API_TRIES = 3
ASSET_PAGES = 10
ASSETS_PER_PAGE = 100
MAX_GGUF_BYTES = 12 << 30
GGUF_MAGIC = b"GGUF"
MAX_MEMBERS = 4000
MAX_ARCHIVE_BYTES = 2 << 30
GIB = 1 << 30
MIB = 1 << 20
NAME_SHOWN_RE = re.compile(r"[A-Za-z0-9._/-]{1,200}", re.ASCII)
DIGEST_RE = re.compile(r"sha256:([0-9a-f]{64})", re.ASCII)
MAX_NAMES_SHOWN = 50
MAX_LIBRARIES = 100
RECORD_DEPTH = 3
VERIFIED_BY = ("lock", "hf-api", "github-api", "first-use")
TARGETS = ("server", "gguf")


class ProvisionError(Exception):
    """A fixed problem (a notes sentence, numbers and sanitised names rendered in) and the exit code it maps to."""

    def __init__(self, problem: str, code: int = EXIT_USAGE) -> None:
        super().__init__(problem)
        self.problem = problem
        self.code = code


def run_attempt(environ: Mapping[str, str]) -> int:
    value = environ.get("GITHUB_RUN_ATTEMPT", "")
    return int(value) if re.fullmatch(r"[0-9]{1,6}", value) and int(value) >= 1 else 1


def disk_free(path: str | os.PathLike[str]) -> int:
    return shutil.disk_usage(path).free


def mem_available() -> int | None:
    return hostinfo.mem_available()


def _mib(n: int) -> int:
    return n // MIB


def _remove(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path, ignore_errors=True)


def _keep_only(directory: Path, name: str) -> None:
    """Delete every entry of a cache directory but ``name``."""
    if directory.is_dir():
        for entry in directory.iterdir():
            if entry.name != name:
                _remove(entry)


# --------------------------------------------------------------------------------------------------- archives

def _inside(path: str, base: str) -> bool:
    return path == base or path.startswith(base.rstrip("/") + "/")


def extract_server(tarball: Path, dest: Path, archive_root: str, binary: str) -> Path:
    """Check every member of the gzip tarball, extract it under ``dest`` with the ``data`` filter and return the
    binary ``dest/<archive_root>/<binary>``. The tree is never moved afterwards: the release's rpath is ``$ORIGIN``."""
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    base = os.path.normpath(os.path.join(os.path.abspath(dest), archive_root)) if archive_root else \
        os.path.abspath(dest)
    root = PurePosixPath(archive_root) if archive_root else None
    try:
        archive = tarfile.open(tarball, mode="r:gz")
    except (tarfile.TarError, EOFError, OSError, zlib.error):
        raise ProvisionError(NOT_GZIP_TAR) from None
    with archive:
        members: list[tarfile.TarInfo] = []
        total = 0
        try:
            while True:
                member = archive.next()
                if member is None:
                    break
                members.append(member)
                total += max(member.size, 0)
                if len(members) > MAX_MEMBERS or total > MAX_ARCHIVE_BYTES:
                    raise ProvisionError(UNSAFE_MEMBER)
                name = PurePosixPath(member.name)
                norm = PurePosixPath(*[p for p in name.parts if p != "."]) if name.parts else name
                top = norm == PurePosixPath(".") and member.isdir()
                safe = not name.is_absolute() and ".." not in name.parts
                safe = safe and (root is None or top or norm == root or root in norm.parents)
                safe = safe and (member.isfile() or member.isdir() or member.issym() or member.islnk())
                if safe and member.issym():
                    target = os.path.normpath(os.path.join(os.path.abspath(dest), str(norm.parent), member.linkname))
                    safe = not os.path.isabs(member.linkname) and _inside(target, base)
                if safe and member.islnk():
                    target = os.path.normpath(os.path.join(os.path.abspath(dest), member.linkname))
                    safe = not os.path.isabs(member.linkname) and _inside(target, base)
                if not safe:
                    raise ProvisionError(UNSAFE_MEMBER)
            if not members:
                raise ProvisionError(NOT_GZIP_TAR)
            archive.extractall(dest, members=members, filter="data")
        except (tarfile.TarError, EOFError, zlib.error):
            raise ProvisionError(NOT_GZIP_TAR) from None
        except OSError:
            raise ProvisionError(UNSAFE_MEMBER) from None
    path = Path(base) / binary
    try:
        regular = stat.S_ISREG(os.lstat(path).st_mode)
    except OSError:
        regular = False
    if not regular or not os.access(path, os.X_OK):
        raise ProvisionError(BINARY_MISSING)
    return path


def libraries(tree: Path) -> list[str]:
    names = sorted({p.name for p in tree.rglob("*") if p.name.endswith(".so") or ".so." in p.name})
    return names[:MAX_LIBRARIES]


def check_version(binary: Path, scratch: Path, environ: Mapping[str, str]) -> str:
    home, tmp = scratch / "home", scratch / "tmp"
    for directory in (home, tmp):
        directory.mkdir(parents=True, exist_ok=True)
    try:
        code, line = run_version(binary, server_env(environ, str(home), str(tmp)), tmp)
    except (OSError, subprocess.SubprocessError):  # the kernel would not run it: ENOEXEC (another machine, empty)
        raise ProvisionError(VERSION_FAILED) from None
    if code != 0:
        raise ProvisionError(VERSION_FAILED)
    return line


# --------------------------------------------------------------------------------------------------- one session

@dataclass
class _Session:
    """What one provision or prepare call did on the network and how long it took."""

    url_map: UrlMap | None
    sleep: Callable[[float], Any]
    environ: Mapping[str, str]
    connections: list[dict[str, Any]] = field(default_factory=list)
    timings: dict[str, float] = field(default_factory=lambda: {"api_s": 0.0, "download_s": 0.0, "hash_s": 0.0,
                                                               "extract_s": 0.0, "total_s": 0.0})
    downloads: dict[str, Any] | None = None

    def add(self, name: str, t0: float) -> None:
        self.timings[name] = round(self.timings[name] + time.monotonic() - t0, 3)

    def fetch(self, url: str, final: Path, expected: str | None, expected_size: int | None, *, trust_cache: bool,
              mismatch: str, magic: bytes | None = None) -> tuple[str, str, int]:
        """(source, sha256, size) of ``final``: a cached file that hashes to ``expected`` (when the cache may be
        trusted), else a fresh download verified against ``expected`` (one full retry) and renamed into place."""
        final.parent.mkdir(parents=True, exist_ok=True)
        if final.exists() or final.is_symlink():
            if trust_cache and expected is not None and final.is_file() and not final.is_symlink():
                t0 = time.monotonic()
                sha, size = sha256_file(final)
                self.add("hash_s", t0)
                if sha == expected and (magic is None or _starts_with(final, magic)):
                    return "cache", sha, size
            _remove(final)
        part = final.with_name(final.name + ".part")
        record = {"attempts": 0, "resumed_from": [], "bytes": 0, "wall_s": 0.0, "verify_retries": 0}
        self.downloads = record
        while True:
            t0 = time.monotonic()
            try:
                got = download(url, part, expected_size=expected_size, url_map=self.url_map, environ=self.environ,
                               sleep=self.sleep)
            except DownloadError as err:
                self.connections += err.connections
                self.add("download_s", t0)
                raise
            self.add("download_s", t0)
            self.connections += got.connections
            record["attempts"] += got.attempts
            record["resumed_from"] += got.resumed_from
            record["bytes"] = got.size
            record["wall_s"] = round(record["wall_s"] + got.wall_s, 3)
            if expected is None or got.sha256 == expected:
                break
            _remove(part)
            if record["verify_retries"] >= 1:
                raise ProvisionError(mismatch)
            record["verify_retries"] += 1
        if magic is not None and not _starts_with(part, magic):
            _remove(part)
            raise ProvisionError(NOT_GGUF)
        os.replace(part, final)
        return "download", got.sha256, got.size


def _starts_with(path: Path, magic: bytes) -> bool:
    try:
        with open(path, "rb") as fh:
            return fh.read(len(magic)) == magic
    except OSError:
        return False


def _download_error(err: DownloadError) -> ProvisionError:
    return ProvisionError(err.problem, EXIT_NETWORK if err.kind in ("network", "deadline") else EXIT_USAGE)


# --------------------------------------------------------------------------------------------------- the GitHub API

def api_headers(url: str, environ: Mapping[str, str]) -> dict[str, str]:
    """The release API headers; the workflow token only to the API host itself."""
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": API_VERSION,
               "User-Agent": "mycelic-lab"}
    token = environ.get("GH_TOKEN", "")
    if token and urlsplit(url).hostname == API_HOST:
        headers["Authorization"] = "Bearer " + token
    return headers


def _api(session: _Session, url: str) -> JsonReply | None:
    """The reply for 200 (parsed) or 404; None when the API stays unavailable (rate limited, 5xx, network: up to
    :data:`API_TRIES` tries) or refuses (401, anything else)."""
    for attempt in range(1, API_TRIES + 1):
        t0 = time.monotonic()
        reply = get_json(url, headers=api_headers(url, session.environ), url_map=session.url_map,
                         environ=session.environ)
        session.add("api_s", t0)
        session.connections.append(reply.connection)
        if (reply.status == 200 and reply.obj is not None) or reply.status == 404:
            return reply
        limited = reply.status in (403, 429) and (reply.headers.get("x-ratelimit-remaining") == "0"
                                                   or "retry-after" in reply.headers)
        transient = reply.status is None or reply.status >= 500 or limited
        if not transient:
            return None
        if attempt < API_TRIES:
            session.sleep(retry_delay(attempt, reply.headers.get("retry-after")))
    return None


def _find_asset(assets: Any, name: str) -> dict[str, Any] | None:
    if isinstance(assets, list):
        for asset in assets:
            if isinstance(asset, dict) and asset.get("name") == name:
                return asset
    return None


def _release_digest(session: _Session, server: Mapping[str, Any]) -> tuple[str | None, int | None, str | None,
                                                                          str | None, str]:
    """(expected sha256, size, digest, digest_note, verified_by) from the release API."""
    owner, repo = urlsplit(server["url"]).path.split("/")[1:3]
    base = f"https://{API_HOST}/repos/{quote(owner)}/{quote(repo)}/releases"
    reply = _api(session, f"{base}/tags/{quote(server['tag'], safe='')}")
    if reply is None:
        return None, None, None, DIGEST_UNAVAILABLE, "first-use"
    if reply.status == 404 or not isinstance(reply.obj, dict):
        raise ProvisionError(RELEASE_TAG_MISSING)
    asset = _find_asset(reply.obj.get("assets"), server["asset"])
    release_id = reply.obj.get("id")
    if asset is None and isinstance(release_id, int) and not isinstance(release_id, bool):
        for page in range(1, ASSET_PAGES + 1):
            listing = _api(session, f"{base}/{release_id}/assets?per_page={ASSETS_PER_PAGE}&page={page}")
            if listing is None:
                return None, None, None, DIGEST_UNAVAILABLE, "first-use"
            if listing.status != 200 or not isinstance(listing.obj, list):
                break
            asset = _find_asset(listing.obj, server["asset"])
            if asset is not None or len(listing.obj) < ASSETS_PER_PAGE:
                break
    if asset is None:
        raise ProvisionError(ASSET_MISSING)
    if asset.get("state") != "uploaded":
        raise ProvisionError(ASSET_NOT_UPLOADED)
    size = asset.get("size")
    size = size if isinstance(size, int) and not isinstance(size, bool) and size >= 1 else None
    digest = asset.get("digest")
    m = DIGEST_RE.fullmatch(digest) if isinstance(digest, str) else None
    if m is None:
        return None, size, None, NO_DIGEST, "first-use"
    return m.group(1), size, digest, None, "github-api"


# --------------------------------------------------------------------------------------------------- the hub API

def _hub(session: _Session, url: str) -> JsonReply:
    for attempt in range(1, API_TRIES + 1):
        t0 = time.monotonic()
        reply = get_json(url, url_map=session.url_map, environ=session.environ)
        session.add("api_s", t0)
        session.connections.append(reply.connection)
        transient = (reply.status is None or reply.status == 429 or reply.status >= 500
                     or (reply.status == 200 and reply.obj is None))
        if not transient:
            return reply
        if attempt < API_TRIES:
            session.sleep(retry_delay(attempt, reply.headers.get("retry-after")))
    raise ProvisionError(HUB_UNAVAILABLE, EXIT_NETWORK)


def _file_missing(siblings: list[Any]) -> str:
    names = sorted({s["rfilename"] for s in siblings
                    if isinstance(s, dict) and isinstance(s.get("rfilename"), str)
                    and s["rfilename"].endswith(".gguf")})
    shown = [n for n in names if NAME_SHOWN_RE.fullmatch(n)][:MAX_NAMES_SHOWN]
    hidden = len(names) - len(shown)
    return FILE_MISSING + " " + (", ".join(shown) if shown else "none") + (f" ({hidden} not shown)" if hidden else "")


def _hub_file(session: _Session, hf_base: str, entry: Mapping[str, Any],
              locked: Mapping[str, Any] | None) -> tuple[str, str, int, Any]:
    """(commit, sha256, size, gated) for the model's file at the lock's commit or the manifest's revision."""
    ref = entry["gguf"]
    revision = locked["commit"] if locked is not None else ref["revision"]
    reply = _hub(session, f"{hf_base}/api/models/{quote(ref['repo'])}/revision/{quote(revision, safe='')}?blobs=true")
    if reply.status in (401, 403, 404):
        raise ProvisionError(HUB_REFUSED)
    if 300 <= (reply.status or 0) <= 399:
        raise ProvisionError(HUB_REDIRECTED)
    if reply.status != 200 or not isinstance(reply.obj, dict):
        raise ProvisionError(f"{HUB_STATUS} (HTTP {reply.status})")
    info = reply.obj
    commit = info.get("sha")
    if not isinstance(commit, str) or COMMIT_RE.fullmatch(commit) is None:
        raise ProvisionError(HUB_BAD_COMMIT)
    if locked is not None and commit != locked["commit"]:
        raise ProvisionError(HUB_COMMIT_DIFFERS)
    if info.get("gated") is not False:
        raise ProvisionError(REPO_GATED)
    card = info.get("cardData")
    license_ = card.get("license") if isinstance(card, dict) else None
    if not isinstance(license_, str) or license_ != ref["license"]:
        raise ProvisionError(LICENSE_DIFFERS)
    siblings = info.get("siblings") if isinstance(info.get("siblings"), list) else []
    sibling = next((s for s in siblings if isinstance(s, dict) and s.get("rfilename") == ref["file"]), None)
    if sibling is None:
        raise ProvisionError(_file_missing(siblings))
    lfs = sibling.get("lfs") if isinstance(sibling.get("lfs"), dict) else {}
    sha, size = lfs.get("sha256"), lfs.get("size")
    if (not isinstance(sha, str) or SHA256_RE.fullmatch(sha) is None or not isinstance(size, int)
            or isinstance(size, bool) or size < 1):
        raise ProvisionError(NO_HUB_DIGEST.format(field="lfs.sha256"))
    if size > MAX_GGUF_BYTES:
        raise ProvisionError(FILE_TOO_LARGE.format(limit=MAX_GGUF_BYTES // GIB))
    if locked is not None and (sha != locked["sha256"] or size != locked["size"]):
        raise ProvisionError(HUB_DIFFERS_FROM_LOCK)
    return commit, sha, size, info.get("gated")


# --------------------------------------------------------------------------------------------------- preflight

def _preflight(record: dict[str, Any], cache_root: Path, size: int | None, *, memory: bool) -> None:
    if size is not None:
        need = 2 * size + GIB
        free = disk_free(cache_root)
        record.update(disk_free_bytes=free, disk_needed_bytes=need)
        if free < need:
            raise ProvisionError(NOT_ENOUGH_DISK.format(free=_mib(free), need=_mib(need)))
    if memory and size is not None:
        need = size + 2 * GIB
        available = mem_available()
        record.update(mem_available_bytes=available, mem_needed_bytes=need)
        if available is not None and available < need:
            raise ProvisionError(NOT_ENOUGH_MEMORY.format(free=_mib(available), need=_mib(need)))


def _check_roots(out: Path, cache_root: Path) -> None:
    for path in (out, cache_root):
        if forbidden_root(path) is not None:
            raise ProvisionError(FORBIDDEN_ROOT)
    resolved_out, resolved_cache = out.resolve(), cache_root.resolve()
    if resolved_cache == resolved_out or resolved_out in resolved_cache.parents:
        raise ProvisionError(CACHE_INSIDE_OUT)


def load_plan(path: str | os.PathLike[str]) -> tuple[dict[str, Any], bytes]:
    try:
        data = Path(path).read_bytes()
        plan = strict_load(data)
    except (OSError, StrictJsonError):
        raise ProvisionError(NOT_A_PLAN) from None
    ok = (isinstance(plan, dict) and plan.get("kind") == "lab_plan" and plan.get("schema_version") == 1
          and isinstance(plan.get("models"), dict) and isinstance(plan.get("shards"), list)
          and isinstance(plan.get("manifest"), dict) and isinstance(plan.get("lock"), dict))
    if not ok:
        raise ProvisionError(NOT_A_PLAN)
    return plan, data


# --------------------------------------------------------------------------------------------------- provision

def _skeleton(target: str, key: str | None, matched_key: str, environ: Mapping[str, str]) -> dict[str, Any]:
    return {"schema_version": 1, "kind": "lab_provision", "target": target, "key": key,
            "run_attempt": run_attempt(environ), "plan_sha256": None, "manifest_sha256": None, "lock_sha256": None,
            "hf_base": None, "verified": False, "verified_by": None, "first_use": False, "problem": None,
            "exit_code": None, "server": None, "model": None,
            "cache": {"path": None, "matched_key": matched_key, "hit": False, "save_key": None, "save": False},
            "download": None, "connections": [],
            "preflight": {"disk_free_bytes": None, "disk_needed_bytes": None, "mem_available_bytes": None,
                          "mem_needed_bytes": None},
            "timings": {}, "host": None, "started_at": utc_clock(), "finished_at": None}


def _provision_server(record: dict[str, Any], session: _Session, manifest: Manifest, cache_root: Path,
                      out: Path) -> tuple[Path, dict[str, Any]]:
    server = manifest.server
    tag, asset = server["tag"], server["asset"]
    block = {name: server[name] for name in ("program", "tag", "asset", "url", "archive_root", "binary", "args",
                                             "cache_ram_mib", "threads")}
    block.update(sha256=None, size=None, digest=None, digest_note=None, version=None)
    record["server"] = block
    final = cache_root / cache_dir("server", tag) / asset
    record["cache"]["path"] = f"{cache_dir('server', tag)}/{asset}"
    locked = manifest.lock.server
    if locked is not None:
        expected, size, verified_by = locked["sha256"], None, "lock"
    else:
        expected, size, block["digest"], block["digest_note"], verified_by = _release_digest(session, server)
    _preflight(record["preflight"], cache_root, size, memory=False)
    try:
        source, sha, got_size = session.fetch(server["url"], final, expected, size,
                                              trust_cache=expected is not None,
                                              mismatch=LOCK_MISMATCH if locked is not None else DIGEST_MISMATCH)
    except DownloadError as err:
        raise _download_error(err) from None
    scratch = Path(tempfile.mkdtemp(prefix="extract-", dir=out))
    try:
        t0 = time.monotonic()
        binary = extract_server(final, scratch / "x", server["archive_root"], server["binary"])
        session.add("extract_s", t0)
        block["version"] = check_version(binary, scratch, session.environ)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    _keep_only(final.parent, asset)
    block.update(sha256=sha, size=got_size)
    record.update(verified_by=verified_by, first_use=verified_by == "first-use")
    candidate = {"schema_version": 1, "server": {"tag": tag, "asset": asset, "sha256": sha}, "models": {}}
    return final, {"source": source, "sha256": sha, "name": tag, "candidate": candidate}


def _provision_gguf(record: dict[str, Any], session: _Session, manifest: Manifest, key: str,
                    cache_root: Path) -> tuple[Path, dict[str, Any]]:
    entry = manifest.models[key]
    ref = entry["gguf"]
    locked = manifest.lock.models.get(key)
    record["model"] = {"key": key, "alias": entry["alias"], "repo": ref["repo"], "file": ref["file"],
                       "revision": ref["revision"], "commit": None, "url": None, "sha256": None, "size": None,
                       "license": ref["license"], "gated": None}
    commit, sha, size, gated = _hub_file(session, manifest.hf_base, entry, locked)
    url = f"{manifest.hf_base}/{quote(ref['repo'])}/resolve/{commit}/{quote(ref['file'], safe='/')}"
    record["model"].update(commit=commit, url=url, gated=gated)
    directory = cache_root / cache_dir("gguf", key)
    final = directory / f"{sha}.gguf"
    record["cache"]["path"] = f"{cache_dir('gguf', key)}/{sha}.gguf"
    _preflight(record["preflight"], cache_root, size, memory=True)
    try:
        source, got_sha, got_size = session.fetch(url, final, sha, size, trust_cache=True,
                                                  mismatch=LOCK_MISMATCH if locked is not None else DIGEST_MISMATCH,
                                                  magic=GGUF_MAGIC)
    except DownloadError as err:
        raise _download_error(err) from None
    _keep_only(directory, final.name)
    record["model"].update(sha256=got_sha, size=got_size)
    record.update(verified_by="lock" if locked is not None else "hf-api", first_use=False)
    candidate = {"schema_version": 1, "server": None,
                 "models": {key: {"repo": ref["repo"], "file": ref["file"], "revision": ref["revision"],
                                  "commit": commit, "sha256": got_sha, "size": got_size}}}
    return final, {"source": source, "sha256": got_sha, "name": key, "candidate": candidate}


def provision(target: str, key: str | None, plan_path: str, manifest_path: str, cache_root: Path, out: Path, *,
              matched_key: str = "", github_output: str | None = None, url_map: UrlMap | None = None,
              sleep: Callable[[float], Any] = time.sleep, environ: Mapping[str, str] | None = None) -> int:
    environ = os.environ if environ is None else environ
    t0 = time.monotonic()
    record = _skeleton(target, key, matched_key, environ)
    session = _Session(url_map=url_map, sleep=sleep, environ=environ)
    final: Path | None = None
    result: dict[str, Any] | None = None
    if forbidden_root(out) is not None:
        print(f"error: --out {FORBIDDEN_ROOT}", file=sys.stderr)
        return EXIT_USAGE
    try:
        out.mkdir(parents=True, exist_ok=True)
        _check_roots(out, cache_root)
        for name in ("provision.json", "lock-candidate.json"):
            _remove(out / name)
        cache_root.mkdir(parents=True, exist_ok=True)
        plan, plan_bytes = load_plan(plan_path)
        record["plan_sha256"] = sha256_hex(plan_bytes)
        try:
            manifest = load_manifest(manifest_path)
        except ManifestError as err:
            raise ProvisionError(f"{err.source} {err.json_path}: {err.problem}") from None
        record.update(manifest_sha256=manifest.sha256, lock_sha256=manifest.lock.sha256, hf_base=manifest.hf_base)
        if manifest.sha256 != plan["manifest"].get("sha256") or manifest.lock.sha256 != plan["lock"].get("sha256"):
            raise ProvisionError(PLAN_CHANGED)
        gguf_keys = sorted(k for k, m in plan["models"].items() if isinstance(m, dict) and m.get("kind") == "gguf")
        if target == "server":
            if not gguf_keys or manifest.server is None:
                raise ProvisionError(NO_GGUF_IN_PLAN)
            final, result = _provision_server(record, session, manifest, cache_root, out)
        else:
            if key not in gguf_keys or key not in manifest.models or manifest.models[key]["kind"] != "gguf":
                raise ProvisionError(MODEL_NOT_IN_PLAN)
            final, result = _provision_gguf(record, session, manifest, key, cache_root)
        save_key = cache_key(target, result["name"], result["sha256"])
        record.update(verified=True, exit_code=EXIT_OK)
        record["cache"].update(hit=result["source"] == "cache", save_key=save_key, save=matched_key != save_key)
        write_json_atomic(out / "lock-candidate.json", result["candidate"])
    except ProvisionError as err:
        record.update(verified=False, problem=err.problem, exit_code=err.code, verified_by=None, first_use=False)
        if final is None and record["cache"]["path"] is not None:
            final = cache_root / record["cache"]["path"]
        if final is not None:
            _remove(final)
            _remove(final.with_name(final.name + ".part"))
    record["download"] = session.downloads
    record["connections"] = session.connections
    session.timings["total_s"] = round(time.monotonic() - t0, 3)
    record["timings"] = session.timings
    record["host"] = hostinfo.collect(cache_root if cache_root.is_dir() else out)
    record["finished_at"] = utc_clock()
    if out.is_dir():
        write_json_atomic(out / "provision.json", record)
    verified = record["verified"]
    sha = (record["server"] or record["model"] or {}).get("sha256") if verified else None
    if github_output:
        write_github_output(github_output, {"verified": "true" if verified else "false", "sha256": sha or "",
                                            "save_key": record["cache"]["save_key"] or "",
                                            "save": "true" if record["cache"]["save"] else "false"})
    source = result["source"] if verified and result is not None else "none"
    print(f"lab: provision {target} {key or '-'} verified {'true' if verified else 'false'} by "
          f"{record['verified_by'] or 'none'} sha256 {sha[:16] if sha else 'none'} source {source} "
          f"wall_s {session.timings['total_s']:.1f}", flush=True)
    if not verified:
        print(f"error: {record['problem']}", file=sys.stderr)
        print(f"::error::{gh_data(record['problem'])}", file=sys.stderr)
    return record["exit_code"]


# --------------------------------------------------------------------------------------------------- the lock

def merge_lock(manifest_path: str, candidates: Path, out_file: Path) -> int:
    """Merge every candidate under ``candidates`` into the manifest's lock and write the result to ``out_file``."""
    try:
        manifest = load_manifest(manifest_path)
        base = strict_load(lock_path(manifest_path).read_bytes())
        server, models = parse_lock(base, manifest.server, manifest.models)
    except (ManifestError, OSError, StrictJsonError):
        print("error: the manifest or its lock cannot be loaded", file=sys.stderr)
        return EXIT_USAGE
    known = (1 if server is not None else 0) + len(models)
    paths = sorted(p for p in Path(candidates).rglob("lock-candidate.json") if p.is_file())
    for path in paths:
        try:
            got_server, got_models = parse_lock(strict_load(path.read_bytes()), manifest.server, manifest.models)
        except ManifestError as err:
            print(f"error: lock candidate {err.json_path}: {err.problem}", file=sys.stderr)
            return EXIT_USAGE
        except (OSError, StrictJsonError):
            print("error: a lock candidate cannot be read", file=sys.stderr)
            return EXIT_USAGE
        if got_server is not None:
            if server is not None and server != got_server:
                print(f"error: {LOCK_CONFLICT}: $.server", file=sys.stderr)
                return EXIT_USAGE
            server = got_server
        for key, entry in got_models.items():
            if key in models and models[key] != entry:
                print(f"error: {LOCK_CONFLICT}: $.models.{key}", file=sys.stderr)
                return EXIT_USAGE
            models[key] = entry
    merged = {"schema_version": 1, "server": server, "models": {k: models[k] for k in sorted(models)}}
    try:
        parse_lock(merged, manifest.server, manifest.models)
    except ManifestError as err:
        print(f"error: lock {err.json_path}: {err.problem}", file=sys.stderr)
        return EXIT_USAGE
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")
    new = (1 if server is not None else 0) + len(models) - known
    print("lock: unchanged" if new == 0 else f"lock: {new} new entries")
    return EXIT_OK


# --------------------------------------------------------------------------------------------------- records

def load_records(directory: str | os.PathLike[str]) -> dict[tuple[str, str], tuple[dict[str, Any], bytes]]:
    """``{("server", ""): (record, bytes), ("gguf", key): ...}`` from every ``provision.json`` at most
    :data:`RECORD_DEPTH` levels under ``directory``; per target the highest ``run_attempt`` wins, and two different
    records of that attempt are ambiguous."""
    found: dict[tuple[str, str], list[tuple[int, dict[str, Any], bytes]]] = {}
    base = Path(directory)
    for current, dirnames, filenames in os.walk(base):
        depth = len(Path(current).relative_to(base).parts)
        if depth >= RECORD_DEPTH - 1:
            dirnames.clear()
        dirnames.sort()
        if "provision.json" not in filenames:
            continue
        try:
            data = (Path(current) / "provision.json").read_bytes()
            rec = strict_load(data)
        except (OSError, StrictJsonError):
            continue
        if not isinstance(rec, dict) or rec.get("kind") != "lab_provision" or rec.get("target") not in TARGETS:
            continue
        attempt = rec.get("run_attempt")
        if not isinstance(attempt, int) or isinstance(attempt, bool):
            continue
        target_key = (rec["target"], rec.get("key") or "")
        found.setdefault(target_key, []).append((attempt, rec, data))
    records = {}
    for target_key, items in found.items():
        newest = max(a for a, _, _ in items)
        top = {data: rec for a, rec, data in items if a == newest}
        if len(top) > 1:
            raise ProvisionError(AMBIGUOUS_RECORDS)
        data, rec = next(iter(top.items()))
        records[target_key] = (rec, data)
    return records


def _server_block_ok(block: Any) -> bool:
    if not isinstance(block, dict):
        return False

    def rel(value: Any, empty: bool) -> bool:
        return (value == "" and empty) or (isinstance(value, str) and value != "" and all(
            SEGMENT_RE.fullmatch(s) is not None and s not in (".", "..") for s in value.split("/")))

    return (isinstance(block.get("tag"), str) and TAG_RE.fullmatch(block["tag"]) is not None
            and isinstance(block.get("asset"), str) and ASSET_RE.fullmatch(block["asset"]) is not None
            and isinstance(block.get("sha256"), str) and SHA256_RE.fullmatch(block["sha256"]) is not None
            and isinstance(block.get("url"), str) and rel(block.get("archive_root"), True)
            and rel(block.get("binary"), False))


def _model_block_ok(block: Any, key: str) -> bool:
    return (isinstance(block, dict) and block.get("key") == key and isinstance(block.get("url"), str)
            and isinstance(block.get("sha256"), str) and SHA256_RE.fullmatch(block["sha256"]) is not None
            and isinstance(block.get("size"), int) and not isinstance(block.get("size"), bool)
            and isinstance(block.get("commit"), str) and COMMIT_RE.fullmatch(block["commit"]) is not None)


# --------------------------------------------------------------------------------------------------- prepare

def prepare(plan_path: str, shard_id: str, records_dir: str, cache_root: Path, out: Path, *,
            github_output: str | None = None, url_map: UrlMap | None = None,
            sleep: Callable[[float], Any] = time.sleep, environ: Mapping[str, str] | None = None) -> int:
    environ = os.environ if environ is None else environ
    t0 = time.monotonic()
    started_at = utc_clock()
    session = _Session(url_map=url_map, sleep=sleep, environ=environ)
    try:
        if forbidden_root(out) is not None or forbidden_root(cache_root) is not None:
            raise ProvisionError(FORBIDDEN_ROOT)
        _check_roots(out, cache_root)
        if out.is_symlink() or (out.exists() and not out.is_dir()):
            raise ProvisionError(OUT_NOT_PREPARABLE)
        if out.exists() and any(p.name not in ("provision", "server") for p in out.iterdir()):
            raise ProvisionError(OUT_NOT_PREPARABLE)
        if SHARD_ID_RE.fullmatch(shard_id) is None:
            raise ProvisionError(NOT_A_PLAN)
        plan, plan_bytes = load_plan(plan_path)
        shard = next((s for s in plan["shards"] if isinstance(s, dict) and s.get("shard") == shard_id), None)
        if shard is None:
            raise ProvisionError(NOT_A_PLAN)
    except ProvisionError as err:
        print(f"error: {err.problem}", file=sys.stderr)
        print(f"::error::{gh_data(err.problem)}", file=sys.stderr)
        if github_output:
            write_github_output(github_output, {"needed": "true", "prepared": "false"})
        return err.code
    for name in ("provision", "server"):
        _remove(out / name)
    (out / "provision").mkdir(parents=True)
    plan_sha256 = sha256_hex(plan_bytes)
    base = {"schema_version": 1, "kind": "lab_prepare", "shard": shard_id, "plan_sha256": plan_sha256}
    if shard.get("kind") != "gguf":
        write_json_atomic(out / "provision" / "prepare.json",
                          {**base, "needed": False, "started_at": started_at, "finished_at": utc_clock(),
                           "wall_s": round(time.monotonic() - t0, 3)})
        if github_output:
            write_github_output(github_output, {"needed": "false", "prepared": "true"})
        print(f"lab: prepare {shard_id} needed false wall_s {time.monotonic() - t0:.1f}", flush=True)
        return EXIT_OK
    key = shard.get("model")
    try:
        if not isinstance(key, str) or MODEL_KEY_RE.fullmatch(key) is None:
            raise ProvisionError(NOT_A_PLAN)
        prepared = _prepare_gguf(session, plan_sha256, key, records_dir, cache_root, out)
    except ProvisionError as err:
        write_json_atomic(out / "provision" / "prepare-error.json",
                          {**base, "kind": "lab_prepare_error", "problem": err.problem, "exit_code": err.code,
                           "connections": session.connections, "started_at": started_at,
                           "finished_at": utc_clock()})
        if github_output:
            write_github_output(github_output, {"needed": "true", "prepared": "false"})
        print(f"error: {err.problem}", file=sys.stderr)
        print(f"::error::{gh_data(err.problem)}", file=sys.stderr)
        return err.code
    write_json_atomic(out / "provision" / "prepare.json",
                      {**base, "needed": True, **prepared, "connections": session.connections,
                       "started_at": started_at, "finished_at": utc_clock(),
                       "wall_s": round(time.monotonic() - t0, 3)})
    if github_output:
        write_github_output(github_output, {"needed": "true", "prepared": "true"})
    print(f"lab: prepare {shard_id} server {prepared['server']['source']} model {prepared['model']['source']} "
          f"wall_s {time.monotonic() - t0:.1f}", flush=True)
    return EXIT_OK


def _prepare_gguf(session: _Session, plan_sha256: str, key: str, records_dir: str, cache_root: Path,
                  out: Path) -> dict[str, Any]:
    records = load_records(records_dir)
    server_rec, server_bytes = records.get(("server", ""), (None, b""))
    model_rec, model_bytes = records.get(("gguf", key), (None, b""))
    if server_rec is None or server_rec.get("verified") is not True or not _server_block_ok(server_rec.get("server")):
        raise ProvisionError(PROVISION_FAILED_SERVER)
    if model_rec is None or model_rec.get("verified") is not True or not _model_block_ok(model_rec.get("model"),
                                                                                           key):
        raise ProvisionError(f"{PROVISION_FAILED_MODEL} {key}")
    if server_rec.get("plan_sha256") != plan_sha256 or model_rec.get("plan_sha256") != plan_sha256:
        raise ProvisionError(RECORD_OTHER_PLAN)
    server, model = server_rec["server"], model_rec["model"]
    preflight = {"mem_available_bytes": mem_available(), "mem_needed_bytes": model["size"] + 2 * GIB}
    available, need = preflight["mem_available_bytes"], preflight["mem_needed_bytes"]
    if available is not None and available < need:
        raise ProvisionError(NOT_ENOUGH_MEMORY.format(free=_mib(available), need=_mib(need)))
    cache_root.mkdir(parents=True, exist_ok=True)
    tarball = cache_root / cache_dir("server", server["tag"]) / server["asset"]
    size = server.get("size") if isinstance(server.get("size"), int) else None
    try:
        server_source, _, _ = session.fetch(server["url"], tarball, server["sha256"], size, trust_cache=True,
                                            mismatch=DIGEST_MISMATCH)
    except DownloadError as err:
        raise _download_error(err) from None
    bin_dir = out / "server" / "bin"
    _remove(bin_dir)
    t0 = time.monotonic()
    binary = extract_server(tarball, bin_dir, server["archive_root"], server["binary"])
    extract_s = round(time.monotonic() - t0, 3)
    version = check_version(binary, out / "server", session.environ)
    gguf = cache_root / cache_dir("gguf", key) / f"{model['sha256']}.gguf"
    try:
        model_source, _, _ = session.fetch(model["url"], gguf, model["sha256"], model["size"], trust_cache=True,
                                           mismatch=DIGEST_MISMATCH, magic=GGUF_MAGIC)
    except DownloadError as err:
        raise _download_error(err) from None
    (out / "provision" / "server.json").write_bytes(server_bytes)
    (out / "provision" / "model.json").write_bytes(model_bytes)
    root = Path("server") / "bin" / server["archive_root"] if server["archive_root"] else Path("server") / "bin"
    return {
        "server": {"record_sha256": sha256_hex(server_bytes), "sha256": server["sha256"],
                   "tarball": f"{cache_dir('server', server['tag'])}/{server['asset']}", "source": server_source,
                   "binary": (root / server["binary"]).as_posix(), "version": version,
                   "libraries": libraries(out / root), "extract_s": extract_s},
        "model": {"record_sha256": sha256_hex(model_bytes), "key": key, "path": os.path.abspath(gguf),
                  "sha256": model["sha256"], "size": model["size"], "commit": model["commit"],
                  "source": model_source},
        "preflight": preflight,
    }


# --------------------------------------------------------------------------------------------------- CLI

def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m lab.provision",
                                description="Download, verify and cache a run's pinned server and model files.")
    sub = p.add_subparsers(dest="command", required=True)
    for name in TARGETS:
        s = sub.add_parser(name, help=f"provision the {'server archive' if name == 'server' else 'model file'}")
        if name == "gguf":
            s.add_argument("--model", required=True)
        s.add_argument("--plan", required=True)
        s.add_argument("--manifest", required=True)
        s.add_argument("--cache-root", required=True)
        s.add_argument("--out", required=True)
        s.add_argument("--cache-matched-key", default="")
        s.add_argument("--github-output")
    m = sub.add_parser("merge-lock", help="merge lock candidates into the manifest's lock")
    m.add_argument("--manifest", required=True)
    m.add_argument("--candidates", required=True)
    m.add_argument("--out", required=True)
    return p


def main(argv: list[str] | None = None, *, url_map: UrlMap | None = None,
         sleep: Callable[[float], Any] = time.sleep) -> int:
    args = _parser().parse_args(argv)
    if args.command == "merge-lock":
        return merge_lock(args.manifest, Path(args.candidates), Path(args.out))
    key = args.model if args.command == "gguf" else None
    if key is not None and MODEL_KEY_RE.fullmatch(key) is None:
        print(f"error: {MODEL_NOT_IN_PLAN}", file=sys.stderr)
        return EXIT_USAGE
    return provision(args.command, key, args.plan, args.manifest, Path(args.cache_root), Path(args.out),
                     matched_key=args.cache_matched_key, github_output=args.github_output, url_map=url_map,
                     sleep=sleep)


if __name__ == "__main__":
    sys.exit(main())
