# CLEANUP_LOG — audit/repository-readiness-2026-09-06

Audit session 2026-09-06 (local machine, macOS arm64). The three cleanup
commits from the prior cloud session (`e45c5c7a`, `35570724`, `130e82da`)
were lost with that workspace and are recreated fresh here from
`origin/main` (`8785d691`). Every action below happened in an isolated
worktree; the original dirty checkout at `/Users/novruz/NeuralGraph` was
never modified, and its uncommitted files are fingerprint-verified at
handoff.

## Actions actually taken

### 1. Removed 22 tracked Python bytecode caches — commit `999823f`

| Field | Value |
|---|---|
| Paths | `NeuralGraph/__pycache__/*.cpython-312.pyc` (22 files) |
| Classification | DELETE |
| Ownership | Build output, no author ownership; introduced with the original bulk import on main |
| Reason | Regenerable interpreter output; stale (CPython 3.12 caches for sources since edited) |
| Checks | No reference from any tracked `*.py`, `*.md`, `*.txt`, `*.ini` (`git grep __pycache__` empty at main); not imported; not used by tests, packaging, or docs; not an experiment input or record |
| Size | ~0.6 MB in the checkout (historical Git objects retained — no repository-history size saving claimed) |
| Recovery | `git checkout 8785d691 -- NeuralGraph/__pycache__/`; or run Python 3.12 to regenerate |
| Extra | Added `.gitignore` taken **verbatim** from `13a1729` (research branch) so a later merge of that branch cannot conflict on it |

### 2. Reserved `evaluation/agentic_web/` — commit `f48da4a`

| Field | Value |
|---|---|
| Paths | `evaluation/agentic_web/README.md` (new) |
| Classification | KEEP (addition) |
| Reason | Isolated boundary + experimental contract + readiness gates for the future N=100/1K/10K+ benchmark. Deliberately **no** code modules: nothing is presented as implemented |

### 3. Hardcoded benchmark credential → environment lookup — commit `9591a18`

| Field | Value |
|---|---|
| Path | `demo/runner.py` line 72 (1 line changed; `import os` already present) |
| Classification | Security fix |
| Ownership | Retrieval-track file (Nurman's per CLAUDE.md rule 3) — minimal security-only edit on the audit branch, mirroring the identical fix Nurman made in `eed4873`; flagged for his review |
| Reason | Live OpenAI project key (secret #2 of the scan) hardcoded on public `origin/main` and 14 other tips |
| Verification | `python -c compile()` OK; zero `sk-proj-` matches remain in the file |
| Recovery | n/a (nothing deleted; the key remains in history — rewriting was not performed) |

Recreation of lost commit `e45c5c7a`. **Credential rotation remains
REQUIRED and UNRESOLVED** for all four discovered keys — removal never
revokes.

## Explicitly NOT touched

| Item | Why |
|---|---|
| Everything on `research/retrieval-campaign-2026-09` (PR #3, Nurman) | BLOCKED — CONTRIBUTOR APPROVAL REQUIRED. Audited read-only |
| The six retrieval-track files (`answering.py`, `llm_profile_extractor.py`, `prompts.py`, `reranker.py`, `service.py`, `tesseract.py`) and `demo/runner.py` on any branch | Nurman's per CLAUDE.md rule 3 |
| Uncommitted changes in `/Users/novruz/NeuralGraph` (4 modified files, `demo/.cache/`, `capability_survival.pdf`) | Original local work — preserved untouched, fingerprinted |
| `stash@{0}` ("pre-replay 2026-08-21") | Possibly the only record of pre-campaign retrieval/coordination experiments; contains edits to Nurman-track files. UNKNOWN ownership → retained |
| `demo/` result JSONs and `evaluation/locomo/locomo10.json` | Experiment inputs/outputs; the sole `AIza`-pattern match in `locomo10.json` was verified to be a false positive inside base64 data, not a key |
| Stale prunable worktree entry (`baseline-head` scratchpad) | Harmless metadata; pruning needs no urgency and the user may want it |
| `master` branch (unrelated history, SporeOS resume) | Historical record; deleting research-adjacent branches is out of scope |

## Incident log

During test attribution, a defensive `git stash pop` in the **audit
worktree** mistakenly applied the pre-existing `stash@{0}` (nothing of
ours had been stashed, so `pop` took the historical entry). The pop
conflicted, so the stash entry was **not dropped**; the worktree was
restored with `git reset --hard f48da4a && git clean -fd`. Verified
after: worktree clean, `stash@{0}` intact (8 files, +454/−107), original
checkout unaffected.
