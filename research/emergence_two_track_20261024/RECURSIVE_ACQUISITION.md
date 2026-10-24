# RECURSIVE_ACQUISITION.md — the loop, built, measured, and reported against interest

**Verdict: it does not work at this gate, and the targeting signal does not beat its own control.**

Label: **ESTIMATE / DIAGNOSTIC.** This is an offline measurement over the frozen generator using the same gate
model the ceilings use (`ceiling_b.site_stats` / `gates`), not an e2e run. It answers "would the shipped TB_v4
gate have cleared this key after the extra round", which is the question that decides whether an e2e run is
worth spending. It is not a baseline and must never be quoted as one.

Tool: `tools/plateau/recursive_acquire.py`. Raw: `raw/plateau/recursive_acquisition.{json,txt}`.
Re-derive: `PYTHONPATH=$EMERGENCE_RULE python3 tools/plateau/recursive_acquire.py out.json 0,1,2,3,4,5`.

## 1. The loop, as built

| step | what it does | what it is allowed to read |
|---|---|---|
| DETECT | every `(anchor, chain predicate)` key carrying ≥1 record, scored at every week of its window | the corpus |
| IDENTIFY MISSING SLOTS | each site's count arrives bucketed at k, so a site holding 1…k−1 records exports `'<k'`. A key that fails TB_v4 but shows at least one `'<k'` site is **one disclosure short** | **the exported contract only** — no `Placement` internals, no node ids, no file paths, no value equality, no gold label |
| SELECT HOLDERS | up to **3** of those sites, site id as the tie-break | exported buckets |
| INQUIRE | the holder discloses what it holds on the key **outside** the 6-week window, up to **8** records, oldest first | its own store |
| VERIFY | recount with the inquired sites' window widened, re-apply TB_v4 | the gate |
| STOP | the gate clears, the budget is spent, or a round returns nothing new — at most **2** verification rounds | — |

Budget: 3 holders · 8 claims · 2 verifications, **per key**, spent at one week. Which week is itself chosen from
the exported contract: the week whose buckets are closest to clearing (most sites reporting, then most at
`'<k'`, then latest).

**The control arm** spends the identical budget on the identical keys with the identical widening, and differs
in exactly one thing: it picks its 3 holders by a seeded shuffle of the org's sites instead of by the
missing-slot signal. If targeting is doing work, it must show up here.

## 2. What it recovered

Dev seeds 0–5, 240 gold keys, 297 decoy keys, 5,197 unplanted keys, each scored over a window (gold and decoys
over their own label window, unplanted keys over a span of the median gold length placed by a seeded draw —
scoring one class over all 26 weeks and another over 13 would flatter the first).

| seed | missed gold | targeted recovered | untargeted recovered | targeted unplanted FP | untargeted unplanted FP |
|---|---|---|---|---|---|
| 0 | 7 | 1 | 1 | 27 | 19 |
| 1 | 8 | 0 | 0 | 16 | 12 |
| 2 | 7 | 1 | 0 | 15 | 6 |
| 3 | 5 | 2 | 1 | 18 | 8 |
| 4 | 6 | 1 | 1 | 16 | 12 |
| 5 | 9 | 0 | 1 | 19 | 12 |
| **total** | **42** | **5** | **4** | **111** | **69** |

| arm | gold recovered | decoys newly passed | unplanted newly passed | precision of the extra round |
|---|---|---|---|---|
| targeted | 5 / 42 (11.9%) | 1 | 111 | 5 / 117 = **4.3%** |
| untargeted (budget-matched) | 4 / 42 (9.5%) | 1 | 69 | 4 / 74 = **5.4%** |

## 3. Reading it honestly

**Targeting buys one extra gold key across six seeds and forty-two extra false positives.** On the measure that
matters — the precision of what the extra round admits — the targeted arm is **worse** than the random control
(4.3% vs 5.4%). The missing-slot signal does identify where a key is short; it just turns out that being one
disclosure short is as true of the unplanted keys as of the real patterns, which is the same fact the
specificity-by-world-size measurement found from the other direction.

Against the thresholds we agreed in advance:

