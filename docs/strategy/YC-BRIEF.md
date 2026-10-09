# YC brief: what to show, what to offer, and what the evidence says today

Date: 2026-10-09. For the founder. Every number has its source in this repository. It sells nothing the evidence does
not carry: no customer or partner exists yet, and no real-data test has found an early warning beyond chance.

## 1. The product, in one paragraph

Mycelic finds problems that show up across a company's units before any single unit can see them, without pooling
the units' raw records. Each unit (a plant, a subsidiary, a country) reads its own records with a small model inside
its own boundary. Only k-suppressed weekly counts leave. A central detector flags patterns rising at several units.
HQ then asks the units narrow questions, and each answers from its own records with a verdict, never the text. A
supported conclusion can open follow-up work, such as a CAPA draft, which a named owner approves. Every conclusion
keeps its lineage back to the verdicts it rests on, in a signed event log (the fabric). The first product is the
**signal audit**: one export and a list of past issues, run inside the customer's walls, answering whether
cross-unit detection would have flagged those issues earlier than chance (`docs/collective/PILOT.md`).

## 2. What runs today

| Part | State | Where |
|---|---|---|
| Fabric: signed event log, lineage, verification, retention, HTTP API, SDK, MCP | Built and tested; deploys with compose and Kubernetes | `mycelic/`, `docs/DEPLOYMENT.md` |
| Site pipeline: in-boundary reading, k-suppressed cells, the Boundary validator | Built and tested | `mycelic/collective/edge/` |
| HQ detectors, pushdown questions, commit gate, follow-up drafts with owner approval | Built and tested | `mycelic/collective/` |
| Packs: the same code in four fields with data-only configuration | Device complaints, insurance claims, IT incidents, vehicle complaints. The IT-incidents pack was added with 0 lines of code changed: 13 data files, loaded 11 minutes after the start (AI agent wall clock, same author as the code; internal, not a buyer claim). The vehicle pack was built by rule from public NHTSA data | `docs/collective/PACKS.md`, `docs/collective/x3/effort.json` |
| Signal audit (the pilot) with a chance baseline and a power check before any replay | Built and tested | `docs/collective/PILOT.md`, `docs/collective/POWER.md` |
| Cloud lab: pre-registered runs of small models on shared CPU runners | Eight runs recorded | `docs/lab/RESULTS.md` |
| Routing layer: per-site NeuralGraph and Tesseract behind the Boundary, routing HQ's questions | Research spike, not in the product path | `docs/collective/ROUTING-SPIKE.md` |

## 3. What the evidence says

| Question | Result | Source |
|---|---|---|
| Does raw text stay inside a unit? | Yes, in the canary scan with a small model reading every record: 0 of 4,259 planted markers crossed; the positive control was hit 884 times. Text only: the counts themselves can reveal whether a known complaint is at a site | RESULTS run 8; `docs/collective/LEAKAGE.md` |
| Do small models read real complaints better than a word list? | No. On 150 public vehicle complaints, predicate F1 was 0.03, 0.18 and 0.22 for the three models, against 0.43 for word matching | RESULTS run 7; `CHOICE-R002.md` |
| On synthetic worlds built to reward reading, do they help? | Partly: the two larger models found 9 and 10 of 20 planted patterns, the codes alone 5, word matching 19 | RESULTS run 5 |
| On public data, does cross-unit counting warn earlier than chance? | No, in five replays over two fields. With the baseline corrected for complaints that react to recalls, the finds sit at chance (recalls 14 against 16.4 expected; investigations 6 against 5.6) | `docs/collective/replay/vehicles/DIAG-E002.md`; `docs/strategy/MARKET.md` 5.1 |
| Could the detectors have seen a signal of that size at all? | No. At the replays' settings they found 0 of 30 planted ramps. A pooled national channel caught 36 of 60 planted sextuplings, against 8 to 10 for state-split channels | `docs/collective/POWER.md` |
| Does routing questions to the units that hold evidence beat asking at random? | On synthetic worlds, yes by a wide margin (22 more planted patterns found per world, mean over 20 seeds), but almost all of it comes from the counts HQ already has. The run failed its pre-registered false-conclusion bar | `docs/collective/ROUTING-SPIKE.md` |

