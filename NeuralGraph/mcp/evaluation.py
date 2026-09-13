"""Offline evaluation of the memory server's recall quality.

A labelled, deterministic coding-agent session: 60 memories an agent would
plausibly store (facts, preferences, decisions, conventions, incidents,
commitments, with keyed updates and distractors) and 48 queries, each with
the id of the one memory that answers it.  Queries are grouped so each
preprocessing component is measured on the queries it exists for:

- ``exact``       the query reuses the memory's own words
- ``paraphrase``  the query uses synonyms (deploy/ship, IDE/editor, db/database)
- ``misspelled``  key terms are misspelled
- ``name``        the query is about a person, place or product by name
- ``keyed``       the answer is the *current* value of a fact that changed
- ``negative``    nothing in memory answers it (reported as a hit rate, not scored)

Five configurations run under matched conditions (same memories, same order,
same deterministic clock, hashed embedder): baseline (all three components
off), each component alone, and all on.  Metrics: precision@1, recall@5, MRR.

No model, no network, no wall clock in the pinned output.  Regenerate with::

    python3.11 -m NeuralGraph.mcp.evaluation --output NeuralGraph/mcp/artifacts/memory_eval.json
    python3.11 -m NeuralGraph.mcp.evaluation --format markdown
"""

from __future__ import annotations

import argparse
import asyncio
import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .memory import Features, HashedEmbedder, MemoryEngine

EVAL_VERSION = "memory-eval-v1"

