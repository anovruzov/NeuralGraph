"""Provisioning against loopback stubs of the hub, the GitHub release API and their CDNs: the downloader's resume,
redirect, retry and deadline rules; server and model provisioning with every refusal; the lock merge; and the
shard's prepare step. Every URL goes through the stub's ``url_map`` to 127.0.0.1, and every backoff sleep is
injected and recorded, never slept.
"""
from __future__ import annotations

import builtins
import errno
import hashlib
import json
import os
import random
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from lab import download as dl
from lab import provision
from lab.manifest import cache_key, load_manifest
from lab.notes import (AMBIGUOUS_RECORDS, ASSET_MISSING, ASSET_NOT_UPLOADED, BINARY_MISSING, CACHE_INSIDE_OUT,
                       DIGEST_MISMATCH, DIGEST_UNAVAILABLE, DOWNLOAD_DEADLINE, DOWNLOAD_INSECURE, DOWNLOAD_NETWORK,
                       DOWNLOAD_REDIRECTS, DOWNLOAD_REFUSED, FILE_MISSING, FILE_TOO_LARGE, FORBIDDEN_ROOT,
                       HUB_COMMIT_DIFFERS, HUB_DIFFERS_FROM_LOCK, HUB_REDIRECTED, HUB_REFUSED, HUB_UNAVAILABLE,
                       LICENSE_DIFFERS, LOCK_CONFLICT, LOCK_MISMATCH, MODEL_NOT_IN_PLAN, NO_DIGEST, NO_HUB_DIGEST,
                       NO_GGUF_IN_PLAN, NOT_A_PLAN, NOT_ENOUGH_DISK, NOT_ENOUGH_MEMORY, NOT_GGUF, NOT_GZIP_TAR,
                       OUT_NOT_PREPARABLE, PLAN_CHANGED,
                       PROVISION_FAILED_MODEL, PROVISION_FAILED_SERVER, RECORD_OTHER_PLAN, RELEASE_TAG_MISSING,
                       REPO_GATED, UNSAFE_MEMBER, VERSION_FAILED)
from tests.lab.helpers import (COMMIT, HUB_COMMIT, MODEL_KEY, ROOT, StubWorld, call_main, gguf_blob,
                               kill_mentioning, make_plan, plumbing_min, stub_manifest, write_json)
from tests.lab.stubs.http_stub import UNSAFE_KINDS, HubStub

MIB = 1 << 20
LINE_RE = (r"lab: provision (server|gguf) (-|[a-z0-9-]+) verified (true|false) by "
           r"(lock|hf-api|github-api|first-use|none) sha256 ([0-9a-f]{16}|none) source (cache|download|none) "
           r"wall_s [0-9]+\.[0-9]")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _outputs(path: Path) -> dict[str, str]:
    return dict(line.split("=", 1) for line in path.read_text(encoding="utf-8").splitlines())


class TempDirTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="lab-provision-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.addCleanup(kill_mentioning, str(self.tmp))


# --------------------------------------------------------------------------------------------------- downloads

