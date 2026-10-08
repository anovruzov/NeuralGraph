"""Shard routing (docs/mycelic/INGESTION.md §7; product decision 1).

One store per holder: shard ``s0`` is the holder's ``evidence.db`` and, for now, the only shard. Every write still asks
:meth:`ShardRouter.route_write`, and every write goes to :meth:`ShardRouter.writer`, so splitting a domain subtree into its
own file later (when measured thresholds trip, §7.3) changes this module and the shard map, never the pipeline.

Routing is sticky: once a record has a shard in ``record_locator`` it stays there (a changed primary domain never moves
data by itself; a reshard does, §7.7). New records go to the most specific active shard whose ``domain_ids`` contain the
record's primary domain or one of its ancestors; otherwise to the catch-all shard (``domain_ids = []``).
"""
from __future__ import annotations

import sqlite3
from typing import Any, Mapping

from ..util import jl
from .domains import Taxonomy

DEFAULT_SHARD = "s0"


class ShardUnavailable(RuntimeError):
    pass


class ShardRouter:
    def __init__(self, stores: Mapping[str, Any], *, taxonomy: Taxonomy) -> None:
        if DEFAULT_SHARD not in stores:
            raise ValueError("the control shard s0 must be open")
        self.stores = dict(stores)
        self.taxonomy = taxonomy

    @property
    def control(self) -> Any:
        """The control shard: connectors, queue, locator, tombstones live here."""
        return self.stores[DEFAULT_SHARD]

    def shards_sync(self, c: sqlite3.Connection) -> list[dict[str, Any]]:
        return [dict(r, domain_ids=jl(r["domain_ids"], [])) for r in
                c.execute("SELECT * FROM shard_map WHERE status IN ('active', 'readonly', 'draining') ORDER BY ordinal").fetchall()]

    def route_write_sync(self, c: sqlite3.Connection, record_key: str, *, primary_domain_id: str | None, created_at: str | None = None) -> str:
        row = c.execute("SELECT shard_id FROM record_locator WHERE record_key=?", (record_key,)).fetchone()
        if row is not None:
            return row["shard_id"]                       # sticky
        best, best_depth = DEFAULT_SHARD, -1
        for s in self.shards_sync(c):
            if s["status"] != "active":
                continue
            if not s["domain_ids"]:
                if best_depth < 0:
                    best = s["shard_id"]
                continue
            if primary_domain_id is None:
                continue
            chain = [*self.taxonomy.ancestors(primary_domain_id), self.taxonomy.resolve(primary_domain_id)]
            for d in s["domain_ids"]:
                if d in chain and chain.index(d) > best_depth:
                    best, best_depth = s["shard_id"], chain.index(d)
        return best

    def writer(self, shard_id: str) -> Any:
        store = self.stores.get(shard_id)
        if store is None:
            raise ShardUnavailable(f"shard {shard_id} is not open in this process")
        return store

    def route_read_sync(self, c: sqlite3.Connection, domain_ids: list[str] | None = None) -> list[str]:
        """Shards to query (bounded fan-out). With one shard this is always ``[s0]``."""
        out = []
        for s in self.shards_sync(c):
            if domain_ids and s["domain_ids"]:
                chains = {a for d in domain_ids for a in [*self.taxonomy.ancestors(d), self.taxonomy.resolve(d)]}
                if not chains & set(s["domain_ids"]) and not any(self.taxonomy.resolve(d) in self.taxonomy.ancestors(x)
                                                                 for d in domain_ids for x in s["domain_ids"]):
                    continue
            out.append(s["shard_id"])
        return [s for s in out if s in self.stores] or [DEFAULT_SHARD]
