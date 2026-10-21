#!/usr/bin/env python3
"""Fixtures for ``retrieval/external_retriever.py`` (``is_open_domain_query`` over LoCoMo is in ``tesseract.json``):
the tracer, ``ExternalResult`` serialisation, the persistent cache's files, the registry, and the Wikipedia
retriever against a local HTTP server that serves canned API responses and records every request it gets.
Writes ``external_retriever.json``."""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qsl, urlsplit

import _reference as R

import NeuralGraph.research.retrieval.external_retriever as X

R.freeze_now([X])

SEARCH = {"query": {"search": [
    {"pageid": 101, "title": "Photosynthesis", "snippet": "process <b>plants</b>", "wordcount": 9000},
    {"pageid": 202, "title": "Chlorophyll", "snippet": "green pigment"},
    {"pageid": 303, "title": "Empty page", "snippet": "", "wordcount": 3},
    {"pageid": 404, "title": "Light", "wordcount": 12},
]}}
PAGES = {"query": {"pages": {
    "101": {"pageid": 101, "extract": "Photosynthesis is a process used by plants…", "fullurl": "https://en.wikipedia.org/wiki/Photosynthesis"},
    "202": {"pageid": 202, "extract": "Chlorophyll is a green pigment.", "fullurl": "https://en.wikipedia.org/wiki/Chlorophyll"},
    "303": {"pageid": 303, "extract": ""},
    "404": {"pageid": 404, "extract": "Light is radiation."},
}}}


class Handler(BaseHTTPRequestHandler):
    requests: list = []

    def do_GET(self):  # noqa: N802 - http.server API
        parts = urlsplit(self.path)
        params = parse_qsl(parts.query, keep_blank_values=True)
        Handler.requests.append({"path": parts.path, "params": params, "ua": self.headers.get("User-Agent")})
        q = dict(params)
        if q.get("srsearch") == "fail":
            self.send_response(500)
            self.end_headers()
            return
        if q.get("list") == "search":
            body = SEARCH if q.get("srsearch") != "nothing" else {"query": {"search": []}}
        elif q.get("meta") == "siteinfo":
            body = {"query": {"general": {}}}
        else:
            body = PAGES
        raw = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *args):
        pass


def tracer():
    t = X.OpenDomainTracer(max_traces=3)
    out = {"stats_empty": t.get_stats()}
    specs = [
        dict(query_text="What is gravity?", is_open_domain=True, open_domain_confidence=0.8),
        dict(query_text="When did she go?", session_hits_at_threshold=4, session_candidates_count=10),
        dict(query_text="Capital of France", is_open_domain=True, external_retriever_called=True,
             external_in_top_k=2, session_hits_at_threshold=1, external_retrieval_latency_ms=12.5),
        dict(query_text="Who is he?", external_retriever_called=True),
        dict(query_text="x" * 80, is_open_domain=True, strategy_used="open_domain"),
    ]
    for s in specs:
        t.record_trace(X.OpenDomainTrace(**s))
    out["stats"] = t.get_stats()
    out["recent"] = t.get_recent_traces(2)
    out["recent_all"] = t.get_recent_traces()
    return out


def results():
    r = X.ExternalResult(content="Paris is the capital of France.", source="wikipedia", title="Paris",
                         relevance_score=0.9, metadata={"page_id": "1"})
    d = r.to_dict()
    back = X.ExternalResult.from_dict(d)
    minimal = X.ExternalResult.from_dict({"content": "ünïcode", "source": "web"})
    return {"to_dict": d, "roundtrip": back.to_dict(), "minimal": minimal.to_dict(),
            "keys": [X.ExternalResult(content=c, source="s").cache_key for c in ["", "a", "ünïcode ✓"]]}


def persistent():
    d = tempfile.mkdtemp(prefix="ng-ext-cache-")
    cache = X.PersistentExternalCache(d)
    out = {"get_missing": cache.get("What is rain?")}
    rs = [X.ExternalResult(content="Rain is liquid water.", source="wikipedia", title="Rain", relevance_score=1.0),
          X.ExternalResult(content="Clouds…", source="wikipedia", metadata={"n": 2})]
    cache.put("What is Rain?", rs)
    out["files_after_put"] = {name: open(os.path.join(d, name), encoding="utf-8").read() for name in sorted(os.listdir(d))}
    out["get_hit"] = [r.to_dict() for r in cache.get("WHAT IS RAIN?")]
    reopened = X.PersistentExternalCache(d)
    out["reopened_hit"] = [r.to_dict() for r in reopened.get("what is rain?")]
    out["cleared"] = reopened.clear()
    out["files_after_clear"] = {name: open(os.path.join(d, name), encoding="utf-8").read() for name in sorted(os.listdir(d))}
    out["get_after_clear"] = reopened.get("what is rain?")
    return out


async def wikipedia(base):
    out = {}
    w = X.create_wikipedia_retriever(X.WikipediaConfig(api_url=base + "/w/api.php"))
    out["source_name"] = w.source_name
    out["supports_embedding_search"] = w.supports_embedding_search()
    out["available"] = await w.is_available()
    Handler.requests.clear()
    first = await w.retrieve("What is Photosynthesis?", limit=3)
    out["first"] = [r.to_dict() for r in first]
    out["first_requests"] = list(Handler.requests)
    Handler.requests.clear()
    again = await w.retrieve("  what is photosynthesis?  ", limit=1)
    out["cached"] = [r.to_dict() for r in again]
    out["cached_requests"] = list(Handler.requests)
    out["nothing"] = [r.to_dict() for r in await w.retrieve("nothing")]
    out["fail"] = [r.to_dict() for r in await w.retrieve("fail")]
    out["last_error_set"] = w._last_error is not None
    registry = X.create_default_registry()
    registry._retrievers["wikipedia"]._config.api_url = base + "/w/api.php"
    out["registry_enabled"] = [r.source_name for r in registry.get_enabled_retrievers()]
    all_results = await registry.retrieve_all("What is Photosynthesis?", limit_per_source=2)
    out["registry_all"] = {k: [r.to_dict() for r in v] for k, v in all_results.items()}
    registry.set_enabled("wikipedia", False)
    out["registry_disabled"] = await registry.retrieve_all("x")
    out["unregister"] = [registry.unregister("wikipedia"), registry.unregister("wikipedia")]
    down = X.WikipediaRetriever(X.WikipediaConfig(api_url="http://127.0.0.1:9/w/api.php"))
    out["down_available"] = [await down.is_available(), await down.is_available()]
    out["down_retrieve"] = await down.retrieve("q")
    return out


def main():
    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    wiki = asyncio.run(wikipedia(base))
    server.shutdown()
    queries = ["What is gravity?", "What are the stars made of?", "how much is it", "Who was he?",
               "Where is Paris located", "my largest fear", "Tell me about the meeting", "What causes   rain",
               "", "  EXPLAIN quantum  ", "what year did we meet", "The cell", "Describe it"]
    R.dump("external_retriever.json", {
        "is_open_domain": {q: list(X.is_open_domain_query(q)) for q in queries},
        "tracer": tracer(),
        "results": results(),
        "persistent": persistent(),
        "wikipedia": wiki,
        "config": vars(X.WikipediaConfig()),
    })


main()
