"""Offline probe: NeuralGraph's NATIVE edge builder (TemporalChainManager.build_temporal_chain, per entity) and NATIVE
multi-hop retrieval (NeuralRetriever.retrieve: entity_expansion / temporal_chain stages, max_hops=3) on the largest
seed-0 site graph, versus Tesseract. Reads go through a fail-closed as-of view: every node-returning call filters
records received after the question's as_of; any storage call not listed raises; writes during ranking are dropped
and counted (NeuralRetriever's LTP edge activation would otherwise make rankings depend on question order).
Usage: native_probe.py [native]   (native = build native temporal edges first). Dev seed 0 only."""
import asyncio, json, sqlite3, sys, tempfile, time
from collections import Counter, defaultdict
from pathlib import Path
from research.e2e_ingest import emergence_world as ew
from research.e2e_ingest import site_graph as sg
from research.routing_spike.world import backup_sqlite, restore_store
from research.routing_spike.site_process import query_text
from NeuralGraph.research.retrieval import retriever as R, temporal as T

calls = Counter()

class StrictAsOf:
    def __init__(self, base, received):
        self._b, self._rx, self.cutoff = base, received, None
    def _ok(self, nid):
        if self.cutoff is None: raise RuntimeError("no as_of")
        d = self._rx.get(nid); return d is not None and d <= self.cutoff
    async def get_nodes_by_session(self, s, layer=None):
        return [n for n in await self._b.get_nodes_by_session(s, layer) if self._ok(n.node_id)]
    async def get_node(self, nid):
        calls["get_node"] += 1; return await self._b.get_node(nid) if self._ok(nid) else None
    async def get_neighbors(self, nid, edge_types=None, direction="outgoing"):
        calls["get_neighbors"] += 1
        return [(n, e) for n, e in await self._b.get_neighbors(nid, edge_types=edge_types, direction=direction) if self._ok(n.node_id)]
    async def get_edges_from(self, nid, edge_types=None):
        calls["get_edges_from"] += 1
        if not self._ok(nid): return []
        return [e for e in await self._b.get_edges_from(nid, edge_types) if self._ok(e.target_id)]
    async def get_edges_to(self, nid, edge_types=None):
        calls["get_edges_to"] += 1
        if not self._ok(nid): return []
        return [e for e in await self._b.get_edges_to(nid, edge_types) if self._ok(e.source_id)]
    async def get_edges_batch(self, pairs, *a, **k):
        r = await self._b.get_edges_batch([p for p in pairs if self._ok(p[0]) and self._ok(p[1])], *a, **k); return r
    async def find_nodes_by_entities(self, ents, s, *a, **k):
        return [n for n in await self._b.find_nodes_by_entities(ents, s, *a, **k) if self._ok(n.node_id)]
    async def vector_search(self, emb, s, limit=50, layer_filter=None):
        r = await self._b.vector_search(emb, s, limit=10**6, layer_filter=layer_filter)
        return [(n, x) for n, x in r if self._ok(n.node_id)][:limit]
    async def save_edge(self, e): calls["dropped_write"] += 1
    async def save_edges_batch(self, es): calls["dropped_write"] += len(es); return 0
    def __getattr__(self, name): raise AttributeError(f"StrictAsOf: storage call {name} not allowed")

native = len(sys.argv) > 1 and sys.argv[1] == "native"
tmp = Path(tempfile.mkdtemp()); w = ew.build_emergence_world(200, 0, tmp / "w")
for site in w.pipeline.sites.values(): site.close()
sid = max(w.site_ids, key=lambda s: w.per_site_records[s])
backup_sqlite(w.workdir / "edge" / f"site-{sid}.sqlite3", tmp / "pristine" / f"site-{sid}.sqlite3")
restore_store(sid, tmp / "pristine", tmp / "run" / "edge")
cfg = ew.site_configs(w, edge=tmp / "run" / "edge", hq_dir=tmp / "run" / "hq", graph_edges=True)[sid]  # entities metadata on
p = sg.E2ESiteProcess(cfg); ret = p.retrieval; loop = ret._loop; storage = ret._storage
conn = sqlite3.connect(cfg.graph_path); conn.execute("DELETE FROM edges"); conn.commit(); conn.close()   # drop site chain edges
held = ret._by_ref; received = {k: n.metadata["received_date"] for k, n in held.items()}
built = 0; t0 = time.perf_counter()
if native:
    tm = T.TemporalChainManager(storage); by = defaultdict(list)
    for n in held.values():
        for k in n.metadata["entities"]: by[k].append(n)
    for k in sorted(by):
        built += len(loop.run_until_complete(tm.build_temporal_chain(sorted(by[k], key=lambda n: n.metadata["seq"]), ret.session)))
n_edges = sqlite3.connect(cfg.graph_path).execute("SELECT COUNT(*) FROM edges").fetchone()[0]
print(f"MODE {'native temporal edges' if native else 'no edges'}: nodes {len(held)}, edges built {built}, persisted {n_edges}, build {time.perf_counter()-t0:.1f}s")
view = StrictAsOf(storage, received)
nr = R.NeuralRetriever(view, temporal_manager=T.TemporalChainManager(view), config=R.RetrieverConfig(record_co_activations=False))
stage_hits = Counter(); out = []; tq = 0
for q in ew.fixed_questions(w, 20):
    text = query_text(w.pack, q); view.cutoff = q["as_of"]
    t1 = time.perf_counter()
    res = loop.run_until_complete(nr.retrieve(text, ret._embed(text, ret._dim), ret.session, limit=200))
    tq += time.perf_counter() - t1
    for s in getattr(res, "stages_executed", []) or []: stage_hits[s] += 1
    ids = [n.node_id for n, _ in res.nodes]
    assert all(received[i] <= q["as_of"] for i in ids)
    tess = ret.rank(text, q["as_of"])
    out.append({"neural": ids, "tesseract": tess})
p.close()
ov = [len(set(o["neural"][:50]) & set(o["tesseract"][:50])) for o in out]
print(f"  NeuralRetriever: {tq/len(out):.2f}s/question, results/question {[len(o['neural']) for o in out]}")
print(f"  stages executed (questions): {dict(stage_hits)}")
print(f"  storage calls via view: {dict(calls)}")
print(f"  top-50 overlap with Tesseract: {ov}")
json.dump(out, open(Path(__file__).with_name(f"neural_{'native' if native else 'none'}.json"), "w"))
