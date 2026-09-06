"""Top-level MANIFEST.json: what was run, from what code, producing which files.

Records the git commit, the environment, the seed list, every run in the index,
and a sha256 for every source file, config, raw result, table and figure, so a
reader can verify that the reported artefacts came from the recorded code.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from .runner import environment_info, git_commit, git_dirty

ROOT = Path(__file__).resolve().parents[1]
HASHED_DIRS = ["src", "configs", "tables", "figures"]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    index = []
    idx_path = ROOT / "results" / "index.jsonl"
    if idx_path.exists():
        index = [json.loads(l) for l in idx_path.read_text().splitlines() if l.strip()]

    files = {}
    for d in HASHED_DIRS:
        for p in sorted((ROOT / d).rglob("*")):
            if p.is_file() and "__pycache__" not in str(p):
                files[str(p.relative_to(ROOT))] = {"sha256": sha256(p), "bytes": p.stat().st_size}
    for name in ("run_all.sh", "seeds.json", "requirements.txt", "README.md",
                 "RESULTS.md", "PAPER_SECTION.md", "environment.txt"):
        p = ROOT / name
        if p.exists():
            files[name] = {"sha256": sha256(p), "bytes": p.stat().st_size}
    for p in sorted((ROOT / "results").rglob("*")):
        if p.is_file() and "_superseded" not in str(p):
            files[str(p.relative_to(ROOT))] = {"sha256": sha256(p), "bytes": p.stat().st_size}

    summary_path = ROOT / "results" / "summary.json"
    summary = json.loads(summary_path.read_text()) if summary_path.exists() else {}

    manifest = {
        "experiment": "Large-Scale Agentic Web Stress Test",
        "paper": "3D Lineage-First Mycelial Fabric — Continual Discovery",
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "git_commit": git_commit(),
        "git_dirty_at_manifest_time": git_dirty(),
        "environment": environment_info(),
        "seeds": json.loads((ROOT / "seeds.json").read_text()),
        "runs": index,
        "n_runs": len(index),
        "total_rows": sum(r.get("n_rows", 0) for r in index),
        "total_runtime_seconds": sum(r.get("runtime_seconds", 0.0) for r in index),
        "scales": summary.get("scales"),
        "questions_per_seed": summary.get("questions_per_seed"),
        "reproduce": "cd experiments/large_scale_agentic_web && FULL=1 ./run_all.sh",
        "notes": [
            "No LLM calls: this experiment is a pure deterministic simulation.",
            "Raw outputs are append-only; results/index.jsonl maps logical run names "
            "to result directories.",
            "results/_superseded/ holds discarded pilot runs and the reasons they were "
            "discarded; nothing reported derives from them.",
        ],
        "files": files,
        "n_files": len(files),
    }
    out = ROOT / "MANIFEST.json"
    out.write_text(json.dumps(manifest, indent=2))
    print(f"wrote {out} ({len(files)} files hashed, {manifest['total_rows']:,} rows across "
          f"{len(index)} runs)")


if __name__ == "__main__":
    main()
