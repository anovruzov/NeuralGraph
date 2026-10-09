# Vehicle pack v2

`pack-v2/` is the vehicle pack with three fixes from plan item 1.5 of the research handoff
(`docs/handoff/HANDOFF-2026-10-09.md`). `pack/` stays as it is: V001 to V003 and R002 use it. **No replay has run on
v2.** A replay that uses it needs its own choice file.

- **Built by** `tools/market/vehicle_pack_v2.py`, from `pack/` and `nhtsa-probe.json`:

  ```
  python tools/market/vehicle_pack_v2.py --pack docs/collective/replay/vehicles/pack \
      --probe docs/collective/replay/vehicles/nhtsa-probe.json --out docs/collective/replay/vehicles/pack-v2
  ```
- **Checked by** `tests/market/test_vehicle_pack_v2.py`: the committed files are byte for byte the builder's output,
  the pack loads, and its four hashes are pinned.
- **Same as `pack/`:** the pack id `vehicle_complaints`, the vehicle ids, egress (k = 3), rules, questions and
  follow-ups. The version is 0.2.0.

## 1. One name per component

NHTSA's files carry two names for some components. The complaint file's notes say that since its May 2021 update it
shows component names as they changed over time (`nhtsa-probe.json`, `docs`). Many recalls use the old names (213 of
896 recall outcomes) and complaints the new ones, so such an outcome could never match an alert (handoff mistake 7).
`pack/` kept three old names as categories of their own, each with its own predicate:

| Old name (recalls) | New name (complaints) | v2 predicate |
|---|---|---|
| ENGINE AND ENGINE COOLING | ENGINE | `engine` |
| FUEL SYSTEM, GASOLINE | FUEL/PROPULSION SYSTEM | `fuel_propulsion_system` |
| SERVICE BRAKES, HYDRAULIC | SERVICE BRAKES | `service_brakes` |

v2 maps each old name to its new name's code. The three old predicates and their codes (NHT-019 to NHT-021) are gone,
and their phrases join the new predicate's lexicon. The mapping still lists the old names, so the exporter writes the
same `components[]` column as before. An outcome filed under an old name now gets the new name's predicate.

**The check** (`name_check`) resolves every component name in the probe the way an outcome is resolved. A name passes
when its predicate is also the predicate of a name in the complaints' scheme (every name but the three old ones), or
when it matches any failure (a name outside the pack).

```
python tools/market/vehicle_pack_v2.py --check docs/collective/replay/vehicles/pack --probe docs/collective/replay/vehicles/nhtsa-probe.json
python tools/market/vehicle_pack_v2.py --check docs/collective/replay/vehicles/pack-v2 --probe docs/collective/replay/vehicles/nhtsa-probe.json
```

| Pack | Names checked | Failing |
|---|---|---|
| `pack/` | 40 | 3: the three old names, whose predicates no complaint name reaches |
| `pack-v2/` | 40 | 0 |

Limits of the check:
- **The probe holds no outcome names.** `nhtsa-probe.json` lists the complaint file's 40 largest component
  categories (2015 to 2024) with counts. Its recall section and `nhtsa-inv-probe.json` hold only sizes, row counts and
  field lists. So the check runs over the 40 complaint categories, each treated as a possible outcome name. Recall and
  investigation components are top-level categories of the same kind.
- **Which names are old comes from the handoff and R001**, not from the probe: the probe has no dates per name.
- **The check can fail only on the three listed names.** It takes every other name as the complaints' scheme, so
  every other name passes by construction. So "0 failing" shows that the merge is applied. It does not show that the
  list of old names is complete.
- **Other probable old names pass through unknown or other.** 14 of the 40 names resolve to unknown or other, whose
  outcome matches any failure on its vehicle, so they pass. Five of them look like old names whose new name is a
  pack category: VISIBILITY (5,680 complaints, for VISIBILITY/WIPER), FUEL SYSTEM, DIESEL (962), SERVICE BRAKES, AIR
  (570), HYBRID PROPULSION SYSTEM (356) and FUEL SYSTEM, OTHER (135). The counts are the probe's. `pack/` already
  folded them there (VISIBILITY lost its phrases to VISIBILITY/WIPER; the others are under the 1,000 complaints a
  category needs, `CHOICE-V001.md`). v2 leaves them there; the handoff names three pairs. The test pins the 14 names.

## 2. A missing reporter is not one reporter

`pack/`'s mapping has no reporter field. A site counts every unknown reporter as one shared reporter
(`edge/site.py`, `build_cells`), so every cell of three or more complaints had one reporter. That is below k, so the
count left the site suppressed, and the detectors applied the few-reporters penalty to every burst (handoff mistake 9).

