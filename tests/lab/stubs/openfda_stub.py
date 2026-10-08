"""A loopback stand-in for api.fda.gov's device event and recall endpoints, and an openFDA-shaped fixture.

:class:`OpenFDAStub` answers ``GET /device/event.json`` and ``GET /device/recall.json`` the way the connector expects
(``meta.results.total``, pages by ``limit`` and ``skip``, 404 ``NOT_FOUND`` for no records) from per-dataset,
per-product-code record lists, and records every request: its dataset path, its search, and the ``api_key`` query
value or None (:attr:`OpenFDAStub.requests`). Modes: ``fail_429`` answers every request 429 with ``Retry-After: 0``
(the connector's retries are exhausted at once); ``all_404`` answers every request 404 ``NOT_FOUND``.

:func:`fixture` builds events from a seeded synthetic world (each record an event of ``MAKER`` with its narrative as
the event text, its site as ``event_location``, and a product code by the record's product) and recalls of ``MAKER``
initiated inside the date range: synthetic data in openFDA's shape, never real records.
"""
from __future__ import annotations

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlsplit

MAKER = "ACME Devices"
DESCRIPTION = "Description of Event or Problem"
PATHS = {"/device/event.json": "event", "/device/recall.json": "recall"}


class _Server(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request: Any, client_address: Any) -> None:
        pass


class OpenFDAStub:
    def __init__(self, records: dict[str, dict[str, list[dict[str, Any]]]] | None = None, *, fail_429: bool = False,
                 all_404: bool = False) -> None:
        self.records = records or {"event": {}, "recall": {}}
        self.fail_429 = fail_429
        self.all_404 = all_404
        self.requests: list[dict[str, Any]] = []
        self.lock = threading.Lock()
        self.httpd: _Server | None = None

    def _handle(self, h: BaseHTTPRequestHandler) -> None:
        parts = urlsplit(h.path)
        query = parse_qs(parts.query)
        search = query.get("search", [""])[0]
        dataset = PATHS.get(parts.path)
        with self.lock:
            self.requests.append({"dataset": dataset, "search": search, "api_key": query.get("api_key", [None])[0]})
        if dataset is None:
            return self._send(h, 404, {"error": {"code": "NOT_FOUND", "message": "No such endpoint"}})
        if self.fail_429:
            return self._send(h, 429, {"error": {"code": "TOO_MANY", "message": "slow down"}}, retry_after="0")
        m = re.search(r'"([A-Z]{3})"', search)
        recs = [] if self.all_404 or m is None else self.records.get(dataset, {}).get(m.group(1), [])
        limit, skip = int(query.get("limit", ["100"])[0]), int(query.get("skip", ["0"])[0])
        page = recs[skip:skip + limit]
        if not page:
            return self._send(h, 404, {"error": {"code": "NOT_FOUND", "message": "No matches found!"}})
        meta = {"disclaimer": "stub", "results": {"skip": skip, "limit": limit, "total": len(recs)}}
        return self._send(h, 200, {"meta": meta, "results": page})

    @staticmethod
    def _send(h: BaseHTTPRequestHandler, status: int, body: dict[str, Any], retry_after: str | None = None) -> None:
        data = json.dumps(body).encode("utf-8")
        h.send_response(status)
        h.send_header("Content-Type", "application/json")
        h.send_header("Content-Length", str(len(data)))
        if retry_after is not None:
            h.send_header("Retry-After", retry_after)
        h.end_headers()
        h.wfile.write(data)

    def start(self) -> "OpenFDAStub":
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                stub._handle(self)

            def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - the base class's signature
                pass

        self.httpd = _Server(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        return self

    def stop(self) -> None:
        if self.httpd is not None:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None

    def __enter__(self) -> "OpenFDAStub":
        return self.start()

    def __exit__(self, *exc: Any) -> None:
        self.stop()

    @property
    def base_url(self) -> str:
        assert self.httpd is not None
        return f"http://127.0.0.1:{self.httpd.server_address[1]}"


def event(record: dict[str, Any], code: str, maker: str = MAKER) -> dict[str, Any]:
    product = (record["entities"]["product"] or [None])[0]
    return {"mdr_report_key": record["record_ref"], "date_received": record["received_date"].replace("-", ""),
            "event_location": record["site"].upper(), "event_type": "Malfunction",
            "device": [{"manufacturer_d_name": maker, "model_number": product,
                        "lot_number": (record["entities"]["lot"] or [None])[0], "device_report_product_code": code,
                        "generic_name": "stub device"}],
            "product_problems": [], "mdr_text": [{"text_type_code": DESCRIPTION, "text": record["narrative"],
                                                  "mdr_text_key": "1"}]}


def fixture(codes: tuple[str, ...] = ("AAA", "BBB", "CCC"), seed: int = 3,
            maker: str = MAKER) -> dict[str, dict[str, list[dict[str, Any]]]]:
    """Events of a 52-week world split over ``codes`` by product, and three recalls of the first code."""
    from mycelic.collective.packs.generator import generate
    from mycelic.collective.packs.loader import load_pack

    world = generate(load_pack("device_quality"), seed, 6, 52)
    events: dict[str, list[dict[str, Any]]] = {code: [] for code in codes}
    for r in world.records:
        product = (r["entities"]["product"] or ["none"])[0]
        code = codes[sum(product.encode("utf-8")) % len(codes)]
        events[code].append(event(r, code, maker))
    recalls = {codes[0]: [{"product_code": codes[0], "recalling_firm": maker, "event_date_initiated": day,
                           "root_cause_description": "Device Design"} for day in ("20240610", "20240902", "20241105")]}
    return {"event": events, "recall": recalls}