# (label, text, kind, key)
MEMORIES: list[tuple[str, str, str, str | None]] = [
    ("m01", "Ali prefers neovim as the editor for Python work.", "preference", "user.editor"),
    ("m02", "Ali prefers dark mode in every editor and terminal.", "preference", "user.theme"),
    ("m03", "The staging database is Postgres 16 hosted on Railway.", "fact", "env.staging_db"),
    ("m04", "Production runs Postgres 15 on RDS in eu-central-1.", "fact", "env.prod_db"),
    ("m05", "We ship to production from GitHub Actions on every push to main.", "convention", None),
    ("m06", "Secrets for deploys live in 1Password under the Infra vault.", "fact", None),
    ("m07", "Nurman Mahammadov owns the retrieval track: answering, reranking, prompts.", "fact", None),
    ("m08", "Anar owns coordination, lineage semantics and the failure experiments.", "fact", None),
    ("m09", "Nurman said the listwise reranker is faster but less accurate than pointwise.", "fact", None),
    ("m10", "Standup is at 09:30 Berlin time on weekdays.", "fact", "team.standup"),
    ("m11", "Standup moved to 10:00 Berlin time starting next sprint.", "fact", "team.standup"),
    ("m12", "The default branch is main; feature branches are prefixed with the author's name.", "convention", "repo.default_branch"),
    ("m13", "Decision: we chose SQLite over Postgres for the local memory store because it needs no server.", "decision", None),
    ("m14", "Decision: reciprocal rank fusion over learned weights, because it needs no training data.", "decision", None),
    ("m15", "Incident: the 2026-08-25 benchmark run reported 0.88 for every strategy because max_nodes was lifted.", "incident", None),
    ("m16", "Incident: pytest collected nothing because a stale root __init__.py shadowed the package.", "incident", None),
    ("m17", "Commitment: send Anar the figure regeneration script before Friday.", "commitment", None),
    ("m18", "Commitment: review Nurman's no-reranker latency table this week.", "commitment", None),
    ("m19", "The single-hop accuracy ceiling on LoCoMo is 96.9% because 47 questions are unanswerable.", "fact", None),
    ("m20", "The paper's headline: lineage-aware repair survives 0.778, oracle min-cut 0.889.", "fact", None),
    ("m21", "Embeddings come from nomic-embed-text on LM Studio at port 1234.", "fact", "env.embed_model"),
    ("m22", "The answer model is gemma-4-e4b; reasoning effort is set to none.", "fact", "env.answer_model"),
    ("m23", "Kubernetes is not used; everything deploys as a single container on Railway.", "fact", None),
    ("m24", "Ali's laptop is a MacBook Pro with Python 3.11 from Homebrew.", "fact", None),
    ("m25", "Ali dislikes long PR descriptions; keep them under ten lines.", "preference", None),
    ("m26", "Code style: ruff with line length 100, no black.", "convention", None),
    ("m27", "Tests run with python3.11 -m pytest from the repo root; there is no venv step.", "convention", None),
    ("m28", "The NATS JetStream transport is design intent only; nothing is implemented.", "fact", None),
    ("m29", "Never synthesize a failure domain; read it from recorded root metadata or report absence.", "convention", None),
    ("m30", "Lineage direction: derivation parents are the targets of outgoing HIERARCHY edges.", "convention", None),
    ("m31", "The Forest Ops game is at docs/sim and is illustrative, not the benchmark.", "fact", None),
    ("m32", "Nurman's LM Studio runs on a Mac mini named studio-1 in the office.", "fact", None),
    ("m33", "Anar is in Baku; Nurman is in Berlin; Ali travels between both.", "fact", None),
    ("m34", "The deadline for the workshop submission is 2026-10-03.", "fact", "paper.deadline"),
    ("m35", "The token for the staging API is rotated every 30 days by the infra bot.", "fact", None),
    ("m36", "Rate limits: Gemini free tier allows 15 requests per minute on the daily quota.", "fact", None),
    ("m37", "Ali wants every benchmark number traceable to a pinned artifact field.", "preference", None),
    ("m38", "We use uv for virtualenvs in the research track and plain pip elsewhere.", "convention", None),
    ("m39", "The office wifi is Heron-5G; the guest network is Heron-Guest.", "fact", None),
    ("m40", "Lunch on Fridays is at the taco place on Torstrasse.", "note", None),
    ("m41", "Incident: the OpenAI key was committed in demo/runner.py on main and must be revoked.", "incident", None),
    ("m42", "The reranker can be disabled with RERANK=none; it costs 0.44 s per question without it.", "fact", None),
    ("m43", "Decision: memories are deduplicated by lexical fingerprint, never by cosine similarity.", "decision", None),
    ("m44", "The MCP server config for Claude Code lives in .mcp.json at the repo root.", "fact", None),
    ("m45", "Codex reads MCP servers from ~/.codex/config.toml under mcp_servers.", "fact", None),
    ("m46", "Ali's cat is called Miso and interrupts calls around 18:00.", "note", None),
    ("m47", "The docker daemon is not available in the remote sandbox; use in-process fakes.", "fact", None),
    ("m48", "Backups of the memory database go to ~/.neuralgraph/backups nightly.", "fact", None),
    ("m49", "Anar prefers merge commits over rebases on shared branches.", "preference", None),
    ("m50", "Nurman prefers rebases and small commits on his own branches.", "preference", None),
    ("m51", "The capstone run covered conversations 1 to 5, 744 questions, 72.2% lenient.", "fact", None),
    ("m52", "Retrieval recall@10 improves with per-agent routing; end-to-end answers did not.", "fact", None),
    ("m53", "The judge model matters: the same answers score 13.5% to 64.9% by grader alone.", "fact", None),
    ("m54", "Use Chakra Petch and IBM Plex Mono for anything Mycelic-branded.", "convention", None),
    ("m55", "The pre-mortem oracle in the game simulates each root failure before it happens.", "fact", None),
    ("m56", "Commitment: open the PR from the cognitive-forcing-function branch to main once Ali approves.", "commitment", None),
    ("m57", "Berlin office moves to Kreuzberg in November.", "fact", "office.location"),
    ("m58", "The thesaurus file is WordNet-derived with 117k entries and lacks most software terms.", "fact", None),
    ("m59", "Spelling correction never touches capitalised words or words the corpus already contains.", "convention", None),
    ("m60", "Ali's editor moved to Zed in September; neovim stays for remote sessions.", "preference", "user.editor"),
]