class DownloadTests(TempDirTest):
    REPO, FILE = "example-org/dl-test-GGUF", "dl-test.gguf"

    def setUp(self) -> None:
        super().setUp()
        self.hub = HubStub().start()
        self.addCleanup(self.hub.stop)
        self.blob = gguf_blob(3 * MIB)
        self.hub.add_model(self.REPO, commit=COMMIT, file=self.FILE, blob=self.blob)
        self.url = f"https://hub.example/{self.REPO}/resolve/{COMMIT}/{self.FILE}"
        self.part = self.tmp / "dl" / "x.gguf.part"
        self.sleeps: list[float] = []

    def _download(self, **kwargs: Any) -> dl.Download:
        kwargs.setdefault("expected_size", len(self.blob))
        self.part.unlink(missing_ok=True)
        return dl.download(self.url, self.part, url_map=self.hub.url_map, sleep=self.sleeps.append, **kwargs)

    def _fails(self, kind: str, **kwargs: Any) -> dl.DownloadError:
        with self.assertRaises(dl.DownloadError) as caught:
            self._download(**kwargs)
        self.assertEqual(caught.exception.kind, kind, caught.exception.problem)
        self.assertFalse(self.part.exists())
        return caught.exception

    def test_success_no_auth_headers(self) -> None:
        secrets = {"GH_TOKEN": "Qz11SecretGh", "HF_TOKEN": "Qz12SecretHf", "GITHUB_TOKEN": "Qz13SecretGithub"}
        for style in ("absolute", "relative"):
            with self.subTest(style=style), mock.patch.dict(os.environ, secrets):
                self.hub.redirect_style = style
                self.hub.requests.clear()
                got = self._download()
                self.assertEqual((got.sha256, got.size, got.attempts, got.resumed_from), (_sha(self.blob),
                                                                                          len(self.blob), 1, []))
                self.assertEqual(self.part.read_bytes(), self.blob)
                cdn_host = "cdn.stub" if style == "absolute" else "hub.example"
                self.assertEqual(got.connections, [
                    {"purpose": "download", "logical_host": "hub.example", "host": "127.0.0.1", "status": 302},
                    {"purpose": "redirect", "logical_host": cdn_host, "host": "127.0.0.1", "status": 200}])
                self.assertEqual([r["route"] for r in self.hub.requests], ["resolve", "cdn"])
                for request in self.hub.requests:
                    headers = request["headers"]
                    self.assertNotIn("authorization", headers)
                    self.assertNotIn("cookie", headers)
                    self.assertEqual((headers["user-agent"], headers["accept"], headers["accept-encoding"]),
                                     ("mycelic-lab", "*/*", "identity"))
                    self.assertNotIn("range", headers)
                    for value in secrets.values():
                        self.assertNotIn(value, json.dumps(headers))
        self.assertEqual(self.sleeps, [])

    def test_cut_once_resumes_from_start_url(self) -> None:
        self.hub.fault("cdn", cut_at=0.4)
        got = self._download()
        n = int(len(self.blob) * 0.4)
        self.assertGreaterEqual(n, MIB)
        self.assertEqual((got.sha256, got.attempts, got.resumed_from), (_sha(self.blob), 2, [n]))
        self.assertEqual([r["route"] for r in self.hub.requests], ["resolve", "cdn", "resolve", "cdn"])
        self.assertEqual([r["range"] for r in self.hub.requests], [None, None, f"bytes={n}-", f"bytes={n}-"])
        self.assertEqual(self.sleeps, [2.0])

    def test_small_partial_restarts_from_zero(self) -> None:
        self.hub.fault("cdn", cut_at=0.2)
        got = self._download()
        self.assertLess(int(len(self.blob) * 0.2), MIB)
        self.assertEqual((got.sha256, got.attempts, got.resumed_from), (_sha(self.blob), 2, []))
        self.assertEqual([r["range"] for r in self.hub.requests_for("cdn")], [None, None])

    def test_expired_signed_redirect_retried_from_start(self) -> None:
        self.hub.fault("resolve", expired=True)
        got = self._download()
        self.assertEqual((got.sha256, got.attempts), (_sha(self.blob), 2))
        self.assertEqual([r["route"] for r in self.hub.requests], ["resolve", "cdn", "resolve", "cdn"])
        self.assertEqual([c["status"] for c in got.connections], [302, 403, 302, 200])
        self.assertEqual(self.sleeps, [])

    def test_range_answered_200_restarts(self) -> None:
        self.hub.fault("cdn", cut_at=0.4)
        self.hub.fault("cdn", ignore_range=True)
        got = self._download()
        self.assertEqual((got.sha256, got.attempts, got.resumed_from), (_sha(self.blob), 2, []))
        self.assertEqual(self.part.read_bytes(), self.blob)
        self.assertIsNotNone(self.hub.requests_for("cdn")[1]["range"])

    def test_416_restarts_without_range(self) -> None:
        self.hub.fault("cdn", cut_at=0.4)
        self.hub.fault("cdn", status=416)
        got = self._download()
        self.assertEqual((got.sha256, got.attempts), (_sha(self.blob), 3))
        ranges = [r["range"] for r in self.hub.requests_for("cdn")]
        self.assertEqual((ranges[0], ranges[2]), (None, None))
        self.assertIsNotNone(ranges[1])
        self.assertEqual(self.sleeps, [2.0])

    def test_content_range_mismatch_restarts_from_zero(self) -> None:
        n = int(len(self.blob) * 0.4)
        for fault in ({"content_range_delta": 1}, {"content_range_delta": -1}, {"content_range_total_delta": 1},
                      {"no_content_range": True}):
            with self.subTest(**fault):
                self.hub.requests.clear()
                self.sleeps.clear()
                self.hub.fault("cdn", cut_at=0.4)
                self.hub.fault("cdn", **fault)
                got = self._download()
                # the misstated 206 is refused (the partial goes), so the third attempt starts again from byte 0
                self.assertEqual((got.sha256, got.attempts, got.resumed_from), (_sha(self.blob), 3, []))
                self.assertEqual(self.part.read_bytes(), self.blob)
                self.assertEqual([r["range"] for r in self.hub.requests_for("cdn")], [None, f"bytes={n}-", None])
                self.assertEqual([c["status"] for c in got.connections if c["purpose"] == "redirect"],
                                 [200, 206, 200])
                self.assertEqual(self.sleeps, [2.0, 4.0])

    def test_content_length_mismatch_exhausts_to_size(self) -> None:
        self.hub.fault("cdn", content_length_delta=1, times=None)
        err = self._fails("size")
        self.assertEqual(err.problem, dl.PROBLEMS["size"])
        self.assertEqual(len(self.hub.requests_for("resolve")), dl.MAX_ATTEMPTS)
        self.assertEqual(self.sleeps, [2.0, 4.0, 8.0, 16.0, 32.0])
        self.hub.requests.clear()
        self.hub.clear_faults()
        self.hub.fault("cdn", cut_at=0.5)
        self.hub.fault("cdn", content_length_delta=-1)
        self.sleeps.clear()
        got = self._download()
        self.assertEqual((got.sha256, got.attempts), (_sha(self.blob), 3))
        n = int(len(self.blob) * 0.5)
        self.assertEqual([r["range"] for r in self.hub.requests_for("cdn")], [None, f"bytes={n}-", f"bytes={n}-"])

    def test_retry_after_capped_and_backoff_sequence(self) -> None:
        self.hub.fault("resolve", status=503, retry_after="500")
        self.assertEqual(self._download().sha256, _sha(self.blob))
        self.assertEqual(self.sleeps, [120.0])
        self.sleeps.clear()
        self.hub.fault("resolve", status=503, times=2)
        self.assertEqual(self._download().attempts, 3)
        self.assertEqual(self.sleeps, [2.0, 4.0])
        self.sleeps.clear()
        self.hub.fault("cdn", status=429, retry_after="7")
        self.assertEqual(self._download().attempts, 2)
        self.assertEqual(self.sleeps, [7.0])
        self.assertEqual(dl.retry_delay(9, None), 32.0)
        self.assertEqual(dl.retry_delay(1, "Wed, 21 Oct 2015 07:28:00 GMT"), 2.0)

    def test_first_host_401_403_404_are_final(self) -> None:
        for status in (401, 403, 404, 410, 400):
            with self.subTest(status=status):
                self.hub.requests.clear()
                self.hub.fault("resolve", status=status)
                err = self._fails("refused")
                self.assertEqual(err.problem, f"{DOWNLOAD_REFUSED} (HTTP {status})")
                self.assertEqual(err.status, status)
                self.assertEqual([r["route"] for r in self.hub.requests], ["resolve"])
        self.assertEqual(self.sleeps, [])

    def test_redirect_limit(self) -> None:
        self.hub.fault("resolve", redirects=5)
        got = self._download()
        self.assertEqual(sum(1 for c in got.connections if c["status"] == 302), 5)
        self.hub.fault("resolve", redirects=6)
        err = self._fails("refused")
        self.assertEqual(err.problem, DOWNLOAD_REDIRECTS)
        self.assertEqual(sum(1 for c in err.connections if c["status"] == 302), 6)

    def test_redirect_to_non_loopback_http_refused(self) -> None:
        self.hub.fault("resolve", redirect_to_http=True)
        err = self._fails("refused")
        self.assertEqual(err.problem, DOWNLOAD_INSECURE)
        self.assertEqual(self.hub.requests_for("cdn"), [])
        self.assertEqual(err.connections[-1]["logical_host"], "cdn.stub")
        with self.assertRaises(dl.DownloadError) as caught:
            dl.download("http://hub.example/x", self.part, expected_size=None, url_map=self.hub.url_map,
                        sleep=self.sleeps.append)
        self.assertEqual(caught.exception.problem, DOWNLOAD_INSECURE)

    def test_deadline_during_trickle(self) -> None:
        self.hub.fault("cdn", trickle_s=0.02, times=None)
        started = time.monotonic()
        err = self._fails("deadline", deadline_s=0.6)
        self.assertEqual(err.problem, DOWNLOAD_DEADLINE)
        self.assertLess(time.monotonic() - started, 5)
        self.hub.fault("resolve", status=503, retry_after="100")
        self._fails("deadline", deadline_s=50)
        self.assertEqual(self.sleeps, [])

    def test_disk_full_deletes_part(self) -> None:
        real_open = builtins.open

        class Full:
            def __init__(self, fh: Any) -> None:
                self.fh = fh

            def write(self, data: bytes) -> int:
                raise OSError(errno.ENOSPC, "No space left on device")

            def __enter__(self) -> "Full":
                return self

            def __exit__(self, *exc: Any) -> None:
                self.fh.close()

            def close(self) -> None:
                self.fh.close()

        def full_open(path: Any, mode: str = "r", *args: Any, **kwargs: Any) -> Any:
            fh = real_open(path, mode, *args, **kwargs)
            return Full(fh) if ("w" in mode or "a" in mode) else fh

        with mock.patch("lab.download.open", full_open, create=True):
            err = self._fails("disk")
        self.assertEqual(err.problem, dl.PROBLEMS["disk"])
        self.assertEqual(len(self.hub.requests_for("cdn")), 1)

    def test_proxy_selection(self) -> None:
        environ = {"HTTPS_PROXY": "http://proxy.invalid:3128", "HTTP_PROXY": "http://proxy.invalid:3128"}
        conn, target = dl.connection("https://hub.example/a/b?c=d", environ)
        self.assertEqual((type(conn).__name__, conn.host, conn.port, target),
                         ("HTTPSConnection", "proxy.invalid", 3128, "/a/b?c=d"))
        self.assertEqual((conn._tunnel_host, conn._tunnel_port), ("hub.example", 443))
        for url in ("http://127.0.0.1:9/x", "http://localhost:9/x", "http://[::1]:9/x"):
            conn, target = dl.connection(url, environ)
            self.assertEqual((type(conn).__name__, target), ("HTTPConnection", "/x"))
            self.assertIsNone(conn._tunnel_host)
            self.assertNotEqual(conn.host, "proxy.invalid")
        conn, target = dl.connection("https://hub.example/x", {})
        self.assertEqual((conn.host, conn._tunnel_host), ("hub.example", None))
        conn, _ = dl.connection("https://hub.example/x", {**environ, "NO_PROXY": "hub.example"})
        self.assertEqual(conn.host, "hub.example")
        self.assertFalse(dl.hop_allowed("http://hub.example/x"))
        self.assertTrue(dl.hop_allowed("http://127.0.0.1:1/x"))
        self.assertTrue(dl.hop_allowed("https://hub.example/x"))
        self.assertFalse(dl.hop_allowed("ftp://hub.example/x"))


# --------------------------------------------------------------------------------------------------- shared

class WorldTest(TempDirTest):
    world_kwargs: dict[str, Any] = {}

    def world(self, **kwargs: Any) -> StubWorld:
        world = StubWorld(self.tmp / f"w{len(getattr(self, '_worlds', []))}", **{**self.world_kwargs, **kwargs})
        self._worlds = [*getattr(self, "_worlds", []), world]
        self.addCleanup(world.close)
        return world

    def assert_failed(self, world: StubWorld, target: str, code: int, problem: str, *extra: str,
                      out: Path | None = None) -> dict[str, Any]:
        got, stdout, stderr, record = world.provision(target, *extra, out=out)
        self.assertEqual(got, code, stderr)
        self.assertRegex(stdout.strip(), LINE_RE)
        self.assertEqual(len(stdout.splitlines()), 1)
        self.assertIn(problem, stderr.splitlines()[0])
        self.assertTrue(stderr.splitlines()[0].startswith("error: "))
        self.assertTrue(any(line.startswith("::error::") for line in stderr.splitlines()))
        self.assertEqual((record["verified"], record["exit_code"]), (False, code))
        self.assertIn(problem, record["problem"])
        self.assert_nothing_cached(world, target)
        return record

    def assert_nothing_cached(self, world: StubWorld, target: str) -> None:
        directory = world.cache / ("server/" + world.tag if target == "server" else f"gguf/{MODEL_KEY}")
        left = sorted(p.name for p in directory.iterdir()) if directory.exists() else []
        self.assertEqual(left, [])


