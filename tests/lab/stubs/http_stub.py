"""A loopback stub of the model hub, the GitHub release API and their download CDNs, plus a release tarball maker.

:class:`HubStub` listens on ``127.0.0.1:<port>``. The lab reaches it through :meth:`HubStub.url_map`, which turns a
logical URL ``https://<host>/<path>?<query>`` into ``http://127.0.0.1:<port>/<host>/<path>?<query>``; every request
must come from loopback and is recorded (:attr:`HubStub.requests`: method, logical host, path, query, lower-cased
headers, range, route).

Routes (``<hf>`` is any host):

* ``/<hf>/api/models/<owner>/<name>/revision/<rev>``: the model info (``sha``, ``gated``, ``cardData.license``,
  ``siblings[].{rfilename, size, lfs.{sha256, size, pointerSize}}``) for a branch or the model's commit;
* ``/<hf>/<owner>/<name>/resolve/<commit>/<file>``: 302 (absolute or, with ``redirect_style = "relative"``, relative)
  to a one-shot CDN token ``https://cdn.stub/<token>/<file>``;
* ``/api.github.com/repos/<o>/<r>/releases/tags/<tag>`` (the first 30 assets) and
  ``/api.github.com/repos/<o>/<r>/releases/<id>/assets?per_page=&page=``;
* ``/github.com/<o>/<r>/releases/download/<tag>/<asset>``: 302 to a one-shot token ``https://release.stub/...``;
* ``/<any host>/<token>/<name>``: the token's bytes, honouring ``Range: bytes=<n>-``; a used token answers 403.

:meth:`HubStub.fault` scripts the next ``times`` requests of a route (``hf_api``, ``resolve``, ``cdn``,
``gh_release``, ``gh_assets``, ``release_download``, ``release_cdn``; ``times=None`` is every request). The CDN
faults: ``status``, ``expired``, ``cut_at``, ``ignore_range``, ``content_length_delta``, ``wrong_bytes``,
``trickle_s`` and, for a 206, ``content_range_delta`` (the stated first byte), ``content_range_total_delta`` (the
stated total) and ``no_content_range``.
"""
from __future__ import annotations

import gzip
import hashlib
import io
import json
import secrets
import socket
import tarfile
import threading
import time
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlsplit

ROUTES = ("hf_api", "resolve", "cdn", "gh_release", "gh_assets", "release_download", "release_cdn", "hop")
API_HOST = "api." + "github.com"
RELEASE_HOST = "github.com"
CHUNK = 65536
TRICKLE_CHUNK = 4096


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    stub: "HubStub"

    def handle_error(self, request: Any, client_address: Any) -> None:
        pass                                 # a client that hung up mid-body is part of the tests


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"
    server_version = "hub-stub"
    sys_version = ""

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - BaseHTTPRequestHandler's signature
        pass

    def do_GET(self) -> None:
        self.server.stub._handle(self)


