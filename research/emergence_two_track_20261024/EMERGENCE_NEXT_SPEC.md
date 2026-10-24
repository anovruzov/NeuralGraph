# EMERGENCE-Next v0 (Track B): a separately versioned protocol

Status: draft, written 2026-10-24 before any Track B held-out seed exists. It changes nothing in
`docs/collective/EMERGENCE-80/SPEC.md` (Track A). A Track B figure is never reported as, or beside, a Track A
`recall_all` without saying which protocol produced it.

## 1. What differs from R0, and only this

| | Track A (R0, frozen) | Track B v0 |
|---|---|---|
| Generator, labels, scorer | frozen | **the same files, byte for byte** |
| Disclosure (what crosses a site Boundary) | k = 3 buckets, closed artifact list | **identical** |
| Gate | ≥ 2 sites each with ≥ k records | N1: ≥ 1 site with ≥ k records, plus up to 1 site that confirms with < k (`pushdown.weak_corroborating_sites: 1`); roots, reporters, support and freshness counted over the k-record site(s) only |
| Site roots | exact folded text | near-duplicate echoes are one root (`egress.near_duplicate_roots {jaccard 0.65, days 14}`) |
| Candidate source | detector snapshots, cap 60 | to be frozen from development ablations before the freeze commit (§4) |

Rationale (measured, development seeds 0–5): no Gold pattern lacks evidence; 54 of 240 are unreachable under R0
only because their second independent site holds 1–2 records; that class holds 20 of 300 decoys. N1 publishes
nothing new: today's fabric already receives every site's verdict with its `<k` bucket.

## 2. Metric and gate

`recall_all` as `Harness.discovery` computes it, on the real process-mode `run` job, with the pack carrying the two
Track B keys. Gate, applied once on 20 held-out worlds at 200 users: mean ≥ 0.80 and the two-sided 95% Student-t
lower end ≥ 0.80; every privacy check passes on every world; total decoy acceptance (five classes) no higher than
the Track A baseline's on the same 20 worlds; failed worlds count 0.

## 3. Seeds

Same derivation as Track A, with distinct set names so the two tracks can never share seeds:
`int(sha256("EMERGENCE-NEXT " + S + "|" + C + "|" + str(j)).hexdigest()[:8], 16)`, S ∈ {preliminary, held-out}, C the
full sha of the Track B freeze commit; skip development seeds 0–19 and any seed in an earlier set. No Track B
preliminary or held-out seed is generated before its freeze commit is pushed; each freeze commit is evaluated once.

## 4. Before the freeze

1. Development ablations, each lever alone then combined, on seeds 0–5 (and 6–19 if needed), all labelled.
2. Independent adversarial review of the candidate (method, privacy, leakage, decoys).
3. The `check_world_counts` guard replaced (it refuses every seed but 0), or every non-zero seed run is a
   disclosed diagnostic.
4. The founder's decision on N1 as a corroboration policy (it changes no disclosure but it is a gate rule).

## 5. The enterprise intelligence benchmark (Track B, part 2)

A separate benchmark with persistent workers (structured profiles: enterprise, legal entity, subsidiary, division,
department, team, manager, title, seniority, tenure, location, region, timezone, clearance, permissions, projects,
systems access, expertise, collaborators, memory graph id), each with a private NeuralGraph of 1,000–10,000+
memories, fragmented evidence across subsidiaries and locations, copies and rumours with shared upstream sources,
and authorization boundaries that can forbid aggregation. Requirements fixed now:

- Workers receive only their profile, their authorised graph context, their task, and what the system delivers.
- Live Qwen inference for representative workloads, with real call counts; 10k / 50k populations disclose inference
  sampling and memory materialisation (measured footprint 4,222 bytes per NeuralGraph node: 10k × 1,000 ≈ 42 GB,
  50k × 5,000 ≈ 1.06 TB).
- Logical site or unit ids never act as wildcard authorisation.
- Its own generator, labels, scorer, seed sets and freeze commit; never mixed with EMERGENCE figures.

Not built yet: it needs the user's machine (Qwen) and a storage decision (materialise vs disclosed lazy
materialisation).
