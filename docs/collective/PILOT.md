# The pilot: a signal audit on your own history

The pilot is the first thing a design partner runs, and it needs nothing from us but this repository. It answers two
questions about your own past, inside your own environment:

1. **Would the cross-site detectors have flagged the issues you later acted on, and how much earlier?** Each answer is
   set against what the same alerts at random times would have found.
2. **What did they flag that you never acted on?** That is the review list: patterns seen at several of your sites that
   match no issue on record. Each one is a missed problem, a known problem never written up, or noise. Only your own
   people can tell which.

It works in any field the packs cover, because "issues you acted on" is a field-neutral idea: CAPAs for a device
maker, problem records for an IT organisation, special investigations for an insurer.

```
python -m mycelic.collective.pilot.audit run --pack <pack id or your pack directory> \
    --records export.csv --outcomes outcomes.csv --out audit/
```

It writes `audit/audit.json` and `audit/audit.md`. Nothing is sent anywhere. The site stores the pipeline builds live in
a temporary directory that is deleted when the run ends. The report names your own record ids so your reviewers can
open them. Python 3.11 and the standard library are all it needs.

## What you export

**Your records.** One row per complaint, incident or claim, as CSV or JSON lines. The pack's `mapping.json` names the
fields: `site` (the plant, company or line of business each row comes from), the record id, the received date, the
codes, the entity fields (products, lots, components, suppliers, services, releases and so on) and the narrative text.

In CSV, the header names are those mapping paths. `patient.name` nests; `lots[]` is a list, written `L1;L2`. If your
export's columns are named differently, copy the pack and change its `mapping.json`. That is the pilot's first week,
and the public replay shows why it matters: until the pack read the manufacturer's real lot and catalog numbers, the
detectors had nothing to count (`docs/collective/replay/CHOICE-002.md`).

**The issues you acted on** (`outcomes.csv`), one per row:

| Column | Meaning |
|---|---|
| `outcome_id` | your id for the issue (a CAPA number, a problem record, an investigation) |
| `opened` | the date it was opened, `YYYY-MM-DD` |
| `entity_type`, `entity_id` | what it was about, in the pack's entity types (a product, a lot, a service, a repair shop) |
| `predicate` | optional: the failure, in the pack's predicates; empty matches any |

`python -m mycelic.collective.pilot.audit demo --pack <pack> --out <dir>` writes both files for a synthetic history,
as a template of their shape.

## What runs

This is the same pipeline the cloud lab and the public replay run (`evaluate.baselines`):
- each site ingests its own rows and extracts claims lexically;
- each site emits k-suppressed weekly cells;
- HQ runs the detectors on those cells. **X** reads the code and text cells and **S** the code cells only. Beside them,
  **model-free R** counts the record-level fields the pack lets leave.

Two comparators run beside them unless you pass `--no-comparators`. Both read model-free R's record-level cells:
- **P, pooled national:** every site's cells summed into one site, then the same detectors. One site can never reach
  the pack's minimum of two sites, so P runs with that minimum at 1. Everything else is the pack's, the alert budget
  and cooldown included.
- **PRR, disproportionality:** each entity and failure against every other entity, over the last 26 weeks (the pack's
  baseline weeks). It signals by Evans' criteria: a proportional reporting ratio of at least 2, a Yates-corrected
  chi-squared of at least 4, and at least 3 records. Signals alert under the same budget and cooldown, strongest
  chi-squared first. The trailing window is the one change from the usual whole-database test: over the whole history,
  a slow rise is diluted and an entity that always had a high share signals once and never again.

Both walk every week of your export from the first, as the detectors do, and their alerts before the first evaluated
week are dropped, as for every channel. PRR needs no history, so a failure that is already disproportionate in your
first weeks alerts there, is dropped, and stays cooling while it keeps signalling. A PRR alert marks when a
disproportion starts, not when the evaluated weeks start.

They answer one question: would pooling every site, or a standard pharmacovigilance test, have seen what the
site-split channels missed? They do not change X, S, model-free R or the review list; a test checks that the audit
gives the same numbers for those with and without them.

An issue counts as **found** by a channel when an alert on its entity (and failure, when given) was available in the
look-back weeks before it was opened (`--lookback-weeks`, default 26). Alerts in the weeks after opening are listed
apart and never counted.

Every found count comes with its **chance**: the circular-shift null of the public replay. It is the expected number
found by the same alerts moved to random times, and the share of those moves that do at least as well (`p`). A found
count means something only as far as it beats that.

Complaints often react to an issue once it is opened. The plain null rotates those reactions into the look-back, so
it expects more finds than chance gives whenever complaints follow issues. Each channel therefore also reports the same
null with every issue's own alerts after its opening left out (`expected_found_excluding_own_post`, its own `p`). On
the public vehicle replays, re-scored this way, the recall null fell from 38.6 to 20.3 expected finds, and the found
count (16) still sat at chance (`docs/handoff/HANDOFF-2026-10-09.md`). Each channel's alert timeline is kept in the
audit file (`alert_timeline`), so the result can be re-scored without re-running:
`python tools/market/rescore_audit.py audit/audit.json` recomputes each channel, and the union of the three, from that
file alone.

**The two nulls on synthetic worlds.** `python tools/market/reactive_world.py` scores synthetic alert timelines with a
known truth through the same scoring: 40 issues, 87 evaluated weeks, 26-week windows, 20 seeds, no detector. Each
world's pass criteria were committed before that world first ran. The fourth world was added after a review found
that the first one's criterion held by construction. Its first run caught a bug (it also drew the unrelated alerts,
against its own description), fixed before the numbers below. Means over the seeds:

