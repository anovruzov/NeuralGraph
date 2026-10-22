#!/usr/bin/env python3
"""Expected tables of the retrieval-only LoCoMo evaluators on the real (nomic) embeddings: ``two_agent_eval.py``
(``MODE=combined`` and ``MODE=split``) and ``retrieval_eval.py``. Writes ``locomo_tables.json``; checked by the
ignored test ``tests/locomo_reproduction.rs`` (the embedding cache is gitignored).

Each script runs twice on all ten conversations: as written (``PYTHONHASHSEED=0``), and with the reference's sets
insertion-ordered (storage indexes, Tesseract novelty tokens, dialogue linker, mega search), the orders the Rust
port uses. Two numbers depend on set order in Python: the "KG: graph channel only" row of the combined stack (the
PageRank mass of the graph channel is summed in set order; Python printed 17.7 single_hop r@10 with hash seed 0 and
18.1 with seed 1) and the ``tesseract+graph`` variant of ``retrieval_eval`` (the storage's neighbour sets hold random
uuid edge ids, so equally charged neighbours come out in a different order on every run). Every other line is
identical between the two runs. The progress lines (``conv i: ...``, with timings) are dropped.

Needs the embedding cache (``EMB_CACHE_DIR``, default ``research/results/emb_cache``) and the KG cache
(``research/results/kg_cache``). Run from the repository root (about 15 minutes):
``PYTHONHASHSEED=0 python crates/neuralgraph-research/tests/golden/gen_locomo_tables.py``.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def ordered_run(script: str) -> None:
    """Apply the insertion-ordered sets to the reference and run ``script`` as ``__main__``."""
    import re
    import runpy
    from collections import defaultdict

    import _reference as R  # noqa: F401  (puts the reference on sys.path)
    from _ordered_sets import load_ordered
    import NeuralGraph.research.retrieval.dialogue_linker as DL
    import NeuralGraph.research.retrieval.mega_search as MS
    import NeuralGraph.research.retrieval.storage as storage_mod
    import NeuralGraph.research.retrieval.tesseract as TS

    class OrderedSet:
        def __init__(self, items=()):
            self._d = dict.fromkeys(items)

        def add(self, x):
            self._d[x] = None

        def discard(self, x):
            self._d.pop(x, None)

        def remove(self, x):
            del self._d[x]

        def clear(self):
            self._d.clear()

        def update(self, items):
            for x in items:
                self._d[x] = None

        def __contains__(self, x):
            return x in self._d

        def __iter__(self):
            return iter(list(self._d))

        def __len__(self):
            return len(self._d)

        def __and__(self, other):
            return OrderedSet(x for x in self._d if x in other)

    class OrderedStorage(storage_mod.InMemoryNeuralGraphStorage):
        def __init__(self):
            super().__init__()
            self._nodes_by_session = defaultdict(OrderedSet)
            self._edges_from = defaultdict(OrderedSet)
            self._edges_to = defaultdict(OrderedSet)
            self._edges_by_session = defaultdict(OrderedSet)
            self._nodes_by_entity = defaultdict(OrderedSet)
            self._nodes_by_layer = defaultdict(lambda: defaultdict(OrderedSet))

    novelty = re.compile(r'\b[a-z0-9]{3,}\b')
    TS._tokenize_for_novelty = lambda text: OrderedSet(
        w for w in novelty.findall(text.lower()) if w not in TS._NOVELTY_STOPWORDS)
    storage_mod.InMemoryNeuralGraphStorage = OrderedStorage
    DL.DialogueLinker = load_ordered("NeuralGraph.research.retrieval.dialogue_linker").DialogueLinker
    MS.MegaIndex = load_ordered("NeuralGraph.research.retrieval.mega_search").MegaIndex
    import retrieval_eval
    import runner
    for m in (runner, retrieval_eval):
        m.InMemoryNeuralGraphStorage = OrderedStorage
        m.DialogueLinker = DL.DialogueLinker
    sys.argv = [script]
    runpy.run_path(script, run_name="__main__")


def run(script: str, env: dict, ordered: bool) -> str:
    import _reference as R
    path = str(R.REF / "research" / "benchmarks" / script)
    # the scripts resolve research/datasets and research/results/kg_cache against the reference tree
    for sub, target in (("research/datasets", R.DATASETS), ("research/results/kg_cache", R.ROOT / "research/results/kg_cache")):
        link = R.REF / sub
        link.parent.mkdir(parents=True, exist_ok=True)
        if not link.exists():
            link.symlink_to(target)
    full_env = {**os.environ, "PYTHONHASHSEED": "0", **env}
    full_env.setdefault("EMB_CACHE_DIR", str(R.ROOT / "research" / "results" / "emb_cache"))
    full_env["NG_PYTHON_REFERENCE"] = str(R.REF)
    cmd = [sys.executable, __file__, "--ordered", path] if ordered else [sys.executable, path]
    out = subprocess.run(cmd, env=full_env, cwd=str(HERE), check=True, capture_output=True, text=True).stdout
    return "".join(line for line in out.splitlines(keepends=True) if not line.startswith("conv "))


def main() -> None:
    import json
    import _reference as R
    tables = {}
    for name, script, env in (("combined", "two_agent_eval.py", {"MODE": "combined"}),
                              ("split", "two_agent_eval.py", {"MODE": "split"}),
                              ("retrieval_eval", "retrieval_eval.py", {})):
        tables[name] = {"python": run(script, env, False), "ordered": run(script, env, True)}
        print(f"{name} done", flush=True)
    path = HERE / "locomo_tables.json"
    path.write_text(json.dumps(tables, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {path.relative_to(R.ROOT)}")


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "--ordered":
        ordered_run(sys.argv[2])
    else:
        main()
