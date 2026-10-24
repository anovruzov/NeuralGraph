# Live simulation runbook (EMERGENCE, 200-user enterprise)

Everything here has already run end to end in this environment: on the real NATS JetStream fabric, with one OS
process per site and the word-matching site judge. Label every number you show **diagnostic**: development seeds,
drills skipped.

## 0. Preflight (2 minutes)

```bash
export ARMS_SCRATCH=<scratch dir holding nats/nats-server, tools/ablate.py and em/>
export WT=<emergence code tree>            # the tree the v4 and rule-first runs used
"$ARMS_SCRATCH/nats/nats-server" --version  # 2.10.29 in the recorded runs
df -h "$ARMS_SCRATCH"                       # ~150 MB per 200-user run
```

## 1. The live run (about 4.5 minutes per seed)

```bash
TAGP=live- tools/arms.sh b60_rs_nd_n2_rulefirst 0        # one seed, live
TAGP=live- tools/arms.sh b60_rs_nd_n2_rulefirst 0 1 2    # three seeds, about 14 minutes
```

What the log shows, in order, and what to say:

| log line | meaning |
|---|---|
| `world: 309 users, 7 sites, 12964 records; … 60 asked` | The enterprise is generated, every site has ingested its own records privately, and HQ has run detection on the suppressed counts. |
| `main pass (process): 60 questions, restart site … after N` | HQ asks 60 targeted questions, each routed to 3 sites. One site process is killed mid-pass and reopened; the run must survive that. |
| `drill 2: SIGKILL … reopened; probe route …` | The killed site is excluded while down; the router sends its probe elsewhere. |
| `main pass: 60 questions … in ~145s; statuses {…}` | Verdicts are in. About 2.2 s median and 3.4 s p95 per question, question to verdict. |

Expected per seed, from the recorded runs (rule-first, 60 questions):

| seed | 0 | 1 | 2 | 3 | 4 | 5 |
|---|---|---|---|---|---|---|
| planted patterns found / 40 | 19 | 18 | 23 | 26 | 20 | 21 |

Across all six seeds: 127/240, decoys 113 (limit 118), 13 confirmed with no pattern. Official baseline: 93/240.

## 2. Refresh the world map from the live run (30 seconds)

```bash
python3 tools/worldmap/build.py mycelic_world_map.html \
  "$ARMS_SCRATCH"/em/abl-live-b60_rs_nd_n2_rulefirst-s0/e2e-ingest-report.json \
  --pools <dir with pool_s0.json>   # optional answer-key overlay (tools/rescue/dump_pools.py)
```

Then republish the page to the same artifact. The map shows regions and sites (sized by records held), the entities
asked about, a line for every question, verdicts, and the hashes of the records behind each finding.

## 3. What to claim, and what not to

Claim:
- Discovery across private owners: raw text never leaves a site; HQ sees only counts under k-suppression, verdicts and
  record hashes.
- Every confirmed finding has verifiable lineage: record SHA-256s, reconstructable from the owning site.
- It survives a site being killed mid-run.
- 127/240 (52.9%) vs the official 93/240, at the same question budget, no extra false discoveries (diagnostic).

Do not claim:
- 80% or more, or any 10K or 50K accuracy. The 10K run is a diagnostic with no score yet, and the planted-evidence
  ceiling at 10K under the current privacy rule is 68.3%.
- Held-out results. None exist yet.
- Anything about another company's system that has not been measured here.