| World | Found | Expected, plain null | Expected, own alerts after opening left out | That null's p below 0.05 |
|---|---|---|---|---|
| Complaints react for 12 weeks after the opening; no earlier signal | 0 | 9.52 | 0 | 0 of 20 seeds |
| The same, plus unrelated alerts on the issues' own keys | 8.25 | 15.49 | 6.24 | 0 of 20 |
| The same, plus a real earlier signal for every other issue | 22.45 | 17.66 | 10.89 | 5 of 20 |
| Complaints react for 40 weeks, past the 26-week window; no earlier signal | 0 | 17.47 | 5.94 | 0 of 20 |

Where reactions ended inside the window, chance lay between the two nulls. The plain one counts each issue's own
reactions. The corrected one also leaves out unrelated alerts after the opening. Where reactions lasted 40 weeks, the
corrected null overstated chance too: it expected 5.94 finds where none could be found (4.53 to 7.70 across the
seeds), because it keeps the reactions that come after the window. The corrected null separated the earlier signal on
average, but its p fell below 0.05 in only 5 of 20 seeds. The criterion was 18, so that test fails. It stays as
written, marked as an expected failure. Shift zero always finds the found count, so the p is never below
2/(1 + shifts) (0.023 at 87 weeks). Shifts of up to 12 weeks also found as many, because they keep each earlier
signal inside its own look-back. So the corrected column is not chance itself. It falls short of chance where
unrelated alerts sit on an issue's key, and overstates it where reactions outlast the window. A found count at or
below it gives no sign of a signal. A found count above it is not evidence on its own.

## Before a replay: the power check

A replay that cannot find a real signal of the size the data carry tells us nothing, whatever it finds. The vehicle
replays were run without asking that first. So the rule is now:

**A replay is pre-registered only with a passing power check.** Its choice file names:
- the signal it assumes: a rate in records a week at the end of a two-year ramp, and where that rate comes from;
- the history (years of export before the outcomes) and the look-back the audit will use;
- the background file the check ran on.

It then runs the check with exactly those settings and cites the passing `power.json` by its sha256:

```
python -m mycelic.collective.pilot.power run --pack <pack> --background <background file> --out runs/power/<id> \
    --rates <rate> --years <years> --lookback-weeks <look-back>
```

The check plants ramps and sextuplings in synthetic worlds shaped like the field and audits each world with every
channel. Power is the share of plants with an alert in the look-back before the outcome opens. The command exits 0 only
when the best channel finds at least 80% of the plants, counting only channels that find at most 20% of unplanted
controls. Otherwise it exits 1 and the replay is not pre-registered. It can still run as exploration, labelled as such.
How the check works, and what it found for the vehicle replays, is in `docs/collective/POWER.md`.

On a synthetic vehicle-like background (run 002 there), the check fails at every rate with the replays' 26-week
look-back. At their signal size, 0.1 records a week over two years, X, S and model-free R found none of 30 plants. The
pooled channel found 36 of 60 planted sextuplings, short of the 80% target, against 8 to 10 of 60 for the state-split
channels. With a 104-week look-back, PRR found all 30 ramps at 0.5 and at 2.0 a week in 4-year exports. In 2-year
exports it found 18 and 1 of 30, because the ramp is already there when the export begins.

## What the demos show, and what they do not

The demo plants three known patterns in a generated six-site history of each built-in pack. Each pattern becomes an
issue opened two weeks after the pattern ends, and the demo audits the result. The planted patterns are written in the
narratives only, so the codes-only channels cannot see them by construction.

| Field (pack) | Records | X: issues found, of 3 | Days earlier (median) | Expected by chance | p | X alerts | Review list |
|---|---|---|---|---|---|---|---|
| Medical devices (`device_quality`) | 1,907 | 2 | 22 | 1.94 | 0.73 | 23 | 21 |
| Insurance claims (`claims_integrity`) | 2,095 | 2 | 29 | 1.27 | 0.42 | 17 | 20 |
| IT incidents (`it_incidents`) | 1,936 | 2 | 29 | 1.21 | 0.45 | 24 | 23 |

Seed 1, 52 weeks, look-back 26 weeks, 33 weeks evaluated. S and model-free R found none of the planted issues in any
field.

- **Shown:** the audit runs end to end, unchanged, in three fields. Its inputs are a flat export and a list of issues,
  and it produces both answers above with the record ids behind every alert.
- **Not shown:** that it finds real problems. The plants and the detectors share an author, three issues per field
  are far too few, and no found count here beats chance (p 0.42 to 0.73). With a 26-week look-back and a few alerts a
  week, randomly timed alerts also land before most issues. A real audit needs your history: tens of issues and years
  of records.
- **On real data so far:** public replays in two fields, and none found a recall or investigation earlier than chance.
  - The same pipeline over two years of one device manufacturer's public FDA reports, with eight recalls
    (`docs/lab/RESULTS.md`, run 4).
  - This audit, run unchanged, over two years of six car makes' public NHTSA complaints (63,235, each state a site),
    with 896 recall outcomes. It flagged 14 before the recall, where randomly timed alerts find about 30, and 51 in
    the 26 weeks after. With the regulator's defect investigations as the outcomes instead, it flagged 6 of 126,
    where chance finds 9 (`docs/collective/replay/vehicles/CHOICE-V001.md` to `CHOICE-V003.md`).

  Public reports carry far less than your own complaint, warranty and service files, and states are not your sites.
  That is why the pilot runs on yours, and why its answer may well be no.

## Reading the report

`audit.md` gives, in order:
- the export's coverage: records used, rows rejected and why, and the share with a resolved identifier, a code and a
  narrative;
- the per-channel table, with its chance column;
- each issue's result per channel;
- the review list, with the sites and the first record ids behind each pattern.

If the resolved-identifier share is low, fix the mapping before reading anything else. The detectors count per
identifier, so ids they cannot read are invisible to them.