class HubStub:
    def __init__(self) -> None:
        self.models: dict[str, dict[str, Any]] = {}
        self.releases: dict[tuple[str, str, str], dict[str, Any]] = {}
        self.requests: list[dict[str, Any]] = []
        self.violations: list[str] = []
        self.redirect_style = "absolute"
        self.port = 0
        self._faults: dict[str, list[list[Any]]] = {route: [] for route in ROUTES}
        self._tokens: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._server: _Server | None = None
        self._thread: threading.Thread | None = None
        self._next_id = 1000

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> "HubStub":
        server = _Server(("127.0.0.1", 0), _Handler)
        server.stub = self
        self.port = server.server_address[1]
        self._server = server
        self._thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def url_map(self, url: str) -> str:
        parts = urlsplit(url)
        return f"http://127.0.0.1:{self.port}/{parts.hostname}{parts.path}" + (f"?{parts.query}" if parts.query else "")

    # ------------------------------------------------------------------ content
    def add_model(self, repo: str, *, commit: str, file: str, blob: bytes, revision: str = "main",
                  license: str = "apache-2.0", gated: Any = False, card: bool = True, lfs: bool = True,
                  extra_files: tuple[str, ...] = ()) -> None:
        self.models[repo] = {"revision": revision, "commit": commit, "file": file, "blob": blob, "license": license,
                             "gated": gated, "card": card, "lfs": lfs, "extra_files": tuple(extra_files)}

    def add_release(self, owner: str, repo: str, tag: str, asset: str, blob: bytes, *, digest: Any = "auto",
                    state: str = "uploaded", listed: bool = True, extra_assets: int = 0) -> None:
        self._next_id += 1
        assets = [{"name": f"extra-{i:03d}.zip", "size": 10, "state": "uploaded", "digest": "sha256:" + "0" * 64,
                   "blob": b"x" * 10} for i in range(extra_assets)]
        if listed:
            assets.append({"name": asset, "size": len(blob), "state": state,
                           "digest": f"sha256:{sha256(blob)}" if digest == "auto" else digest, "blob": blob})
        self.releases[(owner, repo, tag)] = {"id": self._next_id, "tag": tag, "assets": assets, "asset": asset,
                                             "blob": blob}

    def fault(self, route: str, *, times: int | None = 1, **spec: Any) -> None:
        if route not in ROUTES:
            raise ValueError(f"unknown route {route}")
        with self._lock:
            self._faults[route].append([spec, times])

    def clear_faults(self) -> None:
        with self._lock:
            for route in self._faults:
                self._faults[route] = []

    def requests_for(self, route: str) -> list[dict[str, Any]]:
        with self._lock:
            return [r for r in self.requests if r["route"] == route]

    def _take(self, route: str) -> dict[str, Any]:
        with self._lock:
            for item in self._faults[route]:
                spec, times = item
                if times is None or times > 0:
                    if times is not None:
                        item[1] = times - 1
                    return dict(spec)
        return {}

    def _token(self, blob: bytes, name: str, route: str, fault: dict[str, Any] | None = None) -> str:
        token = secrets.token_hex(8)
        with self._lock:
            self._tokens[token] = {"blob": blob, "name": name, "route": route, "used": False,
                                   "expired": bool(fault and fault.get("expired"))}
        return token

    # ------------------------------------------------------------------ dispatch
    def _handle(self, h: _Handler) -> None:
        if h.client_address[0] != "127.0.0.1":
            self.violations.append(h.client_address[0])
            return self._json(h, 403, {"message": "loopback only"})
        target = urlsplit(h.path)
        segments = [unquote(s) for s in target.path.split("/")[1:]]
        host, rest = (segments[0], segments[1:]) if segments else ("", [])
        route = self._route(host, rest)
        record = {"method": "GET", "host": host, "path": "/" + "/".join(rest), "query": target.query,
                  "headers": {k.lower(): v for k, v in h.headers.items()}, "range": h.headers.get("Range"),
                  "route": route}
        with self._lock:
            self.requests.append(record)
        if route is None:
            return self._json(h, 404, {"message": "not found"})
        getattr(self, f"_{route}")(h, host, rest, parse_qs(target.query), self._take(route))

    def _route(self, host: str, rest: list[str]) -> str | None:
        with self._lock:
            token = rest[0] if rest and rest[0] in self._tokens else None
            token_route = self._tokens[token]["route"] if token else None
        if token_route is not None:
            return token_route
        if host == "hop.stub":
            return "hop"
        if host == API_HOST and len(rest) == 6 and rest[0] == "repos" and rest[3:5] == ["releases", "tags"]:
            return "gh_release"
        if host == API_HOST and len(rest) == 6 and rest[0] == "repos" and rest[3] == "releases" and rest[5] == "assets":
            return "gh_assets"
        if host == RELEASE_HOST and len(rest) == 6 and rest[2:4] == ["releases", "download"]:
            return "release_download"
        if len(rest) >= 6 and rest[0:2] == ["api", "models"] and rest[4] == "revision":
            return "hf_api"
        if len(rest) >= 5 and rest[2] == "resolve":
            return "resolve"
        return None

    def _json(self, h: _Handler, status: int, obj: Any, headers: dict[str, str] | None = None) -> None:
        data = json.dumps(obj).encode("utf-8")
        h.send_response(status)
        h.send_header("Content-Type", "application/json")
        h.send_header("Content-Length", str(len(data)))
        for name, value in (headers or {}).items():
            h.send_header(name, value)
        h.end_headers()
        h.wfile.write(data)

    def _status_fault(self, h: _Handler, fault: dict[str, Any]) -> bool:
        if "status" not in fault:
            return False
        headers = {}
        if fault.get("retry_after") is not None:
            headers["Retry-After"] = str(fault["retry_after"])
        if fault.get("ratelimit"):
            headers["X-RateLimit-Remaining"] = "0"
        self._json(h, fault["status"], {"message": "scripted"}, headers)
        return True

    def _redirect(self, h: _Handler, location: str) -> None:
        h.send_response(302)
        h.send_header("Location", location)
        h.send_header("Content-Length", "0")
        h.end_headers()

    # ------------------------------------------------------------------ the hub
    def _hf_api(self, h: _Handler, host: str, rest: list[str], query: dict[str, list[str]],
                fault: dict[str, Any]) -> None:
        if self._status_fault(h, fault):
            return
        repo, revision = f"{rest[2]}/{rest[3]}", rest[5]
        model = self.models.get(repo)
        if model is None or revision not in (model["revision"], model["commit"]):
            return self._json(h, 404, {"error": "Repository not found"})
        if fault.get("api_redirect"):
            h.send_response(307)
            h.send_header("Location", f"/api/models/renamed/{rest[3]}/revision/{rest[5]}")
            h.send_header("Content-Length", "0")
            h.end_headers()
            return
        blob = model["blob"]
        siblings = [{"rfilename": name} for name in model["extra_files"]]
        if not fault.get("file_missing"):
            sibling: dict[str, Any] = {"rfilename": model["file"], "size": len(blob)}
            if model["lfs"] and not fault.get("no_lfs"):
                sibling["lfs"] = {"sha256": fault.get("lfs_sha256", sha256(blob)),
                                  "size": fault.get("lfs_size", len(blob)), "pointerSize": 134}
            siblings.append(sibling)
        info: dict[str, Any] = {"id": repo, "sha": fault.get("sha", model["commit"]),
                                "gated": fault.get("gated", model["gated"]), "siblings": siblings}
        if model["card"] and not fault.get("card_missing"):
            info["cardData"] = {"license": fault.get("license", model["license"])}
        self._json(h, 200, info)

    def _resolve(self, h: _Handler, host: str, rest: list[str], query: dict[str, list[str]],
                 fault: dict[str, Any]) -> None:
        if self._status_fault(h, fault):
            return
        repo, commit, name = f"{rest[0]}/{rest[1]}", rest[3], "/".join(rest[4:])
        model = self.models.get(repo)
        if model is None or commit != model["commit"] or name != model["file"]:
            return self._json(h, 404, {"error": "Entry not found"})
        cdn_fault = {"expired": True} if fault.get("expired") else None
        token = self._token(model["blob"], name, "cdn", cdn_fault)
        self._send_to_token(h, token, quote(name), fault)

    def _send_to_token(self, h: _Handler, token: str, name: str, fault: dict[str, Any]) -> None:
        hops = int(fault.get("redirects", 1)) - 1
        if hops > 0:
            return self._redirect(h, f"https://hop.stub/r{hops - 1}/{token}/{name}")
        if fault.get("redirect_to_http"):
            return self._redirect(h, f"http://cdn.stub/{token}/{name}")
        if self.redirect_style == "relative":
            return self._redirect(h, f"/{token}/{name}")
        self._redirect(h, f"https://cdn.stub/{token}/{name}")

    def _hop(self, h: _Handler, host: str, rest: list[str], query: dict[str, list[str]],
             fault: dict[str, Any]) -> None:
        left = int(rest[0][1:])
        token, name = rest[1], "/".join(rest[2:])
        if left > 0:
            return self._redirect(h, f"https://hop.stub/r{left - 1}/{token}/{quote(name)}")
        self._redirect(h, f"https://cdn.stub/{token}/{quote(name)}")

    def _cdn(self, h: _Handler, host: str, rest: list[str], query: dict[str, list[str]],
             fault: dict[str, Any]) -> None:
        with self._lock:
            entry = self._tokens[rest[0]]
            used, entry["used"] = entry["used"], True
        if used or entry["expired"] or fault.get("expired"):
            return self._json(h, 403, {"message": "Request has expired"})
        if self._status_fault(h, fault):
            return
        blob = entry["blob"]
        if fault.get("wrong_bytes"):
            blob = bytes(reversed(blob))
        start = 0
        requested = h.headers.get("Range")
        if requested and not fault.get("ignore_range"):
            start = int(requested.split("=", 1)[1].split("-", 1)[0])
            if start >= len(blob):
                return self._json(h, 416, {"message": "range not satisfiable"})
        status = 206 if requested and not fault.get("ignore_range") else 200
        body = blob[start:]
        h.send_response(status)
        h.send_header("Content-Type", "application/octet-stream")
        h.send_header("Content-Length", str(len(body) + int(fault.get("content_length_delta", 0))))
        if status == 206 and not fault.get("no_content_range"):
            # the body is always blob[start:]; the faults only misstate it in the header
            first = start + int(fault.get("content_range_delta", 0))
            total = len(blob) + int(fault.get("content_range_total_delta", 0))
            h.send_header("Content-Range", f"bytes {first}-{len(blob) - 1}/{total}")
        h.end_headers()
        cut = int(len(blob) * fault["cut_at"]) - start if fault.get("cut_at") else None
        sent = 0
        step = TRICKLE_CHUNK if fault.get("trickle_s") else CHUNK
        try:
            while sent < len(body):
                if cut is not None and sent >= cut:
                    h.wfile.flush()
                    h.connection.shutdown(socket.SHUT_RDWR)
                    return
                end = min(len(body), sent + step, cut if cut is not None and cut > sent else len(body))
                h.wfile.write(body[sent:end])
                sent = end
                if fault.get("trickle_s"):
                    h.wfile.flush()
                    time.sleep(fault["trickle_s"])
        except OSError:
            return

    _release_cdn = _cdn

    # ------------------------------------------------------------------ GitHub
    def _gh_release(self, h: _Handler, host: str, rest: list[str], query: dict[str, list[str]],
                    fault: dict[str, Any]) -> None:
        if self._status_fault(h, fault):
            return
        release = self.releases.get((rest[1], rest[2], rest[5]))
        if release is None:
            return self._json(h, 404, {"message": "Not Found"})
        self._json(h, 200, {"id": release["id"], "tag_name": release["tag"],
                            "assets": [self._asset(a) for a in release["assets"][:30]]})

    def _gh_assets(self, h: _Handler, host: str, rest: list[str], query: dict[str, list[str]],
                   fault: dict[str, Any]) -> None:
        if self._status_fault(h, fault):
            return
        release = next((r for (o, n, _), r in self.releases.items()
                        if (o, n) == (rest[1], rest[2]) and str(r["id"]) == rest[4]), None)
        if release is None:
            return self._json(h, 404, {"message": "Not Found"})
        per_page = int(query.get("per_page", ["30"])[0])
        page = int(query.get("page", ["1"])[0])
        chunk = release["assets"][(page - 1) * per_page:page * per_page]
        self._json(h, 200, [self._asset(a) for a in chunk])

    def _asset(self, asset: dict[str, Any]) -> dict[str, Any]:
        return {"id": abs(hash(asset["name"])) % 100000, "name": asset["name"], "size": asset["size"],
                "state": asset["state"], "digest": asset["digest"]}

    def _release_download(self, h: _Handler, host: str, rest: list[str], query: dict[str, list[str]],
                          fault: dict[str, Any]) -> None:
        if self._status_fault(h, fault):
            return
        release = self.releases.get((rest[0], rest[1], rest[4]))
        if release is None or rest[5] != release["asset"]:
            return self._json(h, 404, {"message": "Not Found"})
        token = self._token(release["blob"], rest[5], "release_cdn")
        hops = int(fault.get("redirects", 1)) - 1
        if hops > 0:
            return self._redirect(h, f"https://hop.stub/r{hops - 1}/{token}/{rest[5]}")
        self._redirect(h, f"https://release.stub/{token}/{rest[5]}")