# --------------------------------------------------------------------------------------------------- server

class ServerProvisionTests(WorldTest):
    def test_github_api_digest_success(self) -> None:
        world = self.world()
        gho = self.tmp / "gho"
        code, stdout, stderr, record = world.provision("server", "--github-output", str(gho))
        self.assertEqual(code, 0, stderr)
        sha = _sha(world.tarball)
        self.assertEqual(stdout.strip().split(" wall_s")[0],
                         f"lab: provision server - verified true by github-api sha256 {sha[:16]} source download")
        self.assertEqual((record["verified"], record["verified_by"], record["first_use"], record["problem"]),
                         (True, "github-api", False, None))
        self.assertEqual((record["server"]["sha256"], record["server"]["size"], record["server"]["digest"]),
                         (sha, len(world.tarball), f"sha256:{sha}"))
        self.assertEqual(record["server"]["version"], "version: 0 (stub)")
        self.assertEqual(sorted(p.name for p in (world.cache / "server" / world.tag).iterdir()), [world.asset])
        self.assertEqual(sorted(p.name for p in (world.records / "server").iterdir()),
                         ["lock-candidate.json", "provision.json"])
        save_key = f"lab-server-{world.tag}-{sha[:16]}"
        self.assertEqual(record["cache"], {"path": f"server/{world.tag}/{world.asset}", "matched_key": "",
                                           "hit": False, "save_key": save_key, "save": True})
        self.assertEqual(_outputs(gho), {"verified": "true", "sha256": sha, "save_key": save_key, "save": "true"})
        candidate = json.loads((world.records / "server" / "lock-candidate.json").read_text())
        self.assertEqual(candidate, {"schema_version": 1, "server": {"tag": world.tag, "asset": world.asset,
                                                                     "sha256": sha}, "models": {}})
        self.assertEqual(record["plan_sha256"], _sha(world.plan_path.read_bytes()))
        self.assertEqual(record["download"]["verify_retries"], 0)
        self.assertEqual([c["purpose"] for c in record["connections"]], ["api", "download", "redirect"])
        self.assertEqual(world.stub_files("argv"), [["--version"]])
        env = world.stub_files("environ")[0]
        self.assertEqual(sorted(env), ["HOME", "LANG", "PATH", "TMPDIR"])
        self.assertTrue(env["HOME"].startswith(str(world.records / "server")))

        gho2 = self.tmp / "gho2"
        world.hub.requests.clear()
        code, _, stderr, record = world.provision("server", "--cache-matched-key", save_key, "--github-output",
                                                  str(gho2))
        self.assertEqual(code, 0, stderr)
        self.assertEqual((record["cache"]["hit"], record["cache"]["save"]), (True, False))
        self.assertEqual(_outputs(gho2)["save"], "false")
        self.assertEqual(world.hub.requests_for("release_download"), [])

    def test_token_only_to_api_host(self) -> None:
        world = self.world()
        with mock.patch.dict(os.environ, {"GH_TOKEN": "Qz21TokenValue"}):
            code, _, stderr, record = world.provision("server")
        self.assertEqual(code, 0, stderr)
        for request in world.hub.requests:
            with self.subTest(route=request["route"]):
                expected = "Bearer Qz21TokenValue" if request["route"] in ("gh_release", "gh_assets") else None
                self.assertEqual(request["headers"].get("authorization"), expected)
        self.assertEqual({r["route"] for r in world.hub.requests}, {"gh_release", "release_download", "release_cdn"})
        self.assertNotIn("Qz21TokenValue", json.dumps(record))
        for env in world.stub_files("environ"):
            self.assertNotIn("Qz21TokenValue", json.dumps(env))
        self.assertNotIn("Authorization", provision.api_headers("https://evil.example/repos", {"GH_TOKEN": "t"}))
        self.assertNotIn("Authorization", provision.api_headers(f"https://{provision.API_HOST}/x", {"GH_TOKEN": ""}))

    def test_locked_server_makes_no_api_request(self) -> None:
        def lock(world: StubWorld) -> dict[str, Any]:
            return {"schema_version": 1, "models": {},
                    "server": {"tag": world.tag, "asset": world.asset, "sha256": _sha(world.tarball)}}

        world = self.world(lock=lock)
        self.assertEqual(world.plan["provision"][0]["restore_key"],
                         cache_key("server", world.tag, _sha(world.tarball)))
        code, stdout, stderr, record = world.provision("server")
        self.assertEqual(code, 0, stderr)
        self.assertEqual((record["verified_by"], record["server"]["sha256"]), ("lock", _sha(world.tarball)))
        self.assertEqual(world.hub.requests_for("gh_release"), [])
        self.assertIn("by lock", stdout)

    def test_lock_mismatch(self) -> None:
        world = self.world()
        lock = {"schema_version": 1, "server": {"tag": world.tag, "asset": world.asset, "sha256": "e" * 64},
                "models": {}}
        world = self.world(lock=lock)
        record = self.assert_failed(world, "server", 2, LOCK_MISMATCH)
        self.assertEqual(record["download"]["verify_retries"], 1)
        self.assertEqual(len(world.hub.requests_for("release_download")), 2)
        self.assertFalse((world.records / "server" / "lock-candidate.json").exists())

    def test_digest_mismatch(self) -> None:
        world = self.world(release={"digest": "sha256:" + "0" * 64})
        record = self.assert_failed(world, "server", 2, DIGEST_MISMATCH)
        self.assertEqual(record["download"]["verify_retries"], 1)

    def test_null_digest_first_use(self) -> None:
        for digest in (None, "md5:abc", "sha256:XYZ"):
            with self.subTest(digest=digest):
                world = self.world(release={"digest": digest})
                code, stdout, stderr, record = world.provision("server")
                self.assertEqual(code, 0, stderr)
                self.assertEqual((record["verified_by"], record["first_use"], record["server"]["digest_note"],
                                  record["server"]["digest"]), ("first-use", True, NO_DIGEST, None))
                self.assertTrue((world.records / "server" / "lock-candidate.json").exists())
                self.assertIn("by first-use", stdout)

    def test_rate_limited_api_first_use(self) -> None:
        for spec, requests, sleeps in (({"status": 403, "ratelimit": True}, 3, [2.0, 4.0]),
                                       ({"status": 429, "retry_after": "9"}, 3, [9.0, 9.0]),
                                       ({"status": 503}, 3, [2.0, 4.0]), ({"status": 403}, 1, [])):
            with self.subTest(spec=spec):
                world = self.world()
                world.hub.fault("gh_release", times=None, **spec)
                code, _, stderr, record = world.provision("server")
                self.assertEqual(code, 0, stderr)
                self.assertEqual((record["verified_by"], record["first_use"], record["server"]["digest_note"]),
                                 ("first-use", True, DIGEST_UNAVAILABLE))
                self.assertEqual(len(world.hub.requests_for("gh_release")), requests)
                self.assertEqual(world.sleeps, sleeps)
                self.assertEqual(record["server"]["sha256"], _sha(world.tarball))
                self.assertTrue((world.records / "server" / "lock-candidate.json").exists())

    def test_api_401_first_use(self) -> None:
        world = self.world()
        world.hub.fault("gh_release", status=401, times=None)
        code, _, stderr, record = world.provision("server")
        self.assertEqual(code, 0, stderr)
        self.assertEqual((record["verified_by"], record["server"]["digest_note"]), ("first-use", DIGEST_UNAVAILABLE))
        self.assertEqual((len(world.hub.requests_for("gh_release")), world.sleeps), (1, []))

    def test_first_use_never_trusts_the_cache(self) -> None:
        world = self.world(release={"digest": None})
        self.assertEqual(world.provision("server")[0], 0)
        world.hub.requests.clear()
        code, _, _, record = world.provision("server")
        self.assertEqual((code, record["cache"]["hit"]), (0, False))
        self.assertEqual(len(world.hub.requests_for("release_download")), 1)

    def test_release_404(self) -> None:
        world = self.world()
        world.hub.fault("gh_release", status=404)
        self.assert_failed(world, "server", 2, RELEASE_TAG_MISSING)
        self.assertEqual(world.hub.requests_for("release_download"), [])

    def test_asset_missing_after_pagination(self) -> None:
        world = self.world(release={"listed": False, "extra_assets": 150})
        self.assert_failed(world, "server", 2, ASSET_MISSING)
        self.assertEqual([r["query"] for r in world.hub.requests_for("gh_assets")],
                         ["per_page=100&page=1", "per_page=100&page=2"])
        world = self.world(release={"extra_assets": 140})
        code, _, stderr, record = world.provision("server")
        self.assertEqual((code, record["verified_by"]), (0, "github-api"), stderr)
        self.assertEqual(len(world.hub.requests_for("gh_assets")), 2)

    def test_asset_state_open(self) -> None:
        world = self.world(release={"state": "open"})
        self.assert_failed(world, "server", 2, ASSET_NOT_UPLOADED)

    def test_download_404_when_api_unavailable(self) -> None:
        world = self.world()
        world.hub.fault("gh_release", status=503, times=None)
        world.hub.fault("release_download", status=404)
        record = self.assert_failed(world, "server", 2, DOWNLOAD_REFUSED)
        self.assertEqual(record["problem"], f"{DOWNLOAD_REFUSED} (HTTP 404)")

    def test_unsafe_archives(self) -> None:
        expected = {"dotdot": UNSAFE_MEMBER, "absolute": UNSAFE_MEMBER, "symlink_out": UNSAFE_MEMBER,
                    "outside_root": UNSAFE_MEMBER, "fifo": UNSAFE_MEMBER, "not_exec": BINARY_MISSING,
                    "no_binary": BINARY_MISSING, "zip": NOT_GZIP_TAR}
        self.assertEqual(sorted(expected), sorted(UNSAFE_KINDS))
        for kind, problem in expected.items():
            with self.subTest(kind=kind):
                world = self.world(unsafe=kind)
                self.assert_failed(world, "server", 2, problem)
                self.assertEqual(sorted(p.name for p in (world.records / "server").iterdir()), ["provision.json"])
                self.assertFalse(Path("/tmp/lab-stub-absolute.txt").exists())
                self.assertFalse((world.records / "escaped.txt").exists())
                self.assertEqual(world.stub_files("argv"), [])

    def test_version_failing(self) -> None:
        world = self.world(server_config={"version_fail": True})
        self.assert_failed(world, "server", 2, VERSION_FAILED)
        self.assertEqual(world.stub_files("argv"), [["--version"]])

    def test_binary_the_kernel_will_not_run(self) -> None:
        from tests.lab.helpers import ARCHIVE_ROOT, BINARY, RELEASE_OWNER, RELEASE_REPO
        from tests.lab.stubs.http_stub import make_tarball

        other_machine = b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 8 + b"\x02\x00\xb7\x00" + b"\x00" * 100
        for name, content in (("other_machine_elf", other_machine), ("empty", b""), ("no_shebang", b"echo hi\n")):
            with self.subTest(binary=name):
                world = self.world()
                bad = world.tmp / f"bad-{name}"
                bad.write_bytes(content)
                bad.chmod(0o755)
                tarball = make_tarball(world.tmp / f"bad-{name}.tar.gz", archive_root=ARCHIVE_ROOT, binary=BINARY,
                                       launcher=bad)
                world.hub.add_release(RELEASE_OWNER, RELEASE_REPO, world.tag, world.asset, tarball)
                gho = world.tmp / "gho"
                record = self.assert_failed(world, "server", 2, VERSION_FAILED, "--github-output", str(gho))
                self.assertEqual(record["server"]["version"], None)
                self.assertEqual(_outputs(gho), {"verified": "false", "sha256": "", "save_key": "", "save": "false"})
                self.assertEqual(sorted(p.name for p in (world.records / "server").iterdir()), ["provision.json"])

    def test_cache_hit_rehash_no_download_and_save_flags(self) -> None:
        world = self.world()
        self.assertEqual(world.provision("server")[0], 0)
        save_key = cache_key("server", world.tag, _sha(world.tarball))
        for matched, save in ((save_key, "false"), (f"lab-server-{world.tag}-" + "0" * 16, "true"), ("", "true")):
            with self.subTest(matched=matched):
                world.hub.requests.clear()
                gho = self.tmp / f"gho-{save}-{len(matched)}"
                code, stdout, _, record = world.provision("server", "--cache-matched-key", matched,
                                                          "--github-output", str(gho))
                self.assertEqual((code, record["cache"]["hit"], _outputs(gho)["save"]), (0, True, save))
                self.assertIn("source cache", stdout)
                self.assertEqual(world.hub.requests_for("release_download"), [])
                self.assertGreater(record["timings"]["hash_s"], -1)
                self.assertEqual(record["download"], None)

    def test_corrupt_cache_redownloaded(self) -> None:
        world = self.world()
        self.assertEqual(world.provision("server")[0], 0)
        cached = world.cache / "server" / world.tag / world.asset
        cached.write_bytes(b"corrupt")
        (cached.parent / "stale.tar.gz").write_bytes(b"old")
        world.hub.requests.clear()
        code, stdout, _, record = world.provision("server")
        self.assertEqual((code, record["cache"]["hit"]), (0, False))
        self.assertIn("source download", stdout)
        self.assertEqual(cached.read_bytes(), world.tarball)
        self.assertEqual(sorted(p.name for p in cached.parent.iterdir()), [world.asset])

    def test_version_env_is_four_vars_with_sentinels(self) -> None:
        world = self.world()
        program = world.manifest["server"]["program"]
        prefix = program.split("-")[0].upper()
        sentinels = {"GH_TOKEN": "Qz31Gh", "MYCELIC_LAB_HOSTED_API_KEY": "Qz32Hosted",
                     f"{prefix}_ARG_CTX_SIZE": "Qz34Ctx", "HF_TOKEN": "Qz33Hf"}
        with mock.patch.dict(os.environ, sentinels):
            self.assertEqual(world.provision("server")[0], 0)
        env = world.stub_files("environ")[0]
        self.assertEqual(sorted(env), ["HOME", "LANG", "PATH", "TMPDIR"])
        self.assertEqual(env["LANG"], "C.UTF-8")
        for name, value in sentinels.items():
            self.assertNotIn(name, env)
            self.assertNotIn(value, json.dumps(env))

    def test_server_needs_a_gguf_plan_and_matching_manifest(self) -> None:
        world = self.world()
        _, fake_plan = make_plan(self.tmp / "fake-plan", plumbing_min())
        doctored = json.loads(world.plan_path.read_text())
        doctored["models"][MODEL_KEY]["kind"] = "fake"
        no_gguf = write_json(self.tmp / "no-gguf-plan.json", doctored)
        for plan_path, problem in ((fake_plan, PLAN_CHANGED), (no_gguf, NO_GGUF_IN_PLAN),
                                   (self.tmp / "missing.json", NOT_A_PLAN)):
            with self.subTest(problem=problem):
                out = self.tmp / f"o-{len(problem)}"
                code, stdout, stderr = call_main(provision, ["server", "--plan", str(plan_path), "--manifest",
                                                             str(world.manifest_path), "--cache-root",
                                                             str(world.cache), "--out", str(out)],
                                                 url_map=world.hub.url_map)
                self.assertEqual(code, 2)
                self.assertEqual(stderr.splitlines()[0], f"error: {problem}")
                self.assertEqual(json.loads((out / "provision.json").read_text())["problem"], problem)
                self.assertIn("verified false by none sha256 none source none", stdout)
        self.assertEqual(world.hub.requests, [])


