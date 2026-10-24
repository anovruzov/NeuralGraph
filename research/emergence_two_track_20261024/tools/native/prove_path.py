"""Proof: which NeuralGraph graph capabilities does the real EMERGENCE site path invoke?
Builds the largest site's persistent graph on the 200-user world (seed 0) exactly as the e2e harness does, answers
the 20 fixed questions, and counts calls into NeuralGraph's edge builders, edge persistence and graph traversal."""
import sqlite3, sys, tempfile
from collections import Counter
from pathlib import Path
from research.e2e_ingest import emergence_world as ew
from research.e2e_ingest import site_graph as sg
from research.routing_spike.world import backup_sqlite, restore_store
from NeuralGraph.research.retrieval import sqlite_storage, temporal, service, retriever, tesseract

calls = Counter()
def wrap(cls, name, label):
    if not hasattr(cls, name): return
    orig = getattr(cls, name)
    if callable(orig):
        import inspect
        if inspect.iscoroutinefunction(orig):
            async def w(*a, **k):
                calls[label] += 1; r = await orig(*a, **k)
                if label == "storage.get_neighbors": calls["get_neighbors.nonempty"] += bool(r); calls["get_neighbors.pairs"] += len(r)
                return r
        else:
            def w(*a, **k): calls[label] += 1; return orig(*a, **k)
        setattr(cls, name, w)
S = sqlite_storage.SQLiteNeuralGraphStorage
for n in ("save_edge", "save_edges_batch", "get_neighbors", "get_edges_from", "get_edges_to", "save_nodes_batch"):
    wrap(S, n, f"storage.{n}")
for n in ("create_temporal_edge", "build_temporal_chain", "create_causal_edge"):
    wrap(temporal.TemporalChainManager, n, f"TemporalChainManager.{n}")
for n in ("process_episodes", "process_extraction_result", "link_to_existing_memories", "retrieve"):
    wrap(service.NeuralGraphService, n, f"NeuralGraphService.{n}")
for n in ("retrieve", "_expand_stage", "retrieve_with_expansion", "retrieve_by_entities"):
    wrap(retriever.NeuralRetriever, n, f"NeuralRetriever.{n}")
wrap(tesseract.Tesseract, "retrieve", "Tesseract.retrieve")
wrap(tesseract.Tesseract, "_apply_temporal_momentum", "Tesseract._apply_temporal_momentum")

edges_flag = len(sys.argv) > 1 and sys.argv[1] == "edges"
tmp = Path(tempfile.mkdtemp()); w = ew.build_emergence_world(200, 0, tmp / "w")
for site in w.pipeline.sites.values(): site.close()
sid = max(w.site_ids, key=lambda s: w.per_site_records[s])
backup_sqlite(w.workdir / "edge" / f"site-{sid}.sqlite3", tmp / "pristine" / f"site-{sid}.sqlite3")
restore_store(sid, tmp / "pristine", tmp / "run" / "edge")
cfg = ew.site_configs(w, edge=tmp / "run" / "edge", hq_dir=tmp / "run" / "hq", graph_edges=edges_flag)[sid]
p = sg.E2ESiteProcess(cfg); rankings = []
try:
    for q in ew.fixed_questions(w, 20):
        from research.routing_spike.site_process import query_text
        rankings.append(list(p.retrieval.rank(query_text(w.pack, q), q["as_of"])))
finally:
    p.close()
conn = sqlite3.connect(cfg.graph_path)
n_nodes = conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]; n_edges = conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
print(f"MODE {'graph_edges' if edges_flag else 'default (as benchmarked)'}  site {sid}: nodes {n_nodes}, edges persisted {n_edges}")
for k in sorted(set(calls) | {"storage.save_edge","storage.save_edges_batch","TemporalChainManager.build_temporal_chain","TemporalChainManager.create_causal_edge","NeuralGraphService.process_extraction_result","NeuralRetriever.retrieve","NeuralRetriever._expand_stage","NeuralRetriever.retrieve_with_expansion"}):
    print(f"  {k:42s} {calls[k]}")
import json; json.dump(rankings, open(Path(__file__).with_name(f"rank_{'edges' if edges_flag else 'plain'}.json"), "w"))
