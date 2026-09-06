# 3D Lineage-First Mycelial Fabric - Continual Discovery

Anonymous NeurIPS 2026 Agentic Web long-paper draft.

## Submission state

- Main paper: **9 content pages exactly** in the validated build; references and appendices begin on page 10.
- Double-blind: author/institution/email/URL identifiers are removed from the manuscript and PDF metadata.
- Target: **1st NeurIPS Workshop on Agentic Web: Decentralized, Continually-Adapting Agent Ecosystems**.
- Evidence policy: numerical claims come from the audited repository artifacts. The manuscript explicitly states that no N=100/1,000/10,000 agent-population experiment currently exists.
- Continual discovery and full hierarchical aggregation are explicitly labeled proposed rather than measured.

## Build

Place the official NeurIPS 2026 `neurips_2026.sty` from the author kit beside `main.tex`, then run:

```bash
pdflatex main.tex
pdflatex main.tex
pdflatex main.tex
```

The source uses the NeurIPS 2026 double-blind workshop option. Re-check that references begin after page 9 using the exact official style file before OpenReview upload.

## Evidence base

The paper is grounded in the current research audit/results under `docs/research`, the consolidated benchmark ledger in `docs/BENCHMARKS.md`, the deterministic coordination benchmark artifacts, and the supplied prior architecture manuscript. It preserves negative results and separates retrieval accuracy from capability survivability.