# --------------------------------------------------------------------------------------------------- gguf

class GgufProvisionTests(WorldTest):
    def _locked(self, world: StubWorld, **changes: Any) -> dict[str, Any]:
        ref = world.manifest["models"][MODEL_KEY]["gguf"]
        entry = {"repo": ref["repo"], "file": ref["file"], "revision": ref["revision"], "commit": world.commit,
                 "sha256": _sha(world.blob), "size": len(world.blob), **changes}
        return {"schema_version": 1, "server": None, "models": {MODEL_KEY: entry}}

    def test_unlocked_hf_api_success(self) -> None:
        world = self.world()
        gho = self.tmp / "gho"
        with mock.patch.dict(os.environ, {"GH_TOKEN": "Qz61Gh", "HF_TOKEN": "Qz62Hf"}):
            code, stdout, stderr, record = world.provision("gguf", "--github-output", str(gho))
        self.assertEqual(code, 0, stderr)
        sha = _sha(world.blob)
        self.assertRegex(stdout.strip(), LINE_RE)
        self.assertEqual((record["verified_by"], record["model"]["sha256"], record["model"]["size"],
                          record["model"]["commit"], record["model"]["gated"]),
                         ("hf-api", sha, len(world.blob), COMMIT, False))
        self.assertEqual(sorted(p.name for p in (world.cache / "gguf" / MODEL_KEY).iterdir()), [f"{sha}.gguf"])
        self.assertEqual(_outputs(gho), {"verified": "true", "sha256": sha,
                                         "save_key": f"lab-gguf-{MODEL_KEY}-{sha[:16]}", "save": "true"})
        ref = world.manifest["models"][MODEL_KEY]["gguf"]
        self.assertEqual(json.loads((world.records / f"gguf-{MODEL_KEY}" / "lock-candidate.json").read_text()),
                         {"schema_version": 1, "server": None,
                          "models": {MODEL_KEY: {"repo": ref["repo"], "file": ref["file"], "revision": COMMIT,
                                                 "commit": COMMIT, "sha256": sha, "size": len(world.blob)}}})
        self.assertTrue(record["model"]["url"].endswith(f"/resolve/{COMMIT}/{ref['file']}"))
        self.assertEqual(world.hub.requests_for("hf_api")[0]["query"], "blobs=true")
        for request in world.hub.requests:
            self.assertNotIn("authorization", request["headers"])
        self.assertEqual(record["preflight"]["disk_needed_bytes"], 2 * len(world.blob) + (1 << 30))
        self.assertEqual(record["preflight"]["mem_needed_bytes"], len(world.blob) + (2 << 30))

    def test_locked_success_at_lock_commit(self) -> None:
        world = self.world(revision="main")
        world = self.world(revision="main", lock=self._locked(world, revision="main", commit=HUB_COMMIT))
        code, _, stderr, record = world.provision("gguf")
        self.assertEqual(code, 0, stderr)
        self.assertEqual((record["verified_by"], record["model"]["commit"]), ("lock", HUB_COMMIT))
        self.assertTrue(world.hub.requests_for("hf_api")[0]["path"].endswith(f"/revision/{HUB_COMMIT}"))
        self.assertIn(f"/resolve/{HUB_COMMIT}/", world.hub.requests_for("resolve")[0]["path"])

    def test_branch_revision_resolved_once(self) -> None:
        world = self.world(revision="main")
        code, _, stderr, record = world.provision("gguf")
        self.assertEqual(code, 0, stderr)
        self.assertEqual(len(world.hub.requests_for("hf_api")), 1)
        self.assertTrue(world.hub.requests_for("hf_api")[0]["path"].endswith("/revision/main"))
        self.assertIn(f"/resolve/{HUB_COMMIT}/", world.hub.requests_for("resolve")[0]["path"])
        self.assertEqual((record["model"]["revision"], record["model"]["commit"]), ("main", HUB_COMMIT))
        candidate = json.loads((world.records / f"gguf-{MODEL_KEY}" / "lock-candidate.json").read_text())
        self.assertEqual((candidate["models"][MODEL_KEY]["revision"], candidate["models"][MODEL_KEY]["commit"]),
                         ("main", HUB_COMMIT))

    def test_hf_401(self) -> None:
        for status in (401, 403, 404):
            with self.subTest(status=status):
                world = self.world()
                world.hub.fault("hf_api", status=status)
                self.assert_failed(world, "gguf", 2, HUB_REFUSED)
                self.assertEqual(len(world.hub.requests_for("hf_api")), 1)
                self.assertEqual(world.hub.requests_for("resolve"), [])

    def test_gated_auto_and_manual(self) -> None:
        for gated in ("auto", "manual", True, None):
            with self.subTest(gated=gated):
                world = self.world()
                world.hub.fault("hf_api", gated=gated)
                self.assert_failed(world, "gguf", 2, REPO_GATED)

    def test_license_mismatch_and_missing_card(self) -> None:
        for fault in ({"license": "mit"}, {"license": ["apache-2.0"]}, {"license": None}, {"card_missing": True}):
            with self.subTest(fault=fault):
                world = self.world()
                world.hub.fault("hf_api", **fault)
                self.assert_failed(world, "gguf", 2, LICENSE_DIFFERS)

    def test_file_missing_lists_safe_names(self) -> None:
        world = self.world()
        ref = world.manifest["models"][MODEL_KEY]["gguf"]
        extra = ("other-q8.gguf", "sub/dir-q2.gguf", "Qz41 unsafe name.gguf", "readme.md", "x" * 201 + ".gguf")
        world.hub.add_model(ref["repo"], commit=COMMIT, file=ref["file"], blob=world.blob, revision=COMMIT,
                            extra_files=extra)
        world.hub.fault("hf_api", file_missing=True)
        record = self.assert_failed(world, "gguf", 2, FILE_MISSING)
        self.assertEqual(record["problem"], f"{FILE_MISSING} other-q8.gguf, sub/dir-q2.gguf (2 not shown)")
        self.assertNotIn("Qz41", record["problem"])
        self.assertNotIn("readme", record["problem"])

    def test_sibling_without_lfs(self) -> None:
        for fault in ({"no_lfs": True}, {"lfs_sha256": "XYZ"}, {"lfs_size": 0}, {"lfs_size": "7"}):
            with self.subTest(fault=fault):
                world = self.world()
                world.hub.fault("hf_api", **fault)
                self.assert_failed(world, "gguf", 2, NO_HUB_DIGEST.format(field="lfs.sha256"))

    def test_too_large(self) -> None:
        world = self.world()
        world.hub.fault("hf_api", lfs_size=13 << 30)
        self.assert_failed(world, "gguf", 2, FILE_TOO_LARGE.format(limit=12))

    def test_hub_differs_from_lock(self) -> None:
        world = self.world()
        for changes, fault, problem in (({"sha256": "a" * 64}, {}, HUB_DIFFERS_FROM_LOCK),
                                        ({"size": 1}, {}, HUB_DIFFERS_FROM_LOCK),
                                        ({}, {"sha": "f" * 40}, HUB_COMMIT_DIFFERS)):
            with self.subTest(changes=changes, fault=fault):
                locked = self.world(lock=self._locked(world, **changes))
                if fault:
                    locked.hub.fault("hf_api", **fault)
                self.assert_failed(locked, "gguf", 2, problem)
                self.assertEqual(locked.hub.requests_for("resolve"), [])

    def test_api_redirect_renamed(self) -> None:
        world = self.world()
        world.hub.fault("hf_api", api_redirect=True)
        self.assert_failed(world, "gguf", 2, HUB_REDIRECTED)

    def test_sha_mismatch_once_then_ok(self) -> None:
        world = self.world()
        world.hub.fault("cdn", wrong_bytes=True)
        code, _, stderr, record = world.provision("gguf")
        self.assertEqual(code, 0, stderr)
        self.assertEqual((record["download"]["verify_retries"], record["download"]["attempts"]), (1, 2))
        self.assertEqual(len(world.hub.requests_for("resolve")), 2)

    def test_sha_mismatch_twice_exit_2(self) -> None:
        world = self.world()
        world.hub.fault("cdn", wrong_bytes=True, times=2)
        record = self.assert_failed(world, "gguf", 2, DIGEST_MISMATCH)
        self.assertEqual(record["download"]["verify_retries"], 1)
        locked = self.world(lock=self._locked(world))
        locked.hub.fault("cdn", wrong_bytes=True, times=2)
        self.assert_failed(locked, "gguf", 2, LOCK_MISMATCH)

    def test_not_gguf_magic(self) -> None:
        world = self.world()
        ref = world.manifest["models"][MODEL_KEY]["gguf"]
        world.blob = b"NOPE" + world.blob[4:]
        world.hub.add_model(ref["repo"], commit=COMMIT, file=ref["file"], blob=world.blob, revision=COMMIT)
        self.assert_failed(world, "gguf", 2, NOT_GGUF)

    def test_not_enough_disk(self) -> None:
        world = self.world()
        with mock.patch.object(provision, "disk_free", return_value=1 << 30):
            record = self.assert_failed(world, "gguf", 2, "not enough disk:")
        need = 2 * len(world.blob) + (1 << 30)
        self.assertEqual(record["problem"], NOT_ENOUGH_DISK.format(free=1024, need=need >> 20))
        self.assertEqual(world.hub.requests_for("resolve"), [])

    def test_not_enough_memory(self) -> None:
        world = self.world()
        with mock.patch.object(provision, "mem_available", return_value=1 << 30):
            record = self.assert_failed(world, "gguf", 2, "not enough memory:")
        self.assertEqual(record["problem"], NOT_ENOUGH_MEMORY.format(free=1024, need=(len(world.blob) >> 20) + 2048))
        self.assertEqual(world.hub.requests_for("resolve"), [])

    def test_stale_cache_files_removed(self) -> None:
        world = self.world()
        directory = world.cache / "gguf" / MODEL_KEY
        directory.mkdir(parents=True)
        (directory / ("0" * 64 + ".gguf")).write_bytes(b"GGUF old")
        (directory / "x.gguf.part").write_bytes(b"partial")
        (directory / "subdir").mkdir()
        sha = _sha(world.blob)
        (directory / f"{sha}.gguf").write_bytes(b"GGUF corrupt")
        code, stdout, _, record = world.provision("gguf")
        self.assertEqual(code, 0)
        self.assertIn("source download", stdout)
        self.assertEqual(sorted(p.name for p in directory.iterdir()), [f"{sha}.gguf"])
        code, stdout, _, record = world.provision("gguf")
        self.assertEqual((code, record["cache"]["hit"]), (0, True))

    def test_model_not_in_plan(self) -> None:
        world = self.world()
        for key in ("other-model", "../escape", "fake-a"):
            with self.subTest(key=key):
                out = self.tmp / f"o-{abs(hash(key))}"
                code, _, stderr = call_main(provision, ["gguf", "--model", key, "--plan", str(world.plan_path),
                                                        "--manifest", str(world.manifest_path), "--cache-root",
                                                        str(world.cache), "--out", str(out)],
                                            url_map=world.hub.url_map)
                self.assertEqual(code, 2)
                self.assertIn(MODEL_NOT_IN_PLAN, stderr)
        self.assertFalse((self.tmp / "escape").exists())
        self.assertEqual(world.hub.requests, [])

    def test_manifest_changed_since_plan(self) -> None:
        world = self.world()
        manifest = json.loads(world.manifest_path.read_text())
        manifest["models"][MODEL_KEY]["alias"] = "changed-alias"
        write_json(world.manifest_path, manifest)
        self.assert_failed(world, "gguf", 2, PLAN_CHANGED)
        world = self.world()
        write_json(world.manifest_path.with_name("manifest.lock.json"),
                   {"schema_version": 1, "server": None, "models": {}, "x": 1})
        self.assert_failed(world, "gguf", 2, "lock")
        self.assertEqual(world.hub.requests, [])

    def test_network_exhaustion_exit_3(self) -> None:
        world = self.world()
        world.hub.fault("cdn", status=503, times=None)
        self.assert_failed(world, "gguf", 3, DOWNLOAD_NETWORK)
        self.assertEqual(world.sleeps, [2.0, 4.0, 8.0, 16.0, 32.0])
        world = self.world()
        world.hub.fault("hf_api", status=503, times=None)
        self.assert_failed(world, "gguf", 3, HUB_UNAVAILABLE)
        self.assertEqual((len(world.hub.requests_for("hf_api")), world.sleeps), (3, [2.0, 4.0]))

    def test_deadline_exit_3(self) -> None:
        world = self.world(blob_size=3 * MIB)
        world.hub.fault("cdn", trickle_s=0.02, times=None)
        real = dl.download
        with mock.patch.object(provision, "download", lambda *a, **k: real(*a, deadline_s=0.5, **k)):
            self.assert_failed(world, "gguf", 3, DOWNLOAD_DEADLINE)

    def test_out_and_cache_roots(self) -> None:
        world = self.world()
        out = self.tmp / "o-cache"
        code, _, stderr = call_main(provision, ["gguf", "--model", MODEL_KEY, "--plan", str(world.plan_path),
                                                "--manifest", str(world.manifest_path), "--cache-root",
                                                str(out / "cache"), "--out", str(out)], url_map=world.hub.url_map)
        self.assertEqual(code, 2)
        self.assertEqual(stderr.splitlines()[0], f"error: {CACHE_INSIDE_OUT}")
        self.assertEqual(json.loads((out / "provision.json").read_text())["problem"], CACHE_INSIDE_OUT)
        self.assertFalse((out / "cache").exists())
        inside = ROOT / "mycelic" / "lab-provision-never-created"
        code, _, stderr, record = world.provision("gguf", out=inside)
        self.assertEqual((code, record), (2, {}))
        self.assertIn(FORBIDDEN_ROOT, stderr)
        self.assertFalse(inside.exists())


