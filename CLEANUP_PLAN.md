# Cleanup plan — KEEP / REWRITE / ARCHIVE / DELETE

Companion to `AUDIT.md`. Every disposition names a reason. Nothing is deleted
because it disagrees with the current paper.

---

## DELETE — proposed list

**The list is empty.** Every category the brief anticipated was checked and
has no members in this repository:

| Category | Checked by | Result |
|---|---|---|
| Tracked `__pycache__`, `.pyc` | `git ls-files \| grep -iE '__pycache__\|\.pyc$'` | none tracked (already gitignored) |
| Editor / OS junk (`.DS_Store`, `.swp`, `~`) | same | none tracked |
| Duplicate PDFs | `git ls-files \| grep -iE '\.(pdf\|tex\|docx)$'` | **no PDFs exist at all** |
| Duplicate base64 payloads | repo-wide scan | none |
| Byte-invalid manuscript payloads | no manuscript files exist | n/a |
| Obsolete public-site references | only 2 external links, both valid | none |
| Accidentally committed secrets | `git log --all -- .env` | **never committed** |

**Nothing will be deleted.** No credential rotation, no history rewrite, no
`git rm`.

Untracked build noise (`demo/.cache/`, `.pytest_cache/`, `__pycache__/`,
`.DS_Store`) is left on disk and covered by `.gitignore`. Removing files the
user did not ask about is out of scope.

---

## ARCHIVE

Moved with `git mv` to `archive/`, contents unchanged, each given a header
stating what superseded it. Preserved because they record real design intent
and history.

| File | → | Reason |
|---|---|---|
| `QUERY_ROUTING_IMPROVEMENTS.md` | `archive/QUERY_ROUTING_IMPROVEMENTS.md` | Four measured accuracy baselines **CONTRADICTED** by `evaluation/artifacts/accuracy_diagnosis.json`; pattern counts internally inconsistent (headline 2,000+ vs table 5,100+); "Expected: 70-80%" is a projection formatted as a result. Superseded by `docs/ACCURACY.md`. See `AUDIT.md` §5. |

`archive/README.md` explains the directory and why each file is retained.

---

## REWRITE

| File | Change | Reason |
|---|---|---|
| `CLAUDE.md` | test count 185 → 266; `python3`/`pytest` → `python3.11 -m …`; narrow the N2 statement to the I=1 cell | Stale count; bare `python3` is 3.9 and cannot collect the suite; N2 overstated (`AUDIT.md` §2) |
| `state.md` | same three corrections; note `.env` gitignore fix; point to `AUDIT.md` | same |
| `docs/REPRODUCE.md` | every command → `python3.11`; add `sha256sum`→`shasum -a 256` note for macOS; add the table-generation command | Commands as written fail on this machine |
| `docs/RESULTS.md` | narrow N2 to the I=1 cell | `AUDIT.md` §2 |
| `README.md` | add the "eight strategies ≠ eight architectures" caution; state transport is not implemented; link `AUDIT.md` | Prevents the most likely misreading (`AUDIT.md` §3) |
| `docs/PAPER.md` | update checklist to reflect executed verification; correct test count | Reflects what was actually run |

---

## CREATE

| File | Purpose |
|---|---|
| `AUDIT.md` | Claim-to-evidence table (deliverable 1) |
| `CLEANUP_PLAN.md` | This file (deliverable 2) |
| `docs/paper/capability_survival.md` | Corrected authoritative manuscript (deliverable 3) |
| `docs/paper/tables.md` | Tables generated from pinned artifacts (deliverable 4) |
| `tools/build_paper_tables.py` | Generator; `--check` verifies digests before emitting | 
| `archive/README.md` | Explains the archive |
| `VERIFICATION.md` | Executed test + artifact-reproduction report (deliverable 8) |

---

## KEEP unchanged

| File | Reason |
|---|---|
| `docs/ARCHITECTURE.md` | Already states NATS/JetStream unimplemented and Mycelic absent. Accurate. |
| `docs/ACCURACY.md` | Measured, artifact-backed, explicitly says nothing is fixed |
| `NeuralGraph_System_Architecture.md` | Accurate design doc for the local memory engine. Every referenced module exists; parameters match code (`heat_decay_rate` 0.015 → `consolidation.py:87`). Scoped to Nurman's track; simply does not discuss coordination. |
| All `NeuralGraph/**/*.py` | No code changes in a truth-and-consistency cleanup |
| All artifacts + `SHA256SUMS` | Pinned, byte-reproducible, verified |
| `evaluation/**` | Measured and artifact-backed |

---

## NOT TOUCHED

| Item | Reason |
|---|---|
| `stash@{0}` | Explicitly out of scope. Metadata read only. |
| Remote branches | No push, no delete |
| Git history | No rewrite |
| Retrieval-track files | `CLAUDE.md` rule 3 — Nurman's ownership |
| The pasted Mycelic preprint | Not a repository file. Audited in `AUDIT.md` §3; disposition is the author's call. |

---

## Portfolio artifacts (deliverable 6)

**No portfolio site exists in this repository.** The only external links are
`https://github.com/anovruzov` (README:183) and `https://ollama.com/`
(README:132). Both are valid and neither points to a paper artifact. Nothing
to update. If a portfolio lives outside this repo, it should link to
`docs/paper/capability_survival.md`, not to any Mycelic architecture claim.