v2's mapping reads a `reporter` column. `tools/market/nhtsa_export.py` writes it only when the pack's mapping names
one (`pack_reporter`), and it fills it with the complaint's own `ODINO`. NHTSA publishes no complainant id, so each
complaint counts as its own reporter. Model-free R already counts it that way. The pinned code is unchanged.

v2's mapping also lists `reporter` as required. A row without it is refused (`missing_reporter`) instead of falling
back to the shared unknown reporter. So an export written without the column maps to no record, and the audit stops.
Such exports come from the exporter at `7a94535`, or from any caller of `complaints()` that passes no reporter
(`lab.goldlabels` does, for `pack/`).

**One complaint, one reporter, is an assumption, not a measurement.** One person who files several complaints counts
as several reporters. Under v2 a cell's reporter count equals its record count, so on NHTSA data:
- the few-reporters flag (G4's `few_reporters`, a published record count with a suppressed reporter count) can no
  longer fire;
- pushdown's `min_independent_reporters` (3, equal to k) is met by any confirming site, since a site confirms only
  with at least k records.

The tests show both sides:
- **`pack/` exports exactly as before.** A fixed set of 80 synthetic complaints (53 kept) exports to the same bytes as
  with the exporter at `7a94535`, before this change (sha256 `46a2c74f5d40…`), with no reporter column.
- **v2's export** is the same rows with one more column, `reporter`, equal to `odino`.
- **An export without the column** maps to 53 records under `pack/` and to none under v2 (53 refused,
  `missing_reporter`), and the audit stops.
- **Three complaints in one cell** at one site count 3 records and a suppressed reporter count (`<k`) under `pack/`,
  and 3 records and 3 reporters under v2.

## 3. Detector thresholds for 60 sites

`pack/`'s `detectors.json` is the device pack's, set for 6 plants. The replays ran it with each state a site, about
60 sites (handoff mistake 4). v2 changes three settings:

| Setting | `pack/` | `pack-v2/` | Why |
|---|---|---|---|
| `burst.alpha_site` | 0.01 | 0.001 | Ten times the sites, so a tenth of the per-site test level |
| `burst.min_sites` | 2 | 3 | Exceeding sites a burst needs |
| `cooccurrence.min_sites` | 2 | 3 | Rising sites a co-occurrence rise needs |

Everything else in the file is unchanged: the window (8 weeks), the baseline, the history, the cooldown, the alert
budget and the ranker.

**The null world** is synthetic: the pack generator's fictional world (6 fictional vehicles, 24 predicates, 3 to 7
records a site a week) with 60 sites, nothing planted, over 106 weeks, the replays' span. That is 31,638 to 31,898
records per world, near the FORD export of V001 (27,397 complaints over 60 sites, `CHOICE-V001.md`). It runs
through the pilot audit's own pipeline: lexical extraction, k-suppressed cells, X and S at HQ, and model-free R.

**The rate** (`tools/market/vehicle_null_rate.py`). Each series (vehicle and predicate) is tested once in each of the
87 evaluated weeks. The null candidate rate is the share of those tests at which the series is a detector candidate.
Nothing is planted, so every candidate is false. It is also given over the busiest quarter of the series, where noise
candidates gather, and as the share of series that are ever a candidate in the 87 weeks.

**The bar** is the handoff's: at most 5%. `vehicle_null_rate.passes` requires it in every channel, over all series and
over the busiest quarter. **The choice** took the first rung of a ladder, least strict first, that passed on
calibration seeds 1 to 3 (`null-rate/grid.json`):
- **L1:** `alpha_site` 0.001;
- **L2:** L1 with both `min_sites` at 3;
- **L3:** L1 with both `min_sites` at 4.

The rule and the ladder were written after exploratory runs and after seeds 1 to 3 were read. Seeds 4 to 6 ran beside
them and were read only after L2 was set in the builder.

**Before and after, at 60 sites** (pooled over 3 seeds, 37,584 tests per channel):

| Seeds | Setting | X | S | R_mf | Busiest quarter, highest channel | Series ever a candidate, X |
|---|---|---|---|---|---|---|
| 1-3 (calibration) | `pack/` | **26.15%** | 3.61% | **7.01%** | X **26.10%** | 432 of 432 |
| 1-3 | L1 | 1.23% | 0.56% | 1.56% | R_mf **6.12%** | 177 of 432 |
| 1-3 | **L2 (v2)** | 0.09% | 0.09% | 0.57% | R_mf 2.29% | 23 of 432 |
| 1-3 | L3 | 0.00% | 0.01% | 0.15% | R_mf 0.59% | 1 of 432 |
| 4-6 (held out) | `pack/` | **26.26%** | 3.67% | **6.72%** | X **26.44%** | 432 of 432 |
| 4-6 | **L2 (v2)** | 0.11% | 0.13% | 0.51% | R_mf 2.05% | 29 of 432 |