What this means for the pitch:
- The **privacy architecture** is demonstrated: text stays in, counts and verdicts go out, and every conclusion is
  traceable.
- **Detection value is unproven**, and public data cannot prove it: it is too thin for these detectors. Only a
  company's own records can answer the question. That is why the first product is the audit.
- **Small models are the privacy and cost choice, not yet the accuracy choice.** On public complaint text they read
  worse than a word list. What they must earn on a partner's own text is a measured question, not a claim.

## 4. Small models: what is measured

- **Latency per extraction call** on shared 4-vCPU runners: a-4b a median of 6.5 s on synthetic records (AMD EPYC
  9V74) and 25.8 s on real complaints (Intel Xeon Platinum 8573C); a-0p5b 10.9 to 12.5 s on real complaints
  (RESULTS runs 7 and 8). This is batch speed: a site reads its day's records overnight, not interactively.
- **Retrieval is fast:** Tesseract answers a query in about 0.24 s over a 419-message conversation
  (`docs/BENCHMARKS.md`), and a site ranked its records for a routed question in a median 0.46 s in the spike's first run, four worlds at once.
- **No GPU and no egress of text:** every number above ran on CPU, with the model file verified by hash.

## 5. The demo

STRATEGY 9.1 bars synthetic-fixture results from the YC screen, and the recorded console run shows such results
(`demo/collective/README.md`). Three options, in order of strength:

1. **The signal audit on a design partner's export, run inside their walls** (best). It needs one partner: an export
   of complaints or nonconformances with their past CAPAs. The output is two answers with a chance baseline and a
   review list. A "no" is cheap for both sides and still a result.
2. **The recorded console run, labelled fictional.** It shows the whole loop on a fictional six-plant company: the
   alert, the narrow question, the verdicts, the gate, the CAPA draft and its approval. It needs the founder to change
   STRATEGY 9.1 or keep it internal (`python demo/collective/collective_demo.py --replay`).
3. **The research record as proof of rigour:** pre-registered tests, negative results kept, every number traced to a
   job log. This is not a product demo, but it answers "how do you know it works" honestly.

## 6. The pilot offer

From `docs/collective/PILOT.md`: the partner exports their records and the issues they acted on. The audit runs on
their machine with Python and the standard library. It reports, per channel, which issues would have been flagged
before they were opened, how much earlier, and what randomly timed alerts would have found. It also lists what was
flagged and never acted on. Before the audit counts as a test, the power check must pass at the partner's volumes
(`docs/collective/POWER.md`).

## 7. Market

From `docs/strategy/MARKET.md`, which carries every source:
- **Beachhead:** cross-site complaint and nonconformance early warning at multi-site device makers. 344 device groups
  run at least three manufacturing or complaint-handling sites in at least two countries. At the assumed price and
  constraint share that is about $31M a year ($10–69M).
- **Expansion:** quality software for device and pharma manufacturing (about $3–4B a year), then automotive and
  industrial field quality, where warranty claims cost $51B a year. Then cross-company supplier networks, where
  pooling is impossible by construction.
- **Not claimed:** a horizontal "company brain" market.

## 8. What is missing before YC, and who owns it

| Item | Owner |
|---|---|
| Discovery calls and the first letter of intent (`docs/strategy/DISCOVERY.md`, `OUTREACH.md`) | Founder |
| One design partner's export for the signal audit | Founder |
| Whether the recorded run may appear on the YC screen (STRATEGY 9.1) | Founder |
| The baseline that replaces the corrected null, the pooled channel's flags, and routing decisions 1 and 2 | Chief scientist (`docs/handoff/HANDOFF-2026-10-09.md`) |
| The next reader test (R003: name pairs, negation, one repeat at temperature 0) | Chief scientist decides; then the lab runs it |