# --------------------------------------------------------------------------------------------------- lock

class LockMergeTests(TempDirTest):
    def setUp(self) -> None:
        super().setUp()
        self.manifest = stub_manifest(self.tmp / "m", revision="main")
        data = json.loads(self.manifest.read_text())
        self.server = {"tag": data["server"]["tag"], "asset": data["server"]["asset"], "sha256": "1" * 64}
        ref = data["models"][MODEL_KEY]["gguf"]
        self.model = {"repo": ref["repo"], "file": ref["file"], "revision": "main", "commit": HUB_COMMIT,
                      "sha256": "2" * 64, "size": 99}

    def _candidate(self, name: str, *, server: dict[str, Any] | None = None,
                   models: dict[str, Any] | None = None) -> Path:
        return write_json(self.tmp / "c" / name / "lock-candidate.json",
                          {"schema_version": 1, "server": server, "models": models or {}})

    def _merge(self, out: Path) -> tuple[int, str, str]:
        return call_main(provision, ["merge-lock", "--manifest", str(self.manifest), "--candidates",
                                     str(self.tmp / "c"), "--out", str(out)])

    def test_order_independent_and_idempotent(self) -> None:
        outputs = []
        for order in (["a-server", "b-model", "c-model-again"], ["c-server", "a-model", "b-model-again"]):
            shutil.rmtree(self.tmp / "c", ignore_errors=True)
            random.Random(len(outputs)).shuffle(order)
            self._candidate(order[0], server=self.server)
            self._candidate(order[1], models={MODEL_KEY: self.model})
            self._candidate(order[2], models={MODEL_KEY: dict(reversed(list(self.model.items())))})
            out = self.tmp / f"lock-{len(outputs)}.json"
            code, stdout, stderr = self._merge(out)
            self.assertEqual((code, stdout.strip()), (0, "lock: 2 new entries"), stderr)
            outputs.append(out.read_bytes())
        self.assertEqual(outputs[0], outputs[1])
        merged = json.loads(outputs[0])
        self.assertEqual(list(merged), ["schema_version", "server", "models"])
        self.assertEqual(list(merged["models"][MODEL_KEY]), ["repo", "file", "revision", "commit", "sha256", "size"])
        self.assertTrue(outputs[0].endswith(b"}\n"))
        shutil.copy(self.tmp / "lock-0.json", self.manifest.with_name("manifest.lock.json"))
        code, stdout, _ = self._merge(self.tmp / "lock-again.json")
        self.assertEqual((code, stdout.strip()), (0, "lock: unchanged"))
        self.assertEqual((self.tmp / "lock-again.json").read_bytes(), outputs[0])

    def test_conflict_between_candidates(self) -> None:
        self._candidate("a", models={MODEL_KEY: self.model})
        self._candidate("b", models={MODEL_KEY: {**self.model, "sha256": "3" * 64}})
        code, _, stderr = self._merge(self.tmp / "out.json")
        self.assertEqual(code, 2)
        self.assertIn(LOCK_CONFLICT, stderr)
        self.assertFalse((self.tmp / "out.json").exists())

    def test_conflict_with_existing_lock(self) -> None:
        write_json(self.manifest.with_name("manifest.lock.json"),
                   {"schema_version": 1, "server": self.server, "models": {}})
        self._candidate("a", server={**self.server, "sha256": "4" * 64})
        code, _, stderr = self._merge(self.tmp / "out.json")
        self.assertEqual(code, 2)
        self.assertIn(LOCK_CONFLICT, stderr)

    def test_merged_lock_loads_with_manifest(self) -> None:
        self._candidate("s", server=self.server)
        self._candidate("m", models={MODEL_KEY: self.model})
        self.assertEqual(self._merge(self.manifest.with_name("manifest.lock.json"))[0], 0)
        manifest = load_manifest(self.manifest)
        self.assertEqual(manifest.lock.server, self.server)
        self.assertEqual(manifest.lock.models[MODEL_KEY], self.model)

    def test_candidate_with_unknown_key_refused(self) -> None:
        for name, models in (("unknown", {"no-such-model": self.model}), ("field", {MODEL_KEY: {**self.model, "x": 1}}),
                             ("repin", {MODEL_KEY: {**self.model, "revision": "dev"}})):
            with self.subTest(name=name):
                shutil.rmtree(self.tmp / "c", ignore_errors=True)
                self._candidate(name, models=models)
                code, _, stderr = self._merge(self.tmp / "out.json")
                self.assertEqual(code, 2)
                self.assertTrue(stderr.startswith("error: lock candidate"), stderr)
                self.assertFalse((self.tmp / "out.json").exists())


    def test_merge_candidates_matches_merge_lock(self) -> None:
        self._candidate("s", server=self.server)
        self._candidate("m", models={MODEL_KEY: self.model})
        code, stdout, stderr = self._merge(self.tmp / "cli.json")
        self.assertEqual((code, stdout.strip()), (0, "lock: 2 new entries"), stderr)
        manifest = load_manifest(self.manifest)
        base = json.loads(self.manifest.with_name("manifest.lock.json").read_text())
        candidates = [{"schema_version": 1, "server": self.server, "models": {}},
                      {"schema_version": 1, "server": None, "models": {MODEL_KEY: self.model}}]
        for order in (candidates, candidates[::-1]):
            merged, new = provision.merge_candidates(manifest, base, order)
            self.assertEqual(new, 2)
            self.assertEqual(list(merged), ["schema_version", "server", "models"])
            provision.write_lock(self.tmp / "fn.json", merged)
            self.assertEqual((self.tmp / "fn.json").read_bytes(), (self.tmp / "cli.json").read_bytes())
        merged, new = provision.merge_candidates(manifest, merged, candidates)
        self.assertEqual(new, 0)
        with self.assertRaises(provision.ProvisionError) as caught:
            provision.merge_candidates(manifest, base, [*candidates, {"schema_version": 1, "server": None, "models": {
                MODEL_KEY: {**self.model, "sha256": "3" * 64}}}])
        self.assertEqual(caught.exception.problem, f"{LOCK_CONFLICT}: $.models.{MODEL_KEY}")
        with self.assertRaises(provision.ProvisionError) as caught:
            provision.merge_candidates(manifest, {**base, "server": self.server},
                                       [{"schema_version": 1, "models": {}, "server": {**self.server,
                                                                                      "sha256": "4" * 64}}])
        self.assertEqual(caught.exception.problem, f"{LOCK_CONFLICT}: $.server")
        with self.assertRaises(provision.ProvisionError) as caught:
            provision.merge_candidates(manifest, base, [{"schema_version": 1, "server": None,
                                                         "models": {"no-such-model": self.model}}])
        self.assertTrue(caught.exception.problem.startswith("lock candidate $.models"), caught.exception.problem)


