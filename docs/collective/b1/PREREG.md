# B1 pre-registration: the constructed codes-miss illustration (Tarnwick Devices, fictional)

**Committed with B1a, before any codes-miss world exists, and never edited afterwards.** It fixes the cast of the
second demo scenario (`demo/collective/scenario_codes_miss.json`, written in B1b), the rule that decides whether a
recording illustrates the case, the robustness seeds and grid, and how many attempts may be made. Every recording of
the scenario is judged by this file; a change to anything fixed here is a new pre-registration, not an edit.

**Disclosure.** The authors of the detectors and the baselines wrote this cast, knowing how R ranks. The scenario
shows that the mechanism is possible on a constructed world; it says nothing about how often such cases occur.

## 1. What the scenario is meant to illustrate

The hypothesis the pilot tests (STRATEGY sections 6.1, 9.2 and 10.1): the failure mode is written only in the
complaint narratives, and the fields allowed to leave a site carry only a generic, high-base-rate code that is rising
for unrelated reasons across many products. R (model-free, over the allowed fields) and S (over the codes) then have
no case-specific signal; X (in-boundary extraction) surfaces the failure mode, and pushdown verification confirms it
at the sites with no text leaving. Here the unrelated rise is a background change: a new complaint-intake form at five
plants writes the generic code on every complaint, on every product, from one detector window before the case to the
end of the world.

Every screen, run file and document that shows the scenario carries these sentences verbatim:

- STATEMENT: `Constructed illustration of the case codes miss. Whether such cases occur in real data is exactly what N1 and the Phase-1 signal audit measure.`
- AUTHOR_NOTE: `Constructed by the authors of the detectors and baselines, who knew how R ranks; it shows the mechanism is possible, not that it is common.`

## 2. The world

| Setting | Value |
|---|---|
| Pack | `device_quality` |
| Main seed | 29 |
| Robustness seeds | 31, 37, 41, 43, 47, 53, 59, 61 |
| Weeks | 40 |
| Tie salt | `collective-demo-codes-miss` |
| Company | `Tarnwick Devices (fictional)` |
| Enterprise | `tarnwick`; the run id slug is `tarnwick` |
| Case kind | `codes_miss` |

**Org.** The pack world's six generator plants, in its order, with the display names of the first scenario:

| Site | Unit path | Country |
|---|---|---|
| `plant-ashvale` | `tarnwick/gb/ashvale` | GB |
| `plant-brindlemoor` | `tarnwick/gb/brindlemoor` | GB |
| `plant-corrowfield` | `tarnwick/ie/corrowfield` | IE |
| `werk-dornhagen` | `tarnwick/de/dornhagen` | DE |
| `werk-erlenbruch` | `tarnwick/de/erlenbruch` | DE |
| `plant-fennick` | `tarnwick/us/fennick` | US |

**Approvers** as in the first scenario, under unit `tarnwick`: `qe-owner.tarnwick` (quality engineer),
`qm-escalation.tarnwick` (quality manager), `sqe-owner.tarnwick` (supplier quality engineer). **Follow-ups**:
`evidence_packet` (no args), then `capa_initiation_draft` with severity `medium`.

## 3. The cast

**Hero** `hero-overheat` (role hero):

