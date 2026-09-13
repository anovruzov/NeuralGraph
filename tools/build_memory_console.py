"""Rebuild docs/sim/neuralgraph_memory_console.html from the template and live data.

The page is a JavaScript port of the memory engine's ranking over the
evaluation session, with the pinned evaluation table embedded.  Regenerate
after the evaluation artifact or the lexicon changes::

    python3.11 -m tools.build_memory_console
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    from NeuralGraph.mcp import lexicon as L
    from NeuralGraph.mcp.evaluation import MEMORIES, QUERIES
    from NeuralGraph.mcp.memory import content_tokens
    from NeuralGraph.mega_search import _STOP
    from NeuralGraph.synonym_hash import _load_thesaurus

    thesaurus = _load_thesaurus()
    corpus: set[str] = set()
    for _, text, _, _ in MEMORIES:
        corpus.update(content_tokens(text))
    related: dict[str, set[str]] = {}
    for w in sorted(corpus):
        rel = {s for s in thesaurus.get(w, set()) if re.match(r"^[a-z0-9]+$", s)} | set(L._software_bidirectional().get(w, set()))
        for x in rel:
            related.setdefault(x, set()).add(w)
    data = {
        "memories": [{"label": l, "text": t, "kind": k, "key": key} for l, t, k, key in MEMORIES],
        "queries": [{"q": q, "cat": c, "gold": g} for q, c, g in QUERIES],
        "related": {k: sorted(v)[:6] for k, v in related.items()},
        "common_starters": sorted(L.COMMON_STARTERS),
        "stop": sorted(_STOP),
        "eval": json.loads((ROOT / "NeuralGraph/mcp/artifacts/memory_eval.json").read_text()),
    }
    for cfg in data["eval"]["configurations"].values():
        cfg.pop("per_query", None)
    template = (ROOT / "docs/sim/memory_console_template.html").read_text(encoding="utf-8")
    html = template.replace("__DATA__", json.dumps(data, separators=(",", ":")).replace("</", "<\\/")).replace("__DICT__", " ".join(sorted(L.dictionary())))
    out = ROOT / "docs/sim/neuralgraph_memory_console.html"
    out.write_text(html, encoding="utf-8")
    print(f"wrote {out.relative_to(ROOT)} ({len(html)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
