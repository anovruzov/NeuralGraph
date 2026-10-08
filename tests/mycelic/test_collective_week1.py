"""The week-1 founder tools end to end, against local servers only: E3 latency, the openFDA connector, N1 sample
and score, and the dry-run contract shared by every CLI.

A module-level guard makes any ``socket.create_connection`` to a non-loopback address raise and records every
address that was connected to, so the connector tests can prove each connection went to the local stub.
"""
from __future__ import annotations

import contextlib
import csv
import io
import json
import logging
import os
import re
import shutil
import socket
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlsplit

from mycelic.collective import jsonio, stats
from mycelic.collective.connectors import openfda
from mycelic.collective.experiments import e3_latency, n1_narratives
from mycelic.collective.inference.fakeserver import FakeOpenAIServer
from mycelic.collective.inference.ledger import read_ledger

LOOPBACK = ("127.0.0.1", "::1", "localhost")
CONNECTIONS: list[tuple[str, int]] = []
_real_create_connection = socket.create_connection


def _loopback_only(address, *args, **kwargs):
    CONNECTIONS.append((address[0], address[1]))
    if address[0] not in LOOPBACK:
        raise OSError(f"test guard: refusing a non-loopback connection to {address[0]}")
    return _real_create_connection(address, *args, **kwargs)


def setUpModule() -> None:
    socket.create_connection = _loopback_only


def tearDownModule() -> None:
    socket.create_connection = _real_create_connection


def closed_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def run_cli(main, argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(argv)
    return code, out.getvalue(), err.getvalue()


def snapshot(root: Path) -> set[str]:
    return {str(p.relative_to(root)) for p in root.rglob("*")}


class TempCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)

    def server(self, persona: str = "stream", **kwargs) -> FakeOpenAIServer:
        srv = FakeOpenAIServer(persona, **kwargs).start()
        self.addCleanup(srv.stop)
        return srv

    def routing_file(self, endpoints: dict, name: str = "routing.json") -> Path:
        path = self.dir / name
        path.write_text(json.dumps({"schema_version": 1, "endpoints": endpoints, "routes": {}}))
        return path


# =================================================================================================== E3

FACTS = {"facts": ["the pump housing cracked", "the valve was replaced"]}
ANSWER = {"answer": "a routine service report about a pump"}