- The literature programme's P5 bar was "≥15% of error cases recovered within ≤3 rounds, <5% means it is not a
  primary path". We land at **11.9%** — above the kill line, below the bar, and only by spending precision.
- Our own rule was that the loop must beat a budget-matched untargeted control. **It does not.**

So the loop is built and the answer is no. Reporting it the other way — "recursive acquisition recovers 12% of
missed patterns" — would be true in isolation and misleading in every way that counts.

## 4. Why it fails, mechanically

The acquisition this loop can perform is **backwards**: a holder discloses records it already has from before
the window. That is the only direction available, because a later week's records do not exist yet at the week
the question is asked.

But asking at a later week is already fully priced into the baseline. The `before` column counts a key as
cleared if **any** week of its window clears, so "wait and re-ask" has no headroom left against this measure —
which is also why the gap decomposition puts the re-ask lever at +4/240 (see `EVIDENCE_CEILING.md` §7). What
remains is evidence that is in no week of the window at any site, and the k-per-site invariant says that for 95
of 103 rare patterns there simply are not 3 records at two sites to find.

A loop cannot acquire what the world never disclosed.

## 5. What this does not rule out

- **An e2e run of the same loop.** This estimate uses the gate model, not the shipped pipeline. The pipeline's
  own candidate generation is the thing that fails (142 of 240 gold keys never become a question); a loop that
  acts at *candidate generation* rather than at the gate is a different experiment and is not tested here.
- **A verification step with discriminating power.** The loop as specified asks and recounts. It does not
  *judge* what comes back. A round that could tell a real pattern's late records from an unplanted key's would
  change the precision column, and nothing here says that is impossible — only that counting cannot do it.
- **A different gate.** Every number above is against TB_v4. The ceiling table shows the gate's own conditions
  cost 26 patterns between model F and model B.

## 6. Limits of this measurement

1. It is an estimate. No e2e run, no broker, no drills, no verdict file. Nothing here may be called a result of
   the system.
2. The unplanted class has no natural window; the seeded median-length span is a choice, and a different choice
   moves the false-positive column (not the gold column, which is windowed by the labels).
3. The control's holder draw is one seeded shuffle, not an average over draws. With 42 missed keys the
   difference between 4 and 5 recoveries is one key, and neither arm's gold figure is distinguishable from the
   other at this sample size. The precision gap (111 vs 69) is the part that is not noise.
4. Model K (key-level records) is used throughout, so `before` is 198/240 rather than the 179/240 of model F.
   The comparison between arms is unaffected; the absolute recovery rate would be lower under F.

## 7. The 42 cases, one row each — `RARE_MISS_RECOVERY.csv`

84 rows: each of the 42 missed gold keys under each arm, with the week the budget was spent at, the holders
asked, the claims returned, the verification rounds, and whether the gate cleared.

**All 42 missed keys are rare.** Not one common pattern is missed by the gate model at any week of its window,
which is the same fact `EVIDENCE_CEILING.md` §3 reaches from the other direction.

`corrected_verdict` is **empty in every row, deliberately.** A case counts as recovered only with an actual
corrected verdict from an e2e run, and no such run was spent — the estimate says there is nothing here worth
spending one on. Every row carries `evidence = "ESTIMATE (ceiling_b gate model); not a verdict"` so the column
cannot be mistaken for one later.

Which cases cleared:

| arm | cleared | cases |
|---|---|---|
| targeted | 5 | s0/p003, s2/p013, s3/p008, s3/p030, s4/p026 |
| untargeted | 4 | s0/p003, s3/p008, s4/p026, s5/p008 |

Three cases clear under both arms. Targeting uniquely wins two (s2/p013, s3/p030) and uniquely loses one
(s5/p008), for a net of **+1 key out of 42**. That is a coin flip, and no claim about targeting's recall should
rest on it. The precision column — 111 unplanted keys admitted against 69 — is the part with signal, and it
points the wrong way.

Every one of the 42 had at least one holder to ask (`holders_asked` is never 0), so the loop was never starved;
it asked, got 38 records back across all 42 cases, and the gate still said no.