# (query, category, gold label or None)
QUERIES: list[tuple[str, str, str | None]] = [
    # exact
    ("which database does staging use", "exact", "m03"),
    ("where do deploy secrets live", "exact", "m06"),
    ("what is the default branch", "exact", "m12"),
    ("how do tests run", "exact", "m27"),
    ("what is the single-hop accuracy ceiling on LoCoMo", "exact", "m19"),
    ("what embedding model do we use", "exact", "m21"),
    ("what happened when max_nodes was lifted", "exact", "m15"),
    ("how often is the staging API token rotated", "exact", "m35"),
    ("what is the office wifi", "exact", "m39"),
    ("what does the reranker cost per question", "exact", "m42"),
    # paraphrase (synonyms)
    ("how do we release to prod", "paraphrase", "m05"),
    ("which IDE does Ali like", "paraphrase", "m60"),
    ("where is the db for the staging environment", "paraphrase", "m03"),
    ("what was the defect with the benchmark run", "paraphrase", "m15"),
    ("when is the team meeting", "paraphrase", "m11"),
    ("where are the credentials for rollout kept", "paraphrase", "m06"),
    ("which llm answers questions", "paraphrase", "m22"),
    ("what is the ci workflow for shipping", "paraphrase", "m05"),
    ("what settings does the code style use", "paraphrase", "m26"),
    ("is there a container orchestration setup", "paraphrase", "m23"),
    # misspelled
    ("posgres on stagng", "misspelled", "m03"),
    ("nevoim or zed for Ali", "misspelled", "m60"),
    ("listwise rerankr accuracy", "misspelled", "m09"),
    ("kubernets in use?", "misspelled", "m23"),
    ("wokshop submision deadline", "misspelled", "m34"),
    ("embeding model port", "misspelled", "m21"),
    ("backups of the memroy databse", "misspelled", "m48"),
    ("deafult brnach name", "misspelled", "m12"),
    # names
    ("what did Nurman say about the reranker", "name", "m09"),
    ("who owns coordination and lineage", "name", "m08"),
    ("where is Anar based", "name", "m33"),
    ("what machine does Nurman run LM Studio on", "name", "m32"),
    ("Anar's preference on merging branches", "name", "m49"),
    ("what is Miso", "name", "m46"),
    ("what is happening to the Berlin office", "name", "m57"),
    ("what runs on Railway", "name", "m03"),
    # keyed (current value after updates)
    ("what time is standup", "keyed", "m11"),
    ("what editor does Ali use now", "keyed", "m60"),
    ("current standup time in Berlin", "keyed", "m11"),
    ("Ali's editor", "keyed", "m60"),
    # negative: nothing stored answers these
    ("what is the capital of Australia", "negative", None),
    ("how many moons does Jupiter have", "negative", None),
    ("what is the recipe for sourdough", "negative", None),
    ("who won the 2018 world cup", "negative", None),
    ("what is the speed of light", "negative", None),
    ("translate hello to Japanese", "negative", None),
    ("what is the weather tomorrow", "negative", None),
    ("how tall is Mount Everest", "negative", None),
]

CONFIGURATIONS: dict[str, Features] = {
    "baseline": Features(spelling=False, thesaurus=False, names=False),
    "spelling": Features(spelling=True, thesaurus=False, names=False),
    "thesaurus": Features(spelling=False, thesaurus=True, names=False),
    "names": Features(spelling=False, thesaurus=False, names=True),
    "all": Features(spelling=True, thesaurus=True, names=True),
}


class _Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 9, 13, 8, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        self.now += timedelta(seconds=1)
        return self.now


