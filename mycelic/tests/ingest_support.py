"""Shared helpers for the ``test_ingest_*`` suites: export files, a holder store with its pipeline, a collecting publisher.

Everything is offline and deterministic: hash embeddings, local export files, the fake model provider when a router is
needed. Not a test module itself.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from mycelic.evidence import EvidenceStore
from mycelic.ingest.connectors import register_builtin
from mycelic.ingest.pipeline import IngestPipeline
from mycelic.ingest.registry import ConnectorRegistry
from mycelic.ingest.service import IngestService
from mycelic.util import new_id

TENANT = "ten_ingest"
OWNER = "usr_ana"


def write_jsonl(path: Path, records: Iterable[dict[str, Any]], *, header: dict[str, Any] | None = None) -> Path:
    lines = []
    if header is not None:
        lines.append(json.dumps({"type": "source", **header}))
    lines += [json.dumps(r) for r in records]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def append_jsonl(path: Path, records: Iterable[dict[str, Any]]) -> None:
    with path.open("a", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


def write_json(path: Path, records: list[dict[str, Any]], *, header: dict[str, Any] | None = None) -> Path:
    path.write_text(json.dumps({"source": header or {}, "records": records}), encoding="utf-8")
    return path


class CollectingPublisher:
    """Stands in for ``HolderService.publish_ingest_output``: records envelopes, de-duplicates on msg_id like a transport."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, dict[str, Any], str]] = []
        self._seen: set[str] = set()
        self.fail = False

    async def publish_ingest_output(self, kind: str, payload: dict[str, Any], *, msg_id: str) -> bool:
        if self.fail:
            raise ConnectionError("transport down")
        if msg_id in self._seen:
            return False
        self._seen.add(msg_id)
        self.sent.append((kind, payload, msg_id))
        return True

    def of(self, kind: str) -> list[dict[str, Any]]:
        return [p for k, p, _ in self.sent if k == kind]


def make_store(tmp_path: Path, name: str = "h1", *, owner_ids: Iterable[str] = (OWNER,), **kw: Any) -> EvidenceStore:
    return EvidenceStore(tmp_path / f"{name}.db", holder_id=f"hold_{name}", tenant_id=TENANT, owner_ids=owner_ids, **kw)


def make_pipeline(store: EvidenceStore, *, registry: ConnectorRegistry | None = None, **kw: Any) -> IngestPipeline:
    reg = registry or register_builtin(ConnectorRegistry())
    kw.setdefault("publisher", CollectingPublisher())
    kw.setdefault("batch_debounce_seconds", 0.0)
    return IngestPipeline(store, registry=reg, **kw)


async def connect_export(svc: IngestService, paths: list[Path], *, source_app: str, account_id: str = "acme", include: bool = True,
                         **config: Any) -> dict[str, Any]:
    con = await svc.add_connector("local_export", created_by=OWNER,
                                  config={"source_app": source_app, "account_id": account_id, "paths": [str(p) for p in paths], **config})
    await svc.discover_sources(con["connector_id"])
    if include:
        for s in svc.sources(con["connector_id"]):
            if s.selection != "included":
                await svc.set_source(s.source_id, actor=OWNER, selection="included")
    return con


def question(text: str, *, audience: dict[str, Any] | None = None, **kw: Any) -> dict[str, Any]:
    q = {"question_id": kw.pop("question_id", new_id("q")), "text": text, "candidate_domains": kw.pop("candidate_domains", []),
         "valid_from": None, "valid_to": None, "tenant_id": TENANT,
         "policy": {"visibility": kw.pop("visibility", "unit"), "disclosure": kw.pop("disclosure", None), "blind_verification": False}}
    if audience is not None:
        q["audience"] = audience
    q.update(kw)
    return q


def table_contains(store: EvidenceStore, needle: str) -> list[str]:
    """Every table (and column) of the holder file whose text contains ``needle`` (FTS shadow tables included)."""
    c = store.store._conn
    hits = []
    for (name,) in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
        try:
            cols = [r[1] for r in c.execute(f"PRAGMA table_info('{name}')").fetchall()]
        except Exception:
            continue
        for col in cols:
            try:
                n = c.execute(f'SELECT COUNT(*) FROM "{name}" WHERE CAST("{col}" AS TEXT) LIKE ?', (f"%{needle}%",)).fetchone()[0]
            except Exception:
                continue
            if n:
                hits.append(f"{name}.{col}")
    return hits