class E3SmokeTests(TempCase):
    def e3(self, srv: FakeOpenAIServer, *extra: str, run_id: str = "smoke") -> tuple[int, dict | None, str]:
        routing = self.routing_file({"fake-a": {"provider": "openai_compat", "boundary": "site:a",
                                                "base_url": srv.base_url, "model": "m-tag"}})
        code, out, err = run_cli(e3_latency.main, ["--routing", str(routing), "--endpoint", "fake-a", "--run-id",
                                                   run_id, "--runs-dir", str(self.dir / "runs"), *extra])
        path = self.dir / "runs" / "e3" / run_id / "e3.json"
        return code, (jsonio.load_json_file(path) if path.exists() else None), err

    def test_stream_persona_at_two_concurrency_levels(self) -> None:
        srv = self.server("stream", reply=FACTS)
        code, result, err = self.e3(srv, "--concurrency", "1,4", "--requests", "5", "--warmup", "1",
                                    "--workloads", "extraction", "--server-note", "fake server, rehearsal")
        self.assertEqual(code, 0, err)
        self.assertIs(result["measurement"], False)
        self.assertEqual((result["kind"], result["schema_version"], result["data_label"]), ("e3", 1, "synthetic"))
        for key in ("code_commit", "code_dirty", "code_hash", "routing_sha256", "started_at", "finished_at"):
            self.assertIn(key, result)
        self.assertEqual(len(result["cells"]), 2)
        for cell in result["cells"]:
            with self.subTest(concurrency=cell["concurrency"]):
                self.assertEqual((cell["sent"], cell["measured"], cell["ok"], cell["failures"]), (5, 4, 4, {}))
                self.assertEqual((cell["retried"], cell["transport_retries"]), (0, 0))
                self.assertGreaterEqual(cell["ttft_s"]["p50"], 0.3)
                self.assertLessEqual(cell["ttft_s"]["p50"], 1.5)
                self.assertEqual(set(cell["e2e_s"]), {"p50", "p95"})
                self.assertGreater(cell["decode_tok_s"]["p50"], 0)
                self.assertLessEqual(cell["decode_tok_s"]["p50"], 60)
                self.assertEqual(cell["prompt_tokens_p50"], 123)
                self.assertEqual(set(cell["throughput"]), {"wall_s", "requests_per_s", "completion_tokens_per_s"})
                self.assertIsNone(cell["energy"])
                self.assertEqual(cell["energy_note"], "not requested (pass --energy rapl)")
        self.assertEqual(set(result["host"]), {"platform", "python", "machine", "cpu_model", "cores_logical",
                                               "ram_bytes", "gpu", "governor", "server_note"})
        self.assertEqual(result["host"]["server_note"], "fake server, rehearsal")
        [endpoint] = result["endpoints"]
        self.assertEqual((endpoint["listed_fake"], endpoint["models_listed"], endpoint["models_served"]),
                         (True, ["fake-model"], ["m-tag"]))
        self.assertTrue(any("queue" in note for note in result["notes"]))
        rows = read_ledger(self.dir / "runs" / "e3" / "smoke" / "ledger.jsonl")
        self.assertEqual(len(rows), 10)
        self.assertTrue(all(r["fake_marker"] and r["boundary_mode"] == "external_raw_exempt" for r in rows))
        self.assertEqual(len(srv.chat_requests), 10)
        texts = [r["json"]["messages"][1]["content"] for r in srv.chat_requests]
        self.assertEqual(len(set(texts)), 10)     # distinct filler per request

    def test_a_thinking_server_is_timed_from_its_first_thinking_token_and_flagged(self) -> None:
        # regression (audit r2): against Ollama's and vLLM's field name, TTFT waited for the end of the thinking and
        # decode tokens/s came out about 20 times too high, with nothing in the cell saying a model was thinking
        timing = {"reply": ANSWER, "first_token_s": 0.0, "token_s": 0.02, "n_chunks": 5, "think_tokens": 20}
        cells = {}
        for field in ("reasoning", "reasoning_content"):
            srv = self.server("stream", think_field=field, **timing)
            code, result, err = self.e3(srv, "--concurrency", "1", "--requests", "3", "--warmup", "1",
                                        "--workloads", "short", run_id=f"think-{field.replace('_', '-')}")
            self.assertEqual(code, 0, err)
            [cells[field]] = result["cells"]
        for field, cell in cells.items():
            with self.subTest(field=field):
                self.assertEqual((cell["ok"], cell["thinking_requests"], cell["length_cut"]), (2, 2, 0))
                # 24 tokens over about 24 x 20 ms: about 50 tokens/s whichever field the server uses
                self.assertLess(cell["ttft_s"]["p50"], 0.2)
                self.assertLess(cell["decode_tok_s"]["p50"], 100)
        rates = [cells[field]["decode_tok_s"]["p50"] for field in ("reasoning", "reasoning_content")]
        self.assertLess(abs(rates[0] - rates[1]), 25)

    def test_thinking_that_spends_the_token_cap_is_counted(self) -> None:
        srv = self.server("thinks-by-default", reply=ANSWER, first_token_s=0.0, token_s=0.0, think_tokens=300)
        code, result, err = self.e3(srv, "--concurrency", "1", "--requests", "3", "--warmup", "1", "--workloads",
                                    "short", run_id="cut")
        self.assertEqual(code, 0, err)
        [cell] = result["cells"]
        self.assertEqual((cell["ok"], cell["failures"], cell["thinking_requests"], cell["length_cut"]),
                         (0, {"json_invalid": 2}, 2, 2))
        self.assertTrue(any("length_cut" in note for note in result["notes"]))

    def test_transport_retries_are_counted_per_cell(self) -> None:
        """Latencies time the final HTTP try only, so a cell says how many measured requests needed a retry."""
        srv = self.server("429-retry-after", reply=ANSWER, retry_after="0")       # the first chat request gets 429
        code, result, err = self.e3(srv, "--concurrency", "1", "--requests", "3", "--warmup", "0",
                                    "--workloads", "short")
        self.assertEqual(code, 0, err)
        [cell] = result["cells"]
        self.assertEqual((cell["ok"], cell["retried"], cell["transport_retries"], cell["failures"]), (3, 1, 1, {}))
        self.assertEqual(len(srv.chat_requests), 4)
        self.assertTrue(any("final HTTP try" in note and "retried" in note for note in result["notes"]))

    def test_short_workload_and_missing_usage(self) -> None:
        srv = self.server("no-usage", reply=ANSWER, first_token_s=0.05, token_s=0.005)
        code, result, err = self.e3(srv, "--concurrency", "2", "--requests", "3", "--warmup", "1",
                                    "--workloads", "short")
        self.assertEqual(code, 0, err)
        [cell] = result["cells"]
        self.assertEqual((cell["task"], cell["ok"]), ("e3_short_answer", 2))
        self.assertIsNone(cell["decode_tok_s"])
        self.assertIsNone(cell["prompt_tokens_p50"])
        self.assertIsNone(cell["throughput"]["completion_tokens_per_s"])
        self.assertIsNotNone(cell["ttft_s"])

    def test_single_chunk_gives_no_decode_rate(self) -> None:
        srv = self.server("stream", reply=ANSWER, n_chunks=1, first_token_s=0.05)
        code, result, err = self.e3(srv, "--concurrency", "1", "--requests", "2", "--warmup", "0",
                                    "--workloads", "short")
        self.assertEqual(code, 0, err)
        self.assertIsNone(result["cells"][0]["decode_tok_s"])

    def test_server_without_streaming(self) -> None:
        srv = self.server("stream-unsupported", reply=ANSWER)
        code, result, err = self.e3(srv, "--concurrency", "1", "--requests", "2", "--warmup", "0",
                                    "--workloads", "short")
        self.assertEqual(code, 0, err)
        cell = result["cells"][0]
        self.assertIsNone(cell["ttft_s"])
        self.assertIsNotNone(cell["e2e_s"])
        self.assertIsNone(cell["decode_tok_s"])

    def test_queueing_shows_in_ttft(self) -> None:
        srv = self.server("stream", reply=ANSWER, slots=1)
        code, result, err = self.e3(srv, "--concurrency", "4", "--requests", "5", "--warmup", "1",
                                    "--workloads", "short")
        self.assertEqual(code, 0, err)
        self.assertGreaterEqual(result["cells"][0]["ttft_s"]["p95"], 0.9)

    def test_rapl_energy_from_an_injected_counter(self) -> None:
        counter = self.dir / "energy_uj"
        counter.write_text("1000000\n")
        stop = threading.Event()

        def tick() -> None:
            value = 1000000
            while not stop.wait(0.01):
                value += 5000
                tmp = counter.with_name("energy_uj.tmp")
                tmp.write_text(f"{value}\n")
                os.replace(tmp, counter)

        ticker = threading.Thread(target=tick, daemon=True)
        ticker.start()
        self.addCleanup(stop.set)
        srv = self.server("stream", reply=ANSWER, first_token_s=0.05)
        code, result, err = self.e3(srv, "--concurrency", "1", "--requests", "3", "--warmup", "1",
                                    "--workloads", "short", "--energy", "rapl", "--powercap-path", str(counter))
        stop.set()
        self.assertEqual(code, 0, err)
        energy = result["cells"][0]["energy"]
        self.assertIsNotNone(energy)
        self.assertGreater(energy["joules"], 0)
        self.assertAlmostEqual(energy["wh"], energy["joules"] / 3600, places=9)
        self.assertEqual(energy["source"], str(counter))
        self.assertIn("idle", energy["scope"])

    def test_energy_counter_wraparound_and_unreadable(self) -> None:
        counter = self.dir / "energy_uj"
        (self.dir / "max_energy_range_uj").write_text("1000\n")
        reading, note = e3_latency.energy_reading(counter, 900, 100, 2)
        self.assertEqual((reading["joules"], note), (200 / 1e6, None))
        reading, note = e3_latency.energy_reading(counter, None, 100, 2)
        self.assertIsNone(reading)
        self.assertEqual(note, f"powercap not readable: {counter}")

    def test_existing_run_directory_and_fake_provider_are_refused(self) -> None:
        srv = self.server("stream", reply=ANSWER, first_token_s=0.0)
        (self.dir / "runs" / "e3" / "taken").mkdir(parents=True)
        code, _, err = self.e3(srv, "--requests", "1", "--warmup", "0", run_id="taken")
        self.assertEqual(code, 2)
        self.assertIn("already exists", err)
        fake = self.routing_file({"f": {"provider": "fake", "boundary": "any-simulated"}}, name="fake.json")
        code, _, err = run_cli(e3_latency.main, ["--routing", str(fake), "--endpoint", "f", "--run-id", "x",
                                                 "--runs-dir", str(self.dir / "runs")])
        self.assertEqual(code, 2)
        self.assertIn("provider", err)
        self.assertFalse((self.dir / "runs" / "e3" / "x").exists())

    def test_decode_rate_rules(self) -> None:
        def attempt(tokens_out, ttft, latency, chunks):
            row = {"tokens_out": tokens_out, "ttft_ms": ttft, "latency_ms": latency}
            return e3_latency.Attempt(row=row, output={}, content_chunks=chunks)

        self.assertAlmostEqual(e3_latency.decode_rate(attempt(21, 300.0, 700.0, 20)), 20 / 0.4)
        for args in ((None, 300.0, 700.0, 20), (1, 300.0, 700.0, 20), (21, None, 700.0, 20), (21, 300.0, 700.0, 1),
                     (21, 300.0, 301.0, 20)):
            with self.subTest(args=args):
                self.assertIsNone(e3_latency.decode_rate(attempt(*args)))

    def test_filler_is_seeded_and_neutral(self) -> None:
        a = e3_latency.filler(1500, "e3:7:extraction:c1:0")
        self.assertEqual(a, e3_latency.filler(1500, "e3:7:extraction:c1:0"))
        self.assertNotEqual(a, e3_latency.filler(1500, "e3:7:extraction:c1:1"))
        self.assertEqual(len(a.split()), 1500)
        self.assertTrue(set(re.findall(r"[a-z]+", a.lower())) <= set(e3_latency.WORDS))