# --------------------------------------------------------------------------------------------------- prepare

class PrepareTests(WorldTest):
    def setUp(self) -> None:
        super().setUp()
        self.w = self.world()
        self.w.provision_all()
        self.out = self.tmp / "OUT"

    def _prepared(self) -> dict[str, Any]:
        return json.loads((self.out / "provision" / "prepare.json").read_text())

    def test_hit_no_download_and_fresh_extract(self) -> None:
        stale = self.out / "server" / "bin" / "stale.txt"
        stale.parent.mkdir(parents=True)
        stale.write_text("old")
        (self.out / "provision").mkdir()
        (self.out / "provision" / "old.json").write_text("{}")
        self.w.hub.requests.clear()
        gho = self.tmp / "gho"
        code, stdout, stderr = self.w.prepare(self.out, None, "--github-output", str(gho))
        self.assertEqual(code, 0, stderr)
        self.assertRegex(stdout.strip(), r"lab: prepare s001-tiny-gguf server cache model cache wall_s [0-9.]+")
        self.assertEqual(self.w.hub.requests, [])
        self.assertFalse(stale.exists())
        self.assertFalse((self.out / "provision" / "old.json").exists())
        prepared = self._prepared()
        binary = self.out / prepared["server"]["binary"]
        self.assertTrue(binary.is_file() and os.access(binary, os.X_OK))
        self.assertEqual(prepared["server"]["binary"], "server/bin/lab-test-server-b0000/bin/server-stub")
        self.assertEqual(prepared["server"]["libraries"],
                         ["libstub-base.so", "libstub-base.so.0", "libstub-base.so.0.1.0"])
        self.assertEqual(prepared["server"]["version"], "version: 0 (stub)")
        sha = _sha(self.w.blob)
        self.assertEqual((prepared["model"]["path"], prepared["model"]["sha256"], prepared["model"]["commit"]),
                         (os.path.abspath(self.w.cache / "gguf" / MODEL_KEY / f"{sha}.gguf"), sha, COMMIT))
        for name, record in (("server.json", "server"), ("model.json", f"gguf-{MODEL_KEY}")):
            copied = (self.out / "provision" / name).read_bytes()
            self.assertEqual(copied, (self.w.records / record / "provision.json").read_bytes())
        server_copy = (self.out / "provision" / "server.json").read_bytes()
        self.assertEqual(prepared["server"]["record_sha256"], _sha(server_copy))
        self.assertEqual(prepared["plan_sha256"], _sha(self.w.plan_path.read_bytes()))
        self.assertEqual(_outputs(gho), {"needed": "true", "prepared": "true"})
        env = self.w.stub_files("environ")[-1]
        self.assertEqual(sorted(env), ["HOME", "LANG", "PATH", "TMPDIR"])
        self.assertTrue(env["HOME"].startswith(str(self.out)) and env["TMPDIR"].startswith(str(self.out)))

    def test_corrupt_restored_redownloads_at_record_commit(self) -> None:
        sha = _sha(self.w.blob)
        (self.w.cache / "gguf" / MODEL_KEY / f"{sha}.gguf").write_bytes(b"GGUF corrupt")
        (self.w.cache / "server" / self.w.tag / self.w.asset).unlink()
        self.w.hub.requests.clear()
        code, stdout, stderr = self.w.prepare(self.out)
        self.assertEqual(code, 0, stderr)
        self.assertIn("server download model download", stdout)
        self.assertEqual(len(self.w.hub.requests_for("release_download")), 1)
        self.assertEqual(self.w.hub.requests_for("hf_api"), [])
        self.assertIn(f"/resolve/{COMMIT}/", self.w.hub.requests_for("resolve")[0]["path"])
        self.assertEqual((self.w.cache / "gguf" / MODEL_KEY / f"{sha}.gguf").read_bytes(), self.w.blob)
        self.assertEqual((self._prepared()["server"]["source"], self._prepared()["model"]["source"]),
                         ("download", "download"))

    def _refused(self, problem: str, *extra: str) -> str:
        code, stdout, stderr = self.w.prepare(self.out, None, *extra)
        self.assertEqual(code, 2, stderr)
        self.assertIn(problem, stderr.splitlines()[0])
        self.assertFalse((self.out / "provision" / "prepare.json").exists())
        return stderr

    def test_missing_model_record(self) -> None:
        shutil.rmtree(self.w.records / f"gguf-{MODEL_KEY}")
        self._refused(f"{PROVISION_FAILED_MODEL} {MODEL_KEY}")
        error = json.loads((self.out / "provision" / "prepare-error.json").read_text())
        self.assertEqual((error["kind"], error["exit_code"]), ("lab_prepare_error", 2))

    def test_missing_server_record(self) -> None:
        shutil.rmtree(self.w.records / "server")
        self._refused(PROVISION_FAILED_SERVER)

    def test_unverified_record(self) -> None:
        for target in ("server", f"gguf-{MODEL_KEY}"):
            path = self.w.records / target / "provision.json"
            original = path.read_bytes()
            record = json.loads(original)
            record["verified"] = False
            path.write_text(json.dumps(record))
            with self.subTest(target=target):
                self._refused(PROVISION_FAILED_SERVER if target == "server" else PROVISION_FAILED_MODEL)
            path.write_bytes(original)

    def test_record_for_other_plan(self) -> None:
        path = self.w.records / f"gguf-{MODEL_KEY}" / "provision.json"
        record = json.loads(path.read_text())
        record["plan_sha256"] = "9" * 64
        path.write_text(json.dumps(record))
        self._refused(RECORD_OTHER_PLAN)

    def test_newest_attempt_wins_and_tie_refused(self) -> None:
        original = json.loads((self.w.records / "server" / "provision.json").read_text())
        retry = {**original, "run_attempt": 2, "started_at": "retry"}
        write_json(self.w.records / "server-attempt-2" / "provision.json", retry)
        records = provision.load_records(self.w.records)
        self.assertEqual(records[("server", "")][0]["started_at"], "retry")
        self.assertEqual(sorted(records), [("gguf", MODEL_KEY), ("server", "")])
        write_json(self.w.records / "deep" / "a" / "b" / "c" / "provision.json", {**retry, "run_attempt": 9})
        self.assertEqual(provision.load_records(self.w.records)[("server", "")][0]["run_attempt"], 2)
        write_json(self.w.records / "server-attempt-2b" / "provision.json", {**retry, "started_at": "other"})
        with self.assertRaises(provision.ProvisionError) as caught:
            provision.load_records(self.w.records)
        self.assertEqual(caught.exception.problem, AMBIGUOUS_RECORDS)
        self._refused(AMBIGUOUS_RECORDS)
        write_json(self.w.records / "server-attempt-2b" / "provision.json", retry)
        self.assertEqual(provision.load_records(self.w.records)[("server", "")][0]["started_at"], "retry")

    def test_fake_shard_needs_nothing(self) -> None:
        plan, plan_path = make_plan(self.tmp / "fake", plumbing_min())
        from lab import shard as lab_shard
        gho = self.tmp / "gho"
        code, stdout, stderr = call_main(lab_shard, ["prepare", "--plan", str(plan_path), "--shard", "s001-fake-a",
                                                     "--provision-records", str(self.tmp / "none"), "--cache-root",
                                                     str(self.tmp / "c"), "--out", str(self.out), "--github-output",
                                                     str(gho)])
        self.assertEqual(code, 0, stderr)
        self.assertRegex(stdout.strip(), r"lab: prepare s001-fake-a needed false wall_s [0-9.]+")
        prepared = self._prepared()
        self.assertEqual((prepared["kind"], prepared["needed"], prepared["shard"]), ("lab_prepare", False,
                                                                                      "s001-fake-a"))
        self.assertEqual(_outputs(gho), {"needed": "false", "prepared": "true"})
        self.assertFalse((self.out / "server").exists())

    def test_memory_preflight(self) -> None:
        with mock.patch.object(provision, "mem_available", return_value=1 << 30):
            self._refused("not enough memory:")

    def test_binary_the_kernel_will_not_run(self) -> None:
        gho = self.tmp / "gho"
        refusal = OSError(errno.ENOEXEC, "Exec format error")
        with mock.patch("lab.server.subprocess.Popen", side_effect=refusal) as popen:
            stderr = self._refused(VERSION_FAILED, "--github-output", str(gho))
        self.assertEqual(popen.call_args.args[0][1:], ["--version"])
        self.assertTrue(any(line == f"::error::{VERSION_FAILED}" for line in stderr.splitlines()))
        error = json.loads((self.out / "provision" / "prepare-error.json").read_text())
        self.assertEqual((error["kind"], error["problem"], error["exit_code"]), ("lab_prepare_error", VERSION_FAILED,
                                                                                  2))
        self.assertEqual(_outputs(gho), {"needed": "true", "prepared": "false"})

    def test_digest_mismatch_twice_leaves_nothing(self) -> None:
        sha = _sha(self.w.blob)
        for target, route, final in (("server", "release_cdn", self.w.cache / "server" / self.w.tag / self.w.asset),
                                     ("model", "cdn", self.w.cache / "gguf" / MODEL_KEY / f"{sha}.gguf")):
            with self.subTest(target=target):
                final.unlink()
                self.w.hub.clear_faults()
                self.w.hub.requests.clear()
                self.w.hub.fault(route, wrong_bytes=True, times=2)
                self._refused(DIGEST_MISMATCH)
                self.assertEqual(len(self.w.hub.requests_for(route)), 2)
                self.assertFalse(final.exists())
                self.assertFalse(final.with_name(final.name + ".part").exists())
                self.assertEqual(sorted(p.name for p in final.parent.iterdir()), [])
                self.w.provision_all()      # restore both files for the next target

    def test_prepare_deadline_bounds_downloads(self) -> None:
        shutil.rmtree(self.w.cache)
        finals = (self.w.cache / "server" / self.w.tag / self.w.asset,
                  self.w.cache / "gguf" / MODEL_KEY / f"{_sha(self.w.blob)}.gguf")
        self.w.hub.requests.clear()
        code = provision.prepare(str(self.w.plan_path), "s001-tiny-gguf", str(self.w.records), self.w.cache, self.out,
                                 url_map=self.w.hub.url_map, sleep=self.w.sleeps.append, deadline_epoch=time.time() - 5)
        self.assertEqual(code, 3)
        error = json.loads((self.out / "provision" / "prepare-error.json").read_text())
        self.assertEqual((error["problem"], error["exit_code"]), (DOWNLOAD_DEADLINE, 3))
        self.assertEqual(self.w.hub.requests, [])
        for final in finals:
            self.assertFalse(final.exists() or final.with_name(final.name + ".part").exists())
        self.assertFalse((self.out / "provision" / "prepare.json").exists())
        code = provision.prepare(str(self.w.plan_path), "s001-tiny-gguf", str(self.w.records), self.w.cache, self.out,
                                 url_map=self.w.hub.url_map, sleep=self.w.sleeps.append,
                                 deadline_epoch=time.time() + provision.MIN_DOWNLOAD_S / 2)
        self.assertEqual(code, 3)       # time is left, but less than a download may start with
        self.assertEqual(self.w.hub.requests, [])
        self.assertFalse(finals[0].exists())

        gho = self.tmp / "gho"
        start = int(time.time()) - 3600
        code, stdout, stderr = self.w.prepare(self.out, None, "--job-start-epoch", str(start),
                                              "--job-timeout-minutes", "30", "--github-output", str(gho))
        self.assertEqual(code, 3, stderr)
        self.assertIn(DOWNLOAD_DEADLINE, stderr)
        self.assertEqual(_outputs(gho), {"needed": "true", "prepared": "false", "deadline_epoch": str(start + 1200),
                                         "shard_minutes": "1"})
        self.assertEqual(self.w.hub.requests, [])

        calls: list[float] = []
        real = dl.download

        def bounded(*args: Any, **kwargs: Any) -> dl.Download:
            calls.append(kwargs["deadline_s"])
            return real(*args, **kwargs)

        shutil.rmtree(self.out)
        with mock.patch.object(provision, "download", bounded):
            left = time.time() + 600
            code = provision.prepare(str(self.w.plan_path), "s001-tiny-gguf", str(self.w.records), self.w.cache,
                                     self.out, url_map=self.w.hub.url_map, sleep=self.w.sleeps.append,
                                     deadline_epoch=left)
        self.assertEqual(code, 0)
        self.assertEqual(len(calls), 2)
        for deadline_s in calls:
            self.assertTrue(500 < deadline_s <= 600, calls)
        for final in finals:
            self.assertTrue(final.is_file())
        calls.clear()
        shutil.rmtree(self.out)
        with mock.patch.object(provision, "download", bounded):
            code = provision.prepare(str(self.w.plan_path), "s001-tiny-gguf", str(self.w.records), self.w.cache,
                                     self.out, url_map=self.w.hub.url_map, deadline_epoch=time.time() - 5)
        self.assertEqual((code, calls), (0, []))   # the cache restored both files: hashing is not bounded

    def test_candidate_from_record(self) -> None:
        for target in ("server", f"gguf-{MODEL_KEY}"):
            with self.subTest(target=target):
                record = json.loads((self.w.records / target / "provision.json").read_text())
                candidate = provision.candidate_from_record(record)
                written = json.loads((self.w.records / target / "lock-candidate.json").read_text())
                self.assertEqual(candidate, written)
                self.assertEqual(list(candidate), ["schema_version", "server", "models"])
                if target == "server":
                    self.assertEqual(list(candidate["server"]), ["tag", "asset", "sha256"])
                    self.assertEqual(candidate["models"], {})
                else:
                    self.assertIsNone(candidate["server"])
                    self.assertEqual(list(candidate["models"][MODEL_KEY]),
                                     ["repo", "file", "revision", "commit", "sha256", "size"])

    def test_out_rules(self) -> None:
        (self.out / "units").mkdir(parents=True)
        self._refused(OUT_NOT_PREPARABLE)
        self.assertEqual(sorted(p.name for p in self.out.iterdir()), ["units"])
        from lab import shard as lab_shard
        cases = ((self.tmp / "o2", self.tmp / "o2" / "cache", CACHE_INSIDE_OUT),
                 (ROOT / "mycelic" / "lab-prepare-never-created", self.w.cache, FORBIDDEN_ROOT),
                 (self.tmp / "o3", ROOT / "research" / "lab-cache-never-created", FORBIDDEN_ROOT))
        for out, cache, problem in cases:
            with self.subTest(problem=problem, out=out.name):
                code, _, stderr = call_main(lab_shard, ["prepare", "--plan", str(self.w.plan_path), "--shard",
                                                        "s001-tiny-gguf", "--provision-records", str(self.w.records),
                                                        "--cache-root", str(cache), "--out", str(out)],
                                            url_map=self.w.hub.url_map)
                self.assertEqual(code, 2)
                self.assertIn(problem, stderr)
                self.assertFalse(out.exists())
        self.assertFalse((ROOT / "research" / "lab-cache-never-created").exists())


if __name__ == "__main__":
    unittest.main()