Rates in bold miss the bar. The busiest-quarter column gives the highest of the three channels. L2 passes on every
seed by itself, in every channel.

**L2 misses 5% on another reading.** The bar is on the per-test rate. Read as the share of series that are ever a
candidate in the 87 weeks, L2's X is 23 of 432 (5.3%) on seeds 1 to 3 and 29 of 432 (6.7%) on seeds 4 to 6. Its S
and R_mf are at most 18 of 432 (4.2%). Both the measure and the ladder were chosen after seeds 1 to 3 were read.

For reference, at 6 sites the same world gives X 0.38%, S 0.06% and R_mf 0.17% with `pack/`'s settings, which pass,
and none with L2.

Commands (each writes the JSON file named):

```
python tools/market/vehicle_null_rate.py --pack docs/collective/replay/vehicles/pack-v2 \
    --detectors docs/collective/replay/vehicles/pack/detectors.json \
    --grid docs/collective/replay/vehicles/null-rate/grid.json \
    --sites 60 --seeds 1 2 3 --out docs/collective/replay/vehicles/null-rate/sites60-seeds1-3.json
# the same with --seeds 4 5 6 (sites60-seeds4-6.json), with --sites 6 --seeds 1 2 3 (sites6-seeds1-3.json),
# and with --vehicles 60 (sites60-vehicles60-seeds1-3.json, the sparser world below)
```

Each run records the hashes of the pack it ran on, and the tests check all four against the committed `pack-v2/`.
The three runs first committed had been made on an earlier state of `pack-v2/`, before L2 was written into it. Their
rerun on the committed pack gave the same worlds and rates; only the pack's hashes changed.

**The smoke plant still shows.** The pilot audit's demo
(`python -m mycelic.collective.pilot.audit demo --pack docs/collective/replay/vehicles/pack-v2 --out DIR`) plants the
pack's two smoke patterns (3 states each, 2 records a week for 8 weeks) into 52 weeks of the 60-site world. "Before"
is the same command on a copy of pack-v2 with `pack/`'s `detectors.json`.

| | X alerts (matching no plant) | Review list | X: p1, p2 found | S and R_mf: p2 found |
|---|---|---|---|---|
| `pack/`'s settings | 165 (162) | 128 patterns | 113 and 29 days early | 29 and 36 days early |
| v2's settings | 6 (4) | 9 patterns | 8 and 1 days early | 1 and 29 days early |

With `pack/`'s settings X spends its whole budget, 5 alerts a week for 33 weeks, almost all on noise. Its "113 days
early" for p1 is a noise alert from before the plant began. S and R_mf also "found" p1, which is written in the
narratives only, so by noise alone. With v2's settings both plants are found and noise is rare. The cost shows too: p2
is found 1 day before it opens instead of 29.

**What this does not show:**
- **The handoff's own figures are not reproduced.** It gives a null candidate rate of 43% at 60 sites and 1.8% at 6
  from a simulation not in the repository. Its world and its exact measure are unknown, so the numbers here are not
  comparable to those.
- **One synthetic world, and the verdict on `pack/` depends on it.** The generator draws each record's vehicle and
  predicate evenly, so all 144 series have about the same volume. A sparser world (`--vehicles 60`: 60 fictional
  vehicles instead of 6, 31,626 to 32,010 records a world, 1,440 series a seed) has far fewer noise candidates.
  There `pack/`'s settings give X 0.27%, S 0.15% and R_mf 0.24% (seeds 1 to 3 pooled; busiest quarter at most
  0.95%) and pass. Seed 1 alone gives X 0.23%, S 0.12% and R_mf 0.20%. L2 gives no candidate in any channel. On the
  series-ever reading `pack/`'s X is 241 of 4,320 (5.6%). So whether `pack/`'s thresholds pass at all depends on the
  synthetic world chosen. The denser world is the harder test, and the one used. Real exports mix a few busy series
  with many sparse ones.
- **No power on real-sized signals.** Stricter settings also miss weak real rises. Whether v2 detects the slow
  pre-event ramps DIAG-E001 describes is the power check's question (handoff task 1.2), and must be answered before a
  replay uses this pack.
- The rules channel (`rules.json`, 2 states) is not a candidate source and is unchanged.

## Files

- `pack-v2/`: the pack. The loader refuses any file it does not know, so this note lives beside it.
- `null-rate/grid.json`: the ladder. `null-rate/sites60-seeds1-3.json`, `sites60-seeds4-6.json`,
  `sites6-seeds1-3.json` and `sites60-vehicles60-seeds1-3.json`: the runs above, with every seed's counts.
- `tools/market/vehicle_pack_v2.py`, `tools/market/vehicle_null_rate.py`, and the reporter column in
  `tools/market/nhtsa_export.py`.
- `tests/market/test_vehicle_pack_v2.py`.