# --------------------------------------------------------------------------------------------------- tarballs

UNSAFE_KINDS = ("dotdot", "absolute", "symlink_out", "outside_root", "fifo", "not_exec", "no_binary", "zip")


def _add(tar: tarfile.TarFile, name: str, data: bytes = b"", *, mode: int = 0o644, kind: bytes = tarfile.REGTYPE,
         linkname: str = "") -> None:
    info = tarfile.TarInfo(name)
    info.type = kind
    info.mode = mode
    info.mtime = 0
    if kind == tarfile.REGTYPE:
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    else:
        info.linkname = linkname
        tar.addfile(info)


def make_tarball(path: Path, *, archive_root: str, binary: str, launcher: Path, unsafe: str | None = None) -> bytes:
    """A release-like gzip tarball: ``<archive_root>/`` holding the launcher as ``binary`` (mode 0755), a dummy
    versioned library with a symlink chain, and the one unsafe member ``unsafe`` names. Returns the bytes."""
    if unsafe is not None and unsafe not in UNSAFE_KINDS:
        raise ValueError(f"unknown unsafe kind {unsafe}")
    if unsafe == "zip":
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr(f"{archive_root}/{binary}", Path(launcher).read_bytes())
        data = buffer.getvalue()
        Path(path).write_bytes(data)
        return data
    prefix = f"{archive_root}/" if archive_root else ""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        if archive_root:
            _add(tar, archive_root, kind=tarfile.DIRTYPE, mode=0o755)
        if "/" in binary:
            _add(tar, prefix + binary.rsplit("/", 1)[0], kind=tarfile.DIRTYPE, mode=0o755)
        if unsafe != "no_binary":
            _add(tar, prefix + binary, Path(launcher).read_bytes(), mode=0o644 if unsafe == "not_exec" else 0o755)
        _add(tar, prefix + "libstub-base.so.0.1.0", b"\x7fELF stub library", mode=0o755)
        _add(tar, prefix + "libstub-base.so.0", kind=tarfile.SYMTYPE, linkname="libstub-base.so.0.1.0")
        _add(tar, prefix + "libstub-base.so", kind=tarfile.SYMTYPE, linkname="libstub-base.so.0")
        if unsafe == "dotdot":
            _add(tar, prefix + "../escaped.txt", b"x")
        elif unsafe == "absolute":
            _add(tar, "/tmp/lab-stub-absolute.txt", b"x")
        elif unsafe == "symlink_out":
            _add(tar, prefix + "escape", kind=tarfile.SYMTYPE, linkname="../../outside")
        elif unsafe == "outside_root":
            _add(tar, "elsewhere/file.txt", b"x")
        elif unsafe == "fifo":
            _add(tar, prefix + "pipe", kind=tarfile.FIFOTYPE)
    data = gzip.compress(buffer.getvalue(), mtime=0)
    Path(path).write_bytes(data)
    return data