| Field | Value |
|---|---|
| Key | `lot:L10002:overheat` (a universe lot; the generator's link parent is `SD-9`) |
| Visibility | `narrative_only` |
| Codes | `["ILL-9001"]` |
| Start week, weeks | 30, 6 |
| Sites | `plant-brindlemoor` en, `plant-corrowfield` en, `werk-dornhagen` de; `rate_per_week` 1 each |
| Structured | lot `[L10002]` 0.6; product `[SD-9]` 0.95; component `[PUMP-HOUSING]` 0.3; supplier `[V1001]` 0.2 |
| Narratives, en | `Units from lot {lot} overheated while charging.`; `A unit from lot {lot} was too hot to touch after charging.` |
| Narratives, de | `Geräte der Charge {lot} sind beim Laden überhitzt.`; `Bei einem Gerät der Charge {lot} kam es zu einer Überhitzung.` |
| Slots, filler, reporter, copies | `{"lot": "L10002"}`; `[2, 2]`; `pool`; none |

**Sibling** `sibling-quarantine` (role sibling, visibility none) at `plant-fennick` (en): key lot `L10002` with no
predicate, codes `["ILL-9002"]`, structured lot `[L10002]` and product `[SD-9]` at 1.0, narrative `Lot {lot} was
quarantined for review.`, slots `{"lot": "L10002"}`, start week 30, weeks 6, rate 1, filler `[2, 2]`, reporter
`pool`, no copies.

**Decoys** (templates, filler and reporters as in the first scenario's decoy of the same class):

1. **Echo flood** `decoy-echo-flood`: `lot:L20078:crack`, narrative-only, at `plant-brindlemoor` (en), rate 2, start
   week 31, weeks 4, no codes and no structured fields, narrative `Units from lot {lot} arrived cracked.`, filler
   `[1, 2]`, reporter `pool`, marked copies to `plant-ashvale`, `werk-erlenbruch` and `plant-fennick`.
2. **Single-reporter burst** `decoy-single-reporter`: `lot:L20098:occlusion`, narrative-only, at `plant-brindlemoor`
   (en) and `werk-erlenbruch` (de), rate 3, start week 31, weeks 5, no codes and no structured fields, narratives
   `The line on units from lot {lot} was clogged.` and `Die Leitung bei Geräten der Charge {lot} war verstopft.`,
   filler `[1, 2]`, reporter `single`, no copies.
3. **New models** `decoy-generic-rise`: `product:HV-81:malfunction_unspecified`, codes-only, at the five shifted
   plants (`plant-ashvale` en, `plant-brindlemoor` en, `plant-corrowfield` en, `werk-dornhagen` de,
   `werk-erlenbruch` de), rate 1, start week 30, weeks 6, codes `["ILL-9001"]`, structured product
   `[HV-81, HV-82, HV-83]` at 1.0, narratives `A unit was returned under warranty.` and `Ein Gerät wurde im Rahmen der
   Garantie zurückgegeben.`, filler `[1, 2]`, reporter `pool`, no copies.

**Master-data additions** as in the first scenario, for the decoys' fresh ids: lot `L20078` at `plant-ashvale`,
`plant-brindlemoor`, `werk-erlenbruch` and `plant-fennick`; lot `L20098` at `plant-brindlemoor` and
`werk-erlenbruch`; products `HV-81`, `HV-82` and `HV-83` at the five shifted plants. The hero's lot is a universe lot
and is not added anywhere.

**Background shift** `intake-form`:

| Field | Value |
|---|---|
| Label | `A new complaint-intake form sets the generic problem code on every complaint` |
| Sites | `plant-ashvale`, `plant-brindlemoor`, `plant-corrowfield`, `werk-dornhagen`, `werk-erlenbruch` |
| Start week | the hero's start week minus the pack's `window_weeks` (8): week 22 |
| Weeks | to the last world week: 18 |
| To code | `ILL-9001`, the pack generator's generic code |

Every non-copy record at a listed site in a covered week, background and scenario items alike, gets the code
`[ILL-9001]`; every copy then takes its origin's codes after the shift.

## 4. Realism constraints (checked when the scenario is parsed, B1b)

- **R1** At least one shift; a shift covers every hero site-week and runs to the world's last week.
- **R2** The hero is narrative-only and carries exactly the code the shift writes.
- **R3** The hero's entity is a universe entity in every hero site's master data (the pack generator's own master
  data, not the scenario's additions).
- **R4** The hero has exactly one structured entry for each type the generator fills and the central fields allow,
  at the generator's fill rate, each one universe id that follows the generator's links.
- **R5** At most two hero records per site and week; at least two templates per hero language.
- **R6** Exactly eight distinct robustness seeds other than the main seed.
- **R7** No decoy key is a hero case key (checked when the world is built).
- **R8** A shift starts at the hero's start week minus the pack's `window_weeks`.

## 5. The rule (CODES_MISS_RULE, verbatim)

> Channels R (model-free) and S. An alert of a case key in a channel in week w, from the hero's first week on, is attributed to the case unless the same scenario without the hero item alerts the same key in the same channel in some week from the hero's first week to w. X's week is X's first alert of the hero key from the hero's first week on, or the last detection week when X never alerts it. attributed_no_later: the channel has an attributed alert no later than X's week. strict_no_later: the channel alerts a case key from the first shift week to X's week, or holds a case key as a cooling candidate in X's week. X caught: X alerts the hero key from the hero's first week on, and the same scenario without the hero item has no X alert of the hero key from the hero's first week to X's week; an X alert that fails this is a chance find. holds_main: X caught, the gate says supported, and neither channel has attributed_no_later. A robustness seed or a grid cell holds by the same rule without the gate, against its own world without the hero item; a world that does not build does not hold. holds_robust: at least half of the robustness seeds hold. holds: holds_main and holds_robust. holds_strict: holds, and neither channel has strict_no_later.

**Robustness.** The eight robustness seeds above, each with the hero and without it (detection only, no site checks).

**Grid (CODES_MISS_GRID).** The shift's start moved to the hero's start week plus an offset in
{0, −window_weeks//2, −window_weeks} = {0, −4, −8}, crossed with a hero rate of 1 or 2 records per week at every hero
site: six cells, each run with the hero and once per offset without it (detection only, no site checks), iterated
sorted by (offset, rate). The grid is reported; it does not enter `holds`.

## 6. Attempts

- **An attempt** is a distinct scenario digest on which any channel's alerts are computed by any means (a recording,
  a sweep, a scratch script, a preview). There are no previews outside logged attempts.
- **Not attempts:** re-runs of a logged digest (also after a code fix), reviewer runs on the committed digest,
  committed in-test copies that break one constraint, and runs that only build the world (no alerts). A world that
  does not build is a design error, recorded as a pre-attempt fix.
- **Cap: three attempts.** Each is committed under `docs/collective/b1/attempts/attempt-<n>/` with the scenario bytes
  recorded and the first scorecard of that digest, and listed in `INTEGRATION.md` with its digest, what changed and
  why (citing the previous scorecard) and its outcome read from its own scorecard.
- **Attempt-fatal checks**, on the main seed: `codes_miss.x.caught` is false, or the gate status is not
  `supported`. A new attempt is allowed only after an attempt-fatal failure or `holds == false`.
- **Adjustable between attempts**, and only within R1 to R8: the hero lot, the hero sites and their languages, the
  hero rate, the hero start week (the shift follows by R8) and the hero narrative templates. Everything else is fixed
  here: the seeds, the weeks, the org, the shift rule, the grid, the rule, the sibling plant and the decoy list.
- **No constraint is ever relaxed.** If no attempt passes the attempt-fatal checks, nothing of the codes-miss scenario
  is committed and the orchestrator is told. The committed scenario is the last attempt, byte-identical.
- Whatever the last attempt shows is what the screen, the scorecard, `demo/collective/README.md` and `SCRIPT.md`
  say: if R or S resolve the case, they say this run does not illustrate the case codes miss.