async def _run_configuration(name: str, features: Features, k: int = 5) -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as tmp:
        engine = MemoryEngine(Path(tmp) / "eval.db", session_key="eval", clock=_Clock(), embedder=HashedEmbedder(), features=features)
        try:
            ids: dict[str, str] = {}
            for label, text, kind, key in MEMORIES:
                item = await engine.remember(text, kind=kind, key=key)
                ids[label] = item["id"]
            per_query = []
            for query, category, gold in QUERIES:
                hits = await engine.recall(query, limit=k, strengthen=False)
                ranked = [h["id"] for h in hits]
                rank = ranked.index(ids[gold]) + 1 if gold and ids[gold] in ranked else None
                per_query.append({
                    "query": query, "category": category, "gold": gold, "rank_of_gold": rank, "hits": len(hits),
                    "top_confidence": hits[0]["confidence"] if hits else 0.0,
                })
        finally:
            engine.close()
    scored = [q for q in per_query if q["gold"]]
    negatives = [q for q in per_query if not q["gold"]]

    def metrics(rows: list[dict[str, Any]]) -> dict[str, float]:
        if not rows:
            return {"p_at_1": 0.0, "r_at_5": 0.0, "mrr": 0.0, "n": 0}
        return {
            "p_at_1": round(sum(1 for r in rows if r["rank_of_gold"] == 1) / len(rows), 4),
            "r_at_5": round(sum(1 for r in rows if r["rank_of_gold"] and r["rank_of_gold"] <= k) / len(rows), 4),
            "mrr": round(sum(1 / r["rank_of_gold"] for r in rows if r["rank_of_gold"]) / len(rows), 4),
            "n": len(rows),
        }

    by_category = {c: metrics([r for r in scored if r["category"] == c]) for c in ("exact", "paraphrase", "misspelled", "name", "keyed")}
    # Confidence gate: evidence coverage of the top hit, comparable across
    # queries.  Reported at two thresholds so the trade-off is visible.
    gates = {}
    for threshold in (0.25, 0.34):
        gates[str(threshold)] = {
            "positives_answered": sum(1 for r in scored if r["top_confidence"] >= threshold),
            "negatives_answered": sum(1 for r in negatives if r["top_confidence"] >= threshold),
        }
    return {
        "features": features.as_dict(),
        "overall": metrics(scored),
        "by_category": by_category,
        "negative_hit_rate": round(sum(1 for r in negatives if r["hits"]) / len(negatives), 4) if negatives else 0.0,
        "confidence_gate": gates,
        "per_query": per_query,
    }


async def run_evaluation() -> dict[str, Any]:
    configurations = {name: await _run_configuration(name, features) for name, features in CONFIGURATIONS.items()}
    return {
        "schema_version": "1.0",
        "eval_version": EVAL_VERSION,
        "dataset": {
            "memories": len(MEMORIES),
            "queries": len(QUERIES),
            "by_category": {c: sum(1 for _, cat, _ in QUERIES if cat == c) for c in ("exact", "paraphrase", "misspelled", "name", "keyed", "negative")},
            "embedder": HashedEmbedder.name,
        },
        "configurations": configurations,
    }


def render_markdown(result: dict[str, Any]) -> str:
    cats = ("exact", "paraphrase", "misspelled", "name", "keyed")
    lines = [
        f"# Memory recall evaluation ({result['eval_version']})",
        "",
        f"{result['dataset']['memories']} memories, {result['dataset']['queries']} queries, embedder `{result['dataset']['embedder']}`. Precision@1 per category; overall P@1 / R@5 / MRR; negative hit rate = fraction of unanswerable queries that still returned something.",
        "",
        "| configuration | " + " | ".join(cats) + " | overall P@1 | R@5 | MRR | negative hit rate |",
        "|---|" + "---|" * (len(cats) + 4),
    ]
    for name, cfg in result["configurations"].items():
        row = [f"`{name}`"] + [f"{cfg['by_category'][c]['p_at_1']:.2f}" for c in cats]
        row += [f"{cfg['overall']['p_at_1']:.2f}", f"{cfg['overall']['r_at_5']:.2f}", f"{cfg['overall']['mrr']:.2f}", f"{cfg['negative_hit_rate']:.2f}"]
        lines.append("| " + " | ".join(row) + " |")
    lines.append("")
    all_cfg = result["configurations"]["all"]
    n_pos = all_cfg["overall"]["n"]; n_neg = result["dataset"]["by_category"]["negative"]
    for thr, g in all_cfg["confidence_gate"].items():
        lines.append(f"Confidence gate {thr} (`all`): answers {g['positives_answered']}/{n_pos} answerable queries and {g['negatives_answered']}/{n_neg} unanswerable ones.")
    lines.append("")
    misses = [q for q in all_cfg["per_query"] if q["gold"] and q["rank_of_gold"] != 1]
    lines.append(f"Misses at rank 1 under `all` ({len(misses)}):")
    lines.append("")
    for q in misses:
        lines.append(f"- [{q['category']}] {q['query']!r} → gold {q['gold']} at rank {q['rank_of_gold']}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    args = parser.parse_args(argv)
    result = asyncio.run(run_evaluation())
    payload = render_markdown(result) if args.format == "markdown" else json.dumps(result, sort_keys=True, indent=2) + "\n"
    if args.output:
        args.output.write_text(payload, encoding="utf-8")
    else:
        print(payload, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