# =================================================================================================== openFDA stub

def event(key: str, code: str, *, texts=(("Description of Event or Problem", "1", "the housing cracked"),),
          lot: str | None = "L-1", model: str | None = "M-1", problems=("Crack",), generic: str = "infusion pump",
          date: str = "20240105") -> dict:
    return {
        "mdr_report_key": key, "date_received": date, "event_type": "Malfunction",
        "device": [{"device_report_product_code": code, "generic_name": generic, "model_number": model,
                    "lot_number": lot}],
        "product_problems": list(problems),
        "mdr_text": [{"mdr_text_key": k, "text_type_code": t, "text": x, "date_report": date} for t, k, x in texts],
    }


class OpenFDAStub:
    """A stdlib server that answers like api.fda.gov's device endpoints (shape checked against the openFDA source)."""

    def __init__(self, records: dict[str, list[dict]], *, max_skip: int = 25000, fail_429: tuple[str, ...] = (),
                 status: dict[str, tuple[int, dict]] | None = None, redirect: dict[str, str] | None = None,
                 cut_short: tuple[str, ...] = ()) -> None:
        self.records = records
        self.cut_short = set(cut_short)
        self.max_skip = max_skip
        self.fail_429 = set(fail_429)
        self.status = status or {}
        self.redirect = redirect or {}
        self.requests: list[dict] = []
        self.lock = threading.Lock()

    def handle(self, h: BaseHTTPRequestHandler) -> None:
        parts = urlsplit(h.path)
        query = parse_qs(parts.query)
        with self.lock:
            self.requests.append({"path": parts.path, "query": query, "raw": h.path})
        search = query.get("search", [""])[0]
        m = re.search(r'"([A-Z]{3})"', search)
        code = m.group(1) if m else ""
        limit, skip = int(query.get("limit", ["100"])[0]), int(query.get("skip", ["0"])[0])
        if code in self.fail_429:
            self.fail_429.discard(code)
            return self.send(h, 429, {"error": {"code": "TOO_MANY", "message": "slow down"}})
        if code in self.status:
            return self.send(h, *self.status[code])
        if code in self.redirect:
            h.send_response(302)
            h.send_header("Location", self.redirect[code])
            h.send_header("Content-Length", "0")
            h.end_headers()
            return None
        if skip > self.max_skip:
            return self.send(h, 400, {"error": {"code": "BAD_REQUEST",
                                                "message": f"Skip value must {self.max_skip} or less."}})
        recs = self.records.get(code, [])
        page = recs[skip:skip + limit]
        if not page:
            return self.send(h, 404, {"error": {"code": "NOT_FOUND", "message": "No matches found!"}})
        meta = {"disclaimer": "test", "results": {"skip": skip, "limit": limit, "total": len(recs)}}
        if code in self.cut_short:           # promise the whole page, send half, hang up
            self.cut_short.discard(code)
            data = json.dumps({"meta": meta, "results": page}).encode("utf-8")
            h.send_response(200)
            h.send_header("Content-Length", str(len(data)))
            h.end_headers()
            h.wfile.write(data[: len(data) // 2])
            return None
        return self.send(h, 200, {"meta": meta, "results": page})

    @staticmethod
    def send(h: BaseHTTPRequestHandler, status: int, body: dict) -> None:
        data = json.dumps(body).encode("utf-8")
        h.send_response(status)
        h.send_header("Content-Type", "application/json")
        h.send_header("Content-Length", str(len(data)))
        h.end_headers()
        h.wfile.write(data)

    def __enter__(self) -> "OpenFDAStub":
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                stub.handle(self)

            def log_message(self, *args) -> None:
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self.base_url = f"http://127.0.0.1:{self.port}"
        self.thread = threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


def fixture_records() -> dict[str, list[dict]]:
    aaa = [event(f"10{i:02d}", "AAA", lot=None if i % 3 == 0 else f"L-{i}", model=None if i == 4 else "M-2",
                 problems=() if i == 5 else ("Crack",),
                 texts=() if i == 6 else (("Description of Event or Problem", "1", f"cracked unit {i}"),))
           for i in range(7)]
    ccc = [event(f"30{i:02d}", "CCC") for i in range(12)]
    return {"AAA": aaa, "CCC": ccc}


class OpenFDAConnectorTests(TempCase):
    def fetch(self, stub: OpenFDAStub, codes: list[str], **kwargs) -> dict:
        kwargs.setdefault("sleep", lambda s: None)
        kwargs.setdefault("clock", lambda: "2026-01-01T00:00:00.000Z")
        kwargs.setdefault("environ", {})
        return openfda.fetch("event", codes, "20240101", "20240331", self.dir / "cache", base_url=stub.base_url,
                             **kwargs)

    def test_paging_max_skip_not_found_coverage_and_hashes(self) -> None:
        records = fixture_records()
        with OpenFDAStub(records, max_skip=4) as stub:
            CONNECTIONS.clear()
            manifest = self.fetch(stub, ["AAA", "BBB", "CCC"], limit=3)
            self.assertEqual({port for _, port in CONNECTIONS}, {stub.port})
            self.assertEqual({host for host, _ in CONNECTIONS}, {"127.0.0.1"})
        self.assertEqual(manifest["per_code"]["AAA"], {"total": 7, "fetched": 6, "truncated": True,
                                                       "reason": "api_max_skip", "max_skip": 4})
        self.assertEqual(manifest["per_code"]["BBB"], {"total": 0, "fetched": 0, "truncated": False, "reason": None,
                                                       "max_skip": None})
        self.assertEqual(manifest["per_code"]["CCC"], {"total": 12, "fetched": 6, "truncated": True,
                                                       "reason": "api_max_skip", "max_skip": 4})
        self.assertEqual(manifest["data_label"], "synthetic")
        self.assertEqual(manifest["fetched_at"], "2026-01-01T00:00:00.000Z")
        self.assertEqual([p["skip"] for p in manifest["pages"]], [0, 3, 0, 3])
        for page in manifest["pages"]:
            data = (self.dir / "cache" / page["path"]).read_bytes()
            self.assertEqual((page["sha256"], page["bytes"]), (jsonio.sha256_hex(data), len(data)))
            self.assertNotIn("api_key", page["url"])
            self.assertTrue(page["path"].startswith(f"pages/device_event/{page['product_code']}/page-"))
        self.assertEqual(jsonio.load_json_file(self.dir / "cache" / "manifest.json"), manifest)

        fetched_aaa = records["AAA"][:6]
        expected = {
            "lot_number": sum(1 for r in fetched_aaa if r["device"][0]["lot_number"]),
            "model_number": sum(1 for r in fetched_aaa if r["device"][0]["model_number"]),
            "product_problems": sum(1 for r in fetched_aaa if r["product_problems"]),
            "mdr_text": sum(1 for r in fetched_aaa if r["mdr_text"]),
        }
        cov = manifest["coverage"]["per_code"]["AAA"]
        self.assertEqual(cov["records"], 6)
        self.assertEqual({f: cov[f]["count"] for f in expected}, expected)
        self.assertEqual(manifest["coverage"]["overall"]["records"], 12)
        self.assertIsNone(manifest["coverage"]["per_code"]["BBB"]["lot_number"]["share"])

        cache = openfda.load_cache(self.dir / "cache")
        keys = [r["mdr_report_key"] for _, r in cache.records()]
        self.assertEqual(keys, [r["mdr_report_key"] for r in records["AAA"][:6] + records["CCC"][:6]])

    def test_tampered_page_or_missing_manifest_is_refused(self) -> None:
        with OpenFDAStub(fixture_records()) as stub:
            manifest = self.fetch(stub, ["AAA"])
        page = self.dir / "cache" / manifest["pages"][0]["path"]
        page.write_bytes(page.read_bytes() + b" ")
        with self.assertRaises(openfda.CacheError):
            openfda.load_cache(self.dir / "cache")
        (self.dir / "cache" / "manifest.json").unlink()
        with self.assertRaises(openfda.CacheError):
            openfda.load_cache(self.dir / "cache")

    def test_429_is_retried_after_the_backoff(self) -> None:
        slept: list[float] = []
        with OpenFDAStub(fixture_records(), fail_429=("AAA",)) as stub:
            manifest = self.fetch(stub, ["AAA"], sleep=slept.append)
            self.assertEqual(len(stub.requests), 2)
        self.assertEqual(slept, [1.0])
        self.assertEqual(manifest["per_code"]["AAA"]["fetched"], 7)

    def test_page_cut_short_is_retried_as_a_network_failure(self) -> None:
        slept: list[float] = []
        with OpenFDAStub(fixture_records(), cut_short=("AAA",)) as stub:
            manifest = self.fetch(stub, ["AAA"], sleep=slept.append)
            self.assertEqual(len(stub.requests), 2)
        self.assertEqual(slept, [1.0])
        self.assertEqual(manifest["per_code"]["AAA"]["fetched"], 7)
        page = self.dir / "cache" / manifest["pages"][0]["path"]
        self.assertEqual(len(json.loads(page.read_bytes())["results"]), 7)

    def test_max_records_truncates(self) -> None:
        with OpenFDAStub(fixture_records()) as stub:
            manifest = self.fetch(stub, ["CCC"], limit=5, max_records=7)
            self.assertEqual([q["query"]["limit"][0] for q in stub.requests], ["5", "2"])
        self.assertEqual(manifest["per_code"]["CCC"], {"total": 12, "fetched": 7, "truncated": True,
                                                       "reason": "max_records", "max_skip": None})

    def test_other_errors_and_redirects(self) -> None:
        cases = [
            ({"status": {"AAA": (403, {"error": {"code": "FORBIDDEN", "message": "detail text"}})}}, "HTTP 403",
             "FORBIDDEN"),
            ({"status": {"AAA": (404, {"error": {"code": "OTHER", "message": "x"}})}}, "HTTP 404", "OTHER"),
            ({"redirect": {"AAA": "http://127.0.0.1:9/elsewhere"}}, "HTTP 302", "redirects are refused"),
        ]
        for i, (kwargs, status, detail) in enumerate(cases):
            with self.subTest(status=status), OpenFDAStub(fixture_records(), **kwargs) as stub:
                with self.assertRaises(openfda.OpenFDAError) as ctx:
                    openfda.fetch("event", ["AAA"], "20240101", "20240331", self.dir / f"c{i}",
                                  base_url=stub.base_url, sleep=lambda s: None, environ={})
                self.assertIn(status, str(ctx.exception))
                self.assertIn(detail, str(ctx.exception))
                self.assertNotIn("detail text", str(ctx.exception))
                self.assertEqual(ctx.exception.exit_code, 2)
                self.assertFalse((self.dir / f"c{i}" / "manifest.json").exists())

    def test_api_key_is_sent_but_never_written_printed_or_logged(self) -> None:
        key = "openfda-CANARY-KEY-77d2"
        log = io.StringIO()
        handler = logging.StreamHandler(log)
        root = logging.getLogger()
        root.addHandler(handler)
        old_level = root.level
        root.setLevel(logging.DEBUG)
        self.addCleanup(root.setLevel, old_level)
        self.addCleanup(root.removeHandler, handler)
        with OpenFDAStub(fixture_records(), max_skip=4) as stub, mock.patch.dict(os.environ, {"OPENFDA_API_KEY": key}):
            code, out, err = run_cli(openfda.main, [
                "fetch", "--dataset", "event", "--product-codes", "AAA,BBB", "--date-from", "20240101",
                "--date-to", "20240331", "--out", str(self.dir / "cache"), "--base-url", stub.base_url,
                "--limit", "3", "--api-key-env", "OPENFDA_API_KEY"])
            self.assertTrue(stub.requests)
            self.assertTrue(all(r["query"].get("api_key") == [key] for r in stub.requests))
        self.assertEqual(code, 0, err)
        for path in (self.dir / "cache").rglob("*"):
            if path.is_file():
                self.assertNotIn(key.encode("utf-8"), path.read_bytes(), path)
        for text in (out, err, log.getvalue()):
            self.assertNotIn(key, text)
        self.assertIn("data_label=synthetic", out)

    def test_unset_key_variable_is_a_usage_error(self) -> None:
        with self.assertRaises(openfda.OpenFDAError) as ctx:
            openfda.fetch("event", ["AAA"], "20240101", "20240331", self.dir / "c", base_url="http://127.0.0.1:9",
                          api_key_env="NOT_SET_ANYWHERE", environ={})
        self.assertEqual(ctx.exception.exit_code, 2)

    def test_closed_port_exits_3_and_writes_nothing(self) -> None:
        port = closed_port()
        with mock.patch.object(openfda.time, "sleep") as sleeper:
            code, out, err = run_cli(openfda.main, [
                "fetch", "--dataset", "event", "--product-codes", "AAA", "--date-from", "20240101", "--date-to",
                "20240331", "--out", str(self.dir / "cache"), "--base-url", f"http://127.0.0.1:{port}"])
        self.assertEqual(code, 3)
        self.assertEqual(sleeper.call_count, openfda.NETWORK_RETRIES)
        self.assertIn(f"cannot reach http://127.0.0.1:{port}", err)
        self.assertIn("ConnectionRefusedError", err)
        self.assertFalse((self.dir / "cache").exists())

    def test_dead_proxy_environment_does_not_affect_loopback(self) -> None:
        dead = f"http://127.0.0.1:{closed_port()}"
        with OpenFDAStub(fixture_records()) as stub:
            manifest = self.fetch(stub, ["AAA"], environ={"http_proxy": dead, "HTTP_PROXY": dead,
                                                          "https_proxy": dead})
        self.assertEqual(manifest["per_code"]["AAA"]["fetched"], 7)

    def test_query_validation(self) -> None:
        bad = [(["aaa"], "20240101", "20240131", 10), (["AAAA"], "20240101", "20240131", 10),
               (["AAA"], "20240231", "20240301", 10), (["AAA"], "20240301", "20240101", 10),
               (["AAA"], "2024010", "20240131", 10), (["AAA"], "20240101", "20240131", 1001),
               (["AAA", "AAA"], "20240101", "20240131", 10)]
        for codes, start, end, limit in bad:
            with self.subTest(codes=codes, start=start, end=end, limit=limit), \
                    self.assertRaises(openfda.OpenFDAError):
                openfda.check_query("event", codes, start, end, limit, None, openfda.DEFAULT_BASE_URL)
        url = openfda.page_url("https://api.fda.gov", "event", "AAA", "20240101", "20240131", 1000, 0)
        self.assertEqual(parse_qs(urlsplit(url).query)["search"],
                         ['device.device_report_product_code:"AAA" AND date_received:[20240101 TO 20240131]'])
        recall = openfda.page_url("https://api.fda.gov", "recall", "AAA", "20240101", "20240131", 10, 20)
        self.assertTrue(recall.startswith("https://api.fda.gov/device/recall.json?search=product_code"))


# =================================================================================================== N1

A_TYPE = "Description of Event or Problem"
B_TYPE = "Additional Manufacturer Narrative"


def n1_records() -> dict[str, list[dict]]:
    aaa = [event(f"1{i:03d}", "AAA", texts=((A_TYPE, "1", f"pump {i} cracked"),)) for i in range(110)]
    aaa.append(event("1900", "AAA", texts=()))                                       # no mdr_text
    aaa.append(event("1901", "AAA", texts=((B_TYPE, "1", "manufacturer note only"),)))  # no selected text type
    aaa.append(event("1902", "AAA", texts=((B_TYPE, "2", "second"), (A_TYPE, "10", "ten"), (A_TYPE, "9", "nine"),
                                           (A_TYPE, "x7", "letters"))))
    bbb = [event(f"2{i:03d}", "BBB") for i in range(110)]
    bbb += [event("1000", "BBB"), event("1001", "BBB")]                               # duplicates of AAA events
    ccc = [event(f"3{i:03d}", "CCC") for i in range(40)]                                # short of its share
    ddd = [event(f"4{i:03d}", "DDD") for i in range(110)]
    return {"AAA": aaa, "BBB": bbb, "CCC": ccc, "DDD": ddd}


class N1Tests(TempCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.cache = Path(cls._tmp.name) / "cache"
        with OpenFDAStub(n1_records()) as stub:
            openfda.fetch("event", ["AAA", "BBB", "CCC", "DDD"], "20240101", "20240331", cls.cache,
                          base_url=stub.base_url, sleep=lambda s: None, environ={})

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def sample(self, run_id: str, *extra: str, codes: str = "AAA,BBB,CCC") -> tuple[int, Path, str]:
        code, out, err = run_cli(n1_narratives.main, ["sample", "--cache", str(self.cache), "--product-codes", codes,
                                                      "--run-id", run_id, "--runs-dir", str(self.dir / "runs"),
                                                      *extra])
        return code, self.dir / "runs" / "n1" / run_id, err

    def test_sampling_is_seeded_stratified_and_counts_exclusions(self) -> None:
        code, d1, err = self.sample("s1", "--n", "300", "--seed", "1")
        self.assertEqual(code, 0, err)
        code, d2, _ = self.sample("s2", "--n", "300", "--seed", "1")
        code, d3, _ = self.sample("s3", "--n", "300", "--seed", "2")
        s1, s2, s3 = (jsonio.load_json_file(d / "sample.json") for d in (d1, d2, d3))
        self.assertEqual((s1["sheet_jsonl_sha256"], s1["sheet_csv_sha256"]),
                         (s2["sheet_jsonl_sha256"], s2["sheet_csv_sha256"]))
        self.assertEqual((d1 / "sheet.csv").read_bytes(), (d2 / "sheet.csv").read_bytes())
        self.assertNotEqual(s1["sheet_jsonl_sha256"], s3["sheet_jsonl_sha256"])
        self.assertEqual(jsonio.sha256_hex((d1 / "sheet.jsonl").read_bytes()), s1["sheet_jsonl_sha256"])

        self.assertEqual(s1["allocation"], {"AAA": 100, "BBB": 100, "CCC": 40})
        self.assertEqual(s1["shortfalls"], {"CCC": {"requested": 100, "available": 40, "shortfall": 60}})
        self.assertEqual(s1["n_sampled"], 240)
        self.assertEqual(s1["exclusions"]["AAA"], {"no_mdr_text": 1, "no_selected_text_type": 1, "duplicate": 0,
                                                   "no_report_key": 0})
        self.assertEqual(s1["exclusions"]["BBB"]["duplicate"], 2)
        self.assertEqual((s1["data_label"], s1["measurement"]), ("synthetic", False))
        self.assertEqual(s1["truncated_codes"], [])
        for key in ("code_commit", "code_dirty", "code_hash", "cache_manifest_sha256"):
            self.assertIn(key, s1)

        rows = [json.loads(line) for line in (d1 / "sheet.jsonl").read_text().splitlines()]
        self.assertEqual([r["row"] for r in rows], list(range(1, 241)))
        counts: dict[str, int] = {}
        for r in rows:
            counts[r["product_code"]] = counts.get(r["product_code"], 0) + 1
            for column in n1_narratives.LABEL_COLUMNS + ("labeller_note",):
                self.assertIsNone(r[column])
        self.assertEqual(counts, s1["allocation"])
        self.assertEqual(len({r["mdr_report_key"] for r in rows}), 240)
        self.assertNotEqual([r["product_code"] for r in rows], sorted(r["product_code"] for r in rows))
        with open(d1 / "sheet.csv", newline="", encoding="utf-8") as fh:
            table = list(csv.DictReader(fh))
        self.assertEqual(list(table[0]), list(n1_narratives.COLUMNS))
        for r in table:
            for column in n1_narratives.LABEL_COLUMNS:
                self.assertEqual(r[column], "")
        self.assertFalse((d1 / "sheet.csv").read_bytes().startswith(b"\xef\xbb\xbf"))
        self.assertNotIn(b"\r\n", (d1 / "sheet.csv").read_bytes())

    def test_narrative_concatenation_order(self) -> None:
        record = n1_records()["AAA"][-1]
        self.assertEqual(n1_narratives.narrative(record, [A_TYPE]), "nine\n\nten\n\nletters")
        self.assertEqual(n1_narratives.narrative(record, [A_TYPE, B_TYPE]), "nine\n\nten\n\nletters\n\nsecond")
        self.assertEqual(n1_narratives.narrative(record, [B_TYPE, A_TYPE]), "second\n\nnine\n\nten\n\nletters")

    def test_selected_text_types_change_eligibility(self) -> None:
        code, d, err = self.sample("tt", "--n", "30", "--text-types", B_TYPE)
        self.assertEqual(code, 0, err)
        sample = jsonio.load_json_file(d / "sample.json")
        self.assertEqual(sample["text_types"], [B_TYPE])
        self.assertEqual(sample["allocation"]["AAA"], 2)

    def test_a_tampered_cache_is_refused(self) -> None:
        copy = self.dir / "cache-copy"
        shutil.copytree(self.cache, copy)
        manifest = jsonio.load_json_file(copy / "manifest.json")
        page = copy / manifest["pages"][-1]["path"]
        page.write_bytes(page.read_bytes().replace(b"cracked", b"CRACKED", 1))
        code, out, err = run_cli(n1_narratives.main, ["sample", "--cache", str(copy), "--product-codes",
                                                      "AAA,BBB,CCC", "--run-id", "tampered", "--runs-dir",
                                                      str(self.dir / "runs")])
        self.assertEqual(code, 2)
        self.assertIn("sha256 mismatch", err)
        self.assertFalse((self.dir / "runs" / "n1" / "tampered").exists())

    def test_codes_must_be_three_to_five_and_in_the_cache(self) -> None:
        for codes in ("AAA,BBB", "AAA,BBB,ZZZ"):
            with self.subTest(codes=codes):
                code, d, err = self.sample("bad", codes=codes)
                self.assertEqual(code, 2)
                self.assertFalse(d.exists())

    def labelled(self, sample_dir: Path, labels: dict[int, dict[str, str]], *, default: str = "false",
                 bom: bool = False, drop_rows: int = 0) -> Path:
        with open(sample_dir / "sheet.csv", newline="", encoding="utf-8") as fh:
            table = list(csv.DictReader(fh))
        for r in table:
            for column in n1_narratives.LABEL_COLUMNS:
                r[column] = labels.get(int(r["row"]), {}).get(column, default)
        table = table[drop_rows:]
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=list(n1_narratives.COLUMNS), lineterminator="\r\n")
        writer.writeheader()
        writer.writerows(table)
        path = self.dir / f"labelled-{len(list(self.dir.glob('labelled-*')))}.csv"
        path.write_bytes(("﻿" if bom else "").encode("utf-8") + buf.getvalue().encode("utf-8"))
        return path

    def score(self, sheet: Path, sample: Path, out: Path) -> tuple[int, str, str]:
        return run_cli(n1_narratives.main, ["score", "--sheet", str(sheet), "--sample", str(sample), "--out", str(out)])

    def test_score_bars_and_wilson_interval(self) -> None:
        code, d, err = self.sample("full", "--n", "300", codes="AAA,BBB,DDD")
        self.assertEqual(code, 0, err)
        n = jsonio.load_json_file(d / "sample.json")["n_sampled"]
        self.assertEqual(n, 300)
        for k, verdict in ((60, "supports"), (29, "expect_S_matches_X"), (45, "ambiguous")):
            with self.subTest(k=k):
                labels = {row: {"lot_not_in_codes": "TRUE"} for row in range(1, k + 1)}
                sheet = self.labelled(d, labels, default="No", bom=True)
                out = self.dir / f"gain-{k}.json"
                code, stdout, err = self.score(sheet, d / "sample.json", out)
                self.assertEqual(code, 0, err)
                gain = jsonio.load_json_file(out)
                self.assertEqual((gain["n"], gain["any_true"], gain["verdict"]), (n, k, verdict))
                self.assertAlmostEqual(gain["share"], k / n, places=12)
                if k == 60:
                    self.assertEqual(gain["share"], 0.2)
                lo, hi = stats.wilson(k, n)
                self.assertAlmostEqual(gain["ci95"][0], lo, delta=1e-9)
                self.assertAlmostEqual(gain["ci95"][1], hi, delta=1e-9)
                self.assertEqual(gain["per_column"]["lot_not_in_codes"]["k"], k)
                self.assertEqual(gain["per_column"]["component_not_in_codes"]["k"], 0)
                self.assertEqual(sum(v["n"] for v in gain["per_product_code"].values()), n)
                self.assertEqual(gain["caveat"], "MAUDE holds reportable events, not internal complaints.")
                self.assertEqual(gain["bars"], {"supports_at_or_above": 0.2, "expect_S_matches_X_below": 0.1})
                self.assertEqual((gain["data_label"], gain["measurement"], gain["ci_method"]),
                                 ("synthetic", False, "wilson"))
                for key in ("code_commit", "code_dirty", "code_hash", "sample_sha256", "sheet_sha256"):
                    self.assertIn(key, gain)

    def test_verdict_bars_at_300(self) -> None:
        self.assertEqual(n1_narratives.verdict(60, 300), "supports")
        self.assertEqual(n1_narratives.verdict(59, 300), "ambiguous")
        self.assertEqual(n1_narratives.verdict(29, 300), "expect_S_matches_X")
        self.assertEqual(n1_narratives.verdict(30, 300), "ambiguous")
        self.assertEqual(n1_narratives.verdict(45, 300), "ambiguous")
        sample = {"rows": [{"mdr_report_key": str(i), "product_code": "AAA" if i < 150 else "BBB"}
                           for i in range(300)]}
        rows = [{"mdr_report_key": str(i), **{c: ("y" if i < 60 and c == "component_not_in_codes" else "n")
                                              for c in n1_narratives.LABEL_COLUMNS}} for i in range(300)]
        result = n1_narratives.score(rows, sample)
        self.assertEqual((result["share"], result["verdict"]), (0.2, "supports"))
        lo, hi = stats.wilson(60, 300)
        self.assertAlmostEqual(result["ci95"][0], lo, delta=1e-9)
        self.assertAlmostEqual(result["ci95"][1], hi, delta=1e-9)
        self.assertEqual(result["per_product_code"]["AAA"]["k"], 60)

    def test_unlabelled_rows_and_key_mismatch_exit_2(self) -> None:
        code, d, err = self.sample("lab", "--n", "30")
        self.assertEqual(code, 0, err)
        sheet = self.labelled(d, {3: {"component_not_in_codes": ""}, 7: {"use_condition_not_in_codes": "maybe"}})
        code, _, err = self.score(sheet, d / "sample.json", self.dir / "o1.json")
        self.assertEqual(code, 2)
        self.assertIn("2 rows are not fully labelled", err)
        self.assertIn("3, 7", err)
        self.assertFalse((self.dir / "o1.json").exists())
        sheet = self.labelled(d, {}, drop_rows=1)
        code, _, err = self.score(sheet, d / "sample.json", self.dir / "o2.json")
        self.assertEqual(code, 2)
        self.assertIn("differ from the sample", err)
        good = self.labelled(d, {})
        (self.dir / "taken.json").write_text("{}")
        code, _, err = self.score(good, d / "sample.json", self.dir / "taken.json")
        self.assertEqual(code, 2)
        self.assertIn("already exists", err)

    def test_label_tokens(self) -> None:
        for token, value in (("true", True), ("Yes", True), ("Y", True), ("1", True), (" FALSE ", False),
                             ("no", False), ("n", False), ("0", False), ("", None), ("maybe", None), (None, None)):
            with self.subTest(token=token):
                self.assertIs(n1_narratives.parse_label(token), value)


# =================================================================================================== dry runs

class DryRunTests(TempCase):
    def run_dry(self, main, argv: list[str]) -> tuple[int, str, str]:
        def no_network(*args, **kwargs):
            raise AssertionError("a dry run tried to open a socket")

        def no_subprocess(*args, **kwargs):
            raise AssertionError("a dry run tried to run a subprocess")

        before = snapshot(self.dir)
        with mock.patch.object(socket, "create_connection", no_network), \
                mock.patch.object(socket.socket, "connect", no_network), \
                mock.patch.object(subprocess, "run", no_subprocess):
            result = run_cli(main, argv + ["--dry-run"])
        self.assertEqual(snapshot(self.dir), before)
        return result

    def test_missing_inputs_are_listed_and_nothing_is_created(self) -> None:
        missing = self.dir / "missing"
        cases = [
            (e3_latency.main, ["--routing", str(missing / "r.json"), "--endpoint", "a", "--run-id", "d1",
                               "--runs-dir", str(self.dir / "runs"), "--energy", "rapl", "--powercap-path",
                               str(missing / "energy_uj")], "e3_latency", 2),
            (openfda.main, ["fetch", "--dataset", "event", "--product-codes", "AAA,BBB", "--date-from", "20240101",
                            "--date-to", "20240131", "--out", str(missing / "cache"), "--api-key-env",
                            "DRY_RUN_UNSET_KEY"], "openfda fetch", 2),
            (n1_narratives.main, ["sample", "--cache", str(missing / "cache"), "--product-codes", "AAA,BBB,CCC",
                                  "--run-id", "d1", "--runs-dir", str(self.dir / "runs")],
             "n1_narratives sample", 1),
            (n1_narratives.main, ["score", "--sheet", str(missing / "s.csv"), "--sample", str(missing / "x.json"),
                                  "--out", str(missing / "out.json")], "n1_narratives score", 2),
        ]
        for main, argv, cli, needs in cases:
            with self.subTest(cli=cli):
                code, out, err = self.run_dry(main, argv)
                self.assertEqual(code, 0, err)
                lines = out.splitlines()
                self.assertEqual(lines[0], f"dry-run: {cli}")
                self.assertGreaterEqual(sum(1 for line in lines if line.startswith("would need: ")), needs)
                self.assertTrue(any(line.startswith("would write: ") for line in lines))
        self.assertFalse((self.dir / "runs").exists())

    def test_e3_dry_run_with_a_routing_file(self) -> None:
        routing = self.routing_file({"a": {"provider": "openai_compat", "boundary": "site:a",
                                           "base_url": "http://127.0.0.1:8080/v1", "model": "m",
                                           "api_key_env": "DRY_RUN_UNSET_KEY"}})
        code, out, err = self.run_dry(e3_latency.main, ["--routing", str(routing), "--endpoint", "a", "--run-id",
                                                        "d2", "--runs-dir", str(self.dir / "runs")])
        self.assertEqual(code, 0, err)
        self.assertIn("would need: env DRY_RUN_UNSET_KEY", out)
        self.assertIn("would need: network 127.0.0.1:8080", out)

    def test_invalid_existing_inputs_exit_2(self) -> None:
        bad = self.dir / "bad.json"
        bad.write_text('{"schema_version": 1, "endpoints": {}, "routes": {}, "extra": 1}')
        code, _, err = self.run_dry(e3_latency.main, ["--routing", str(bad), "--endpoint", "a", "--run-id", "d3",
                                                      "--runs-dir", str(self.dir / "runs")])
        self.assertEqual(code, 2)
        self.assertIn("$.extra", err)
        unknown = self.routing_file({"a": {"provider": "openai_compat", "boundary": "site:a",
                                           "base_url": "http://127.0.0.1:8080/v1", "model": "m"}}, name="ok.json")
        code, _, err = self.run_dry(e3_latency.main, ["--routing", str(unknown), "--endpoint", "zzz", "--run-id",
                                                      "d4", "--runs-dir", str(self.dir / "runs")])
        self.assertEqual(code, 2)
        (self.dir / "runs" / "e3" / "taken").mkdir(parents=True)
        code, _, err = self.run_dry(e3_latency.main, ["--routing", str(unknown), "--endpoint", "a", "--run-id",
                                                      "taken", "--runs-dir", str(self.dir / "runs")])
        self.assertEqual(code, 2)
        cache = self.dir / "not-a-cache"
        cache.mkdir()
        code, _, err = self.run_dry(n1_narratives.main, ["sample", "--cache", str(cache), "--product-codes",
                                                         "AAA,BBB,CCC", "--run-id", "d5", "--runs-dir",
                                                         str(self.dir / "runs")])
        self.assertEqual(code, 2)
        self.assertIn("manifest", err)
        bad_sample = self.dir / "bad-sample.json"
        bad_sample.write_text('{"kind": "n1_sample", "rows": [{"mdr_report_key": 1}]}')
        code, _, err = self.run_dry(n1_narratives.main, ["score", "--sheet", str(self.dir / "nope.csv"), "--sample",
                                                         str(bad_sample), "--out", str(self.dir / "o.json")])
        self.assertEqual(code, 2)
        self.assertIn("not an n1 sample", err)
        sheet = self.dir / "sheet.csv"
        sheet.write_text("a,b\n1,2\n")
        code, _, err = self.run_dry(n1_narratives.main, ["score", "--sheet", str(sheet), "--sample",
                                                         str(self.dir / "nope.json"), "--out",
                                                         str(self.dir / "o.json")])
        self.assertEqual(code, 2)
        self.assertIn("lacks columns", err)


if __name__ == "__main__":
    unittest.main()
