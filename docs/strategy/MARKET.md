# Mycelic: market, applications and TAM (final for the pilot round)

Date: 2026-10-08. Supersedes the scratch sizing that fed STRATEGY section 8 wherever a figure below is newer. Every
figure carries its source and a tag: **[COUNT]** a count this repository ran on public data (the command and the run
are named), **[ANALYST]** a market-research estimate read from the publisher's summary page (these disagree by about
2x for the same category, so each is quoted as a range), **[CITED]** a figure quoted by a secondary source, and
**[ESTIMATE]** arithmetic of ours with the inputs shown. Nothing here is a measurement of Mycelic's own effect: no
customer, partner or real-data result exists yet (N1, X6 and the Phase-1 audit have not run).

## 1. The answer

**What Mycelic sells, in market terms:** cross-unit pattern discovery and verification for organisations whose units
cannot pool their raw records. Each unit (a plant, a subsidiary, a country) reads its own records with a small
in-boundary model; only k-suppressed weekly counts leave; a central detector flags rising patterns across units; HQ
then asks the units narrow questions, each answered from the unit's own records, and only supported conclusions can
propose follow-up work, which a named owner approves.

**Best application (the beachhead): cross-site complaint and nonconformance early warning at multi-site medical device
and diagnostics makers.** It ranks first on fit, not size: the keys that link records across sites are non-personal
(lot, part, supplier, failure mode); post-market trending is a regulatory duty; the narratives are often in local
languages or under transfer limits; and a late signal costs a recall. It is conditional on two open questions:
whether the codes already carry the signal (N1) and whether the narratives really cannot be centralised (X6). The
first real-data measurement (section 3.5) leans against the first: in FDA's public reports for the ten busiest
product codes, the coded problems are mostly specific, so the pitch leads with verification across sites without
pooling text.

**Best expansion by value pool: field quality across plants, dealers and suppliers in automotive and industrial
manufacturing** (section 4), then **cross-company supplier-quality networks**, where pooling is impossible by
construction and the network thesis lives.

**The TAM we claim** (section 3): the beachhead is small and countable: **344 device groups** run at least three
manufacturing or complaint-handling sites in at least two countries (FDA registrations, counted), which at the
assumed price and constraint share is **about $31M a year ($10–69M)**. The category it expands into, quality software
for device and pharma manufacturing, is **about $3–4B a year** of spend (analyst estimates); the value pools it acts
on are **$2.5–5B a year of device recall cost and $51B a year of automotive warranty claims**. We do not claim a
horizontal "collective layer" TAM.

**The YC sentence:** "We start where pooling is hardest and the cost of a late signal is a recall: 344 multinational
device groups. The same engine, configured per field, runs anywhere many sites share keys but cannot share text,
starting with manufacturing quality, a $3–4B software category sitting on $50B+ a year of warranty and recall cost."
Every number in it is above, with its source; none of it is a result of ours.

## 2. Applications, ranked

Criteria: (a) **structure**: shared, non-personal keys across units plus free-text narratives; (b) **constraint**:
the raw text cannot or will not be pooled centrally; (c) **value pool**: what a late signal costs; (d) **buyer and
cycle**; (e) **evidence today** in this repository.

| Rank | Application | (a) Structure | (b) Constraint | (c) Value pool | (d) Buyer, cycle | (e) Evidence today | Verdict |
|---|---|---|---|---|---|---|---|
| 1 | Medical device and diagnostics: cross-site complaint and NCR early warning | Strong: lot, part, supplier, failure mode | Plausible (local-language narratives, transfer limits); to be confirmed by X6 | Device recalls cost the industry about $2.5–5B a year [CITED: McKinsey 2013 via DOTmed]; 1,059 device recall events in 2024 [CITED: Sparta Systems] | VP Quality or post-market surveillance; 6–12 months to a licence (STRATEGY 8.3) | `device_quality` pack; synthetic only; openFDA replay built, not yet run on real data | **Beachhead, conditional on N1 and X6** |
| 2 | Pharma and biologics manufacturing: deviations, out-of-spec results, complaints | Strong: batch, material, supplier, site | Moderate: validated systems; some data must be central for pharmacovigilance | Not sized here | Head of Quality; validation adds months | None beyond the device pack's shape | First expansion |
| 3 | Automotive and industrial field quality (warranty claims, technician notes, plant NCRs) | Strong: part, plant, supplier, VIN-free failure codes | Inside one OEM, weak (warranty analytics is central); across OEMs and suppliers, strong | Global automakers paid **$51B** in warranty claims in 2023 (average claims rate 1.98% of sales); US car and cycle makers **$13.4B** in 2025 [CITED: Warranty Week] | Warranty or field-quality director; long cycles | None | Largest value pool; best entered through suppliers shared by several OEMs |
| 4 | Cross-company supplier-quality networks (OEMs sharing suppliers or contract manufacturers) | Strong | Strongest: pooling across companies is impossible by construction | Shares rank 1's and rank 3's pools | Multi-party; needs a convening party | The fabric's multi-tenant design is not built | **The long-run thesis** |
| 5 | IT incident correlation across subsidiaries or managed-service clients | Strong: config items, services, error signatures | Weak inside one company; moderate for MSPs across clients | AIOps platforms: **$6.7–32.5B** in 2025 by publisher [ANALYST] | IT operations; crowded with incumbents | `it_incidents` pack, data only, 0 code lines (section 5) | Proof of generality, not a wedge |
| 6 | Insurance cross-line fraud | Medium: free-text notes, claim keys | Weak: carriers already pool | Insurance fraud detection: **$6.8–11.5B** in 2025 by publisher [ANALYST] | SIU heads | `claims_integrity` pack, synthetic | Re-wedge option |
| ✗ | Horizontal "company brain" or agent memory | Weak | None | — | Bundled by office suites | — | **Avoid** |
| ✗ | Security telemetry correlation | Strong | Moderate | Large | SOC | Throughput far too low for telemetry | **Avoid** |

## 3. Sizing

### 3.1 The beachhead, bottom-up

**Accounts N.** Device owner/operators with registered establishments in several countries, counted from FDA's
establishment registration and device listing data [COUNT: `tools/market/openfda_establishments.py`, run by the
`market-count` workflow on a GitHub runner; see section 3.4 for the run]:

| Grouping | Sites counted | Owners or firms | ≥2 sites | **≥3 sites in ≥2 countries** | ≥5 in ≥2 | ≥5 in ≥3 | ≥10 in ≥3 |
|---|---|---|---|---|---|---|---|
| FDA owner/operator number | manufacturing or complaint-handling | 20,210 | 1,266 | **344** | 165 | 139 | 53 |
| FDA owner/operator number | every establishment type | 22,031 | 1,407 | 389 | 191 | 157 | 65 |
| Normalised firm name | manufacturing or complaint-handling | 19,831 | 1,425 | **382** | 174 | 146 | 62 |
| Normalised firm name | every establishment type | 21,586 | 1,598 | 433 | 204 | 168 | 70 |

The data hold 25,708 registered establishments in 108 countries (23,376 of them manufacturing or complaint-handling);
485 records without a registration number and 1 without a country were skipped. **N for the beachhead is 344 to
382**: STRATEGY 8.2's definition (at least 3 manufacturing or complaint-handling sites in at least 2 countries), by
owner/operator number and by normalised firm name. The largest groups are the names one expects (Abbott, Stryker,
Philips, Medtronic, GE HealthCare, Boston Scientific, Baxter, BD), but the bands also hold sterilisation service
providers, distributors, contract manufacturers and consumer firms that register device sites (Sotera, STERIS,
Cardinal Health, Medline, Jabil, Procter & Gamble): **not every counted group is a device maker with a complaint
trending problem**, so 344 is an upper bound for that definition, while it is a floor for the world (US-registered
sites only).

**ACV** is unanchored ($100–300k, STRATEGY 8.2): what device makers pay today for trending or post-market surveillance
software was not found, and X6 asks it directly. **Constraint share** (30–60%) is X6's question too.

**Beachhead, recomputed with the counted N** [ESTIMATE]:

| Case | N | × constraint share | × ACV | = per year |
|---|---|---|---|---|
| Low | 344 | 30% | $100k | **$10.3M** |
| Mid | 344 | 45% | $200k | **$31.0M** |
| High | 382 | 60% | $300k | **$68.8M** |

So the beachhead is **about $31M a year, range $10–69M**, below STRATEGY 8.2's $45M midpoint ($9–144M), because the
counted N sits at the bottom of the 300–800 it assumed. The 53 to 62 groups with at least 10 sites in at least 3
countries are the natural first buyers: at $300k each they are $16–19M of it. **The beachhead is not venture-scale on
its own**; it is the proof point.

### 3.2 The category it sells into, top-down

| Category (2025 unless stated) | Estimate | Source |
|---|---|---|
| Medical device quality management software | $1.33B–2.1B | [ANALYST] Market Research Intellect; Market Intelo |
| Medical device complaint-handling software | about $1.44B | [ANALYST] WiseGuy Reports |
| Pharma quality management software (2024) | about $1.9B; CAPA the largest solution type at 32.5% | [ANALYST] Market.us |

So the quality-software category in device and pharma manufacturing is **about $3–4B a year** [ESTIMATE: the device
and pharma rows added, each at the low and the high estimate]. Mycelic is not a QMS; it is the cross-site trending and
early-warning layer that sells next to one, so it addresses a slice of that spend, in the multi-site segment.

### 3.3 The value pools

| Pool | Figure | Source |
|---|---|---|
| Device recall cost to industry | $2.5–5B a year ($1.5–3B non-routine cost, $1–2B lost sales) | [CITED] McKinsey (2013) via DOTmed |
| Wider device quality cost (recalls, warning letters, consent decrees) | $7.5–9B a year plus $1–2B lost sales | [CITED] University of Hertfordshire research profile |
| Automotive warranty claims, global OEMs | $51B (2023); claims up 18% in 2024 | [CITED] Warranty Week |
| US product warranty claims, all manufacturers | average claims rate 1.30% of sales (2025) | [CITED] Warranty Week |

These are what late detection costs, not software budgets. The pitch is that a few weeks of earlier, verified
detection on one recall pays for years of licence; that is a hypothesis until a Phase-1 audit measures lift.

### 3.4 How the count was made

- **Command:** `python tools/market/openfda_establishments.py --out market-count.json` (stdlib only; the counting
  is pure and tested offline in `tests/market/`).
- **Where:** the `market-count` workflow, GitHub Actions run
  [37851835003](https://github.com/anovruzov/NeuralGraph/actions/runs/37851835003), job `count` (113566360497), on
  commit `5a15a99`, 2026-10-08 22:11 UTC. The job log prints the full result between `MARKET-COUNT BEGIN/END` markers
  (the first such block in the log is the offline test's fixture, the second the real count); the JSON is the run's
  `market-count` artifact, copied from the log into `docs/strategy/data/market-count.json`.
- **Input:** openFDA's bulk `device/registrationlisting` files, both partitions (`download.open.fda.gov`), as published
  on the day of the run.
- **Rules:** an establishment is one registration number; its types are the union over its records; a site counts as
  manufacturing or complaint-handling when a type contains "manufactur" or "complaint"; the firm-name grouping lower-
  cases the name and removes punctuation and legal-form suffixes, which merges some owner/operator numbers of one
  group and can wrongly merge unrelated firms of the same name.

### 3.5 What real device complaint reports look like (a real-data measurement)

The first measurement on real data in this repository describes the reports, not the product
[COUNT: `tools/market/openfda_coverage.py`, run [37853960251](https://github.com/anovruzov/NeuralGraph/actions/runs/37853960251),
job 113573533112, 2026-10-08 22:31 UTC; the output, copied from the log, is `docs/strategy/data/field-coverage.json`]. FDA received **2,627,151** device adverse-event reports in 2024 (openFDA
MAUDE, `date_received`). For the ten product codes with the most of them, on a sample of 200 reports each (the first
100 the API returns and 100 from the middle of the result set: not a random sample):

| Product code | Reports, 2024 | Most frequent coded problem in the sample | Real lot number | Narrative text | Coded problems all generic |
|---|---|---|---|---|---|
| DZE | 697,102 | Failure to Osseointegrate | 85.5% | 100% | 15.0% |
| QBJ | 347,117 | Wireless Communication Problem | 74.0% | 100% | 0.5% |
| QFG | 273,190 | Pumping Stopped | 11.5% | 100% | 6.0% |
| OZP | 129,592 | Power Problem | 90.0% | 100% | 8.5% |
| BZD | 68,264 | Degraded | 0.0% | 100% | 1.0% |
| FRN | 57,731 | Corroded | 5.0% | 100% | 0.5% |
| FPA | 47,209 | Loss of or Failure to Bond | 62.0% | 100% | 0.5% |
| QLG | 35,116 | Incorrect, Inadequate or Imprecise Result or Readings | 1.0% | 100% | 2.0% |
| LGW | 35,077 | Adverse Event Without Identified Device or Use Problem | 68.0% | 100% | 18.5% |
| FTR | 30,284 | Material Rupture | 88.0% | 100% | 19.5% |

"Real lot number" excludes placeholders (UNK, NI, N/A and similar); "all generic" is the share of coded reports whose
every coded device problem says no specific problem was identified or no code applies (the list is in the run's
output). What it means for the strategy:

- **Narratives are always there**, so a site-side reader has material in every report.
- **Lot numbers split the field in two**: six codes carry a real lot in 62–90% of reports, four in at most 11.5%.
  Where lots are filled, a central view over the allowed fields sees lot clusters directly, which is what defeated
  the constructed codes-miss case (B1b); where they are not, only the narrative or the site's own records can link
  reports to a lot.
- **The coded problems are mostly specific** (0.5–19.5% generic-only). In FDA's public data the premise that "the
  codes say only that a device malfunctioned" is the exception, not the rule. These are the codes manufacturers file
  with FDA; whether internal complaint codes are coarser is what N1 and the Phase-1 audit must measure on a partner's
  own records. **Until then, lead the pitch with verification across sites without pooling text, not with discovery
  the codes miss.**

### 3.6 Can one manufacturer's reports be split into sites? (for the public replay)

The public replay (STRATEGY 9.3) runs inside one manufacturer and needs a field that splits its reports into "sites".
Each report names the plant or legal entity that made the device (`device.manufacturer_d_name`) and its country
[COUNT: `tools/market/openfda_sites.py`, run [37854721760](https://github.com/anovruzov/NeuralGraph/actions/runs/37854721760),
job 113576035422, 2026-10-08 22:39 UTC; the output, copied from the log, is `docs/strategy/data/site-split.json`].
Reports received in 2024; a group is every report whose manufacturer name contains the word, sorted by how even the
split by name is:

| Group (word in the name) | Reports | Names with ≥100 reports | Largest name's share | Country values with ≥100 reports | Largest names (reports) |
|---|---|---|---|---|---|
| STRYKER | 10,455 | 10 | 23.5% | 5 | Instruments 2,453; Orthopaedics-Mahwah 1,783; GmbH 1,652; Medical-Kalamazoo 1,428 |
| BECTON | 14,258 | 15 | 24.9% | 6 | "BECTON DICKINSON" 3,550; "BECTON, DICKINSON AND COMPANY (BD)" 2,121; Infusion Therapy Systems 1,545 |
| ABBOTT | 90,153 | 16 | 43.7% | 5 | Diabetes Care Inc 39,381; Medical 13,679; Diabetes Care Ltd 11,512 |
| PHILIPS | 14,845 | 8 | 50.0% | 5 | Medical Systems Nederland 7,416; North America 2,539; Goldway (Shenzhen) 2,036 |
| MEDTRONIC | 274,186 | 23 | 63.2% | 9 | Puerto Rico Operations 173,163; MiniMed 48,518; Singapore Operations 9,574 |
| GE | 2,745 | 2 | 70.2% | 2 | Medical Systems (China) Wuxi 1,927; Healthcare Austria 691 |
| BAXTER | 14,421 | 2 | 71.2% | 2 | Healthcare Corporation 10,268; International 3,868 |
| BOSTON | 64,739 | 4 | 87.0% | 3 | Scientific Corporation 56,327; Scientific Neuromodulation 5,875 |

Names are counted among the 100 largest the API returns; a report with devices of two names counts under both; the
country values include blank and `*`, as the reports carry them. What it means for the replay (facts for the
founder's choice, not the choice):

- **The name is a usable site field for some groups.** Stryker's names are mostly plants or divisions
  (Orthopaedics-Mahwah, Medical-Kalamazoo, Endoscopy-San Jose) and none holds more than a quarter of the reports.
  Becton's split is as even, but several of its largest names are spellings of one company; the replay matches names
  exactly and merges nothing, so each spelling would be its own site.
- **One name dominates elsewhere**: 87.0% for Boston Scientific, 71.2% for Baxter, 63.2% for Medtronic (its Puerto
  Rico operations). A replay there is mostly one site.
- **The country is a weaker field**: blank on 14,088 of Baxter's 14,421 reports; for Medtronic, blank on 55,519 and
  `*` on 96,297. A blank goes to the replay's `unpartitioned` site; `*` would become a site of its own.
- **The prereg must list every name to include**, since the manufacturer field is matched exactly.
- A "site" here is the plant named on the report, not a site holding its own records; the replay's label already
  says so ("public data, artificial partitioning, not a confidentiality demonstration").

## 4. Why the expansion path is automotive and industrial, then networks

Device makers give the cleanest first proof (regulated trending, non-personal keys, a buyer with a budget). The value
pool is bigger elsewhere: automotive warranty alone is an order of magnitude larger than device recalls (section 3.3).
Inside one automaker, central warranty analytics already exists, so the constraint that makes Mycelic necessary is
weak there. It is strong across companies: a supplier's part failing at several OEMs is visible to no single one of
them. The route is therefore device makers first (one company, many sites), then the same mechanism across the
companies that share suppliers or contract manufacturers, starting where a convening party exists (an industry
consortium, a contract manufacturer serving several brands, or an insurer of several makers). The second half of that
thesis, a network effect, is a hypothesis.

## 5. Other fields: what the repository shows, and what it does not

A field is a **pack**: configuration files for the entity types, id formats, codes, vocabulary, detectors, egress
rules, questions and follow-ups; the code is generic. Three packs exist: `device_quality`, `claims_integrity` and
`it_incidents`. The third was added as data only and measured [COUNT: `docs/collective/x3/effort.json`]:

- **0 lines of code changed**; 13 pack files, 1,866 lines (966 changed against the device template);
- of 27 written requirements for the field, **11 expressible, 12 approximated, 4 not expressible** in the current
  pack format;
- built by the same author as the generic code, in about 15 minutes from start to a loading pack (AI agent wall
  clock, not engineer-hours).

So the mechanism carries to a new field without code changes, with honest gaps. It does not show that any field has
the signal: each field needs its own N1-style audit, on real data, before anyone claims it.

## 6. What changes this document

| Open question | How it is answered | What changes |
|---|---|---|
| Do narratives carry signal the codes miss? | N1 on real openFDA narratives (the lab's openFDA units) | If not, rank 1 falls and the pitch becomes verification, not discovery. The pre-registered constructed illustration of such a case failed on all three attempts (`docs/collective/b1/attempts/`): with structured fields filled at realistic rates, R or S caught the case about as early as X |
| Is the constraint real? | X6: 20 discovery calls (questions and decision rules: `DISCOVERY.md`) | The constraint share, and so the beachhead size |
| What do device makers pay? | X6 asks for current trending and PMS spend and the cost of the last field action | ACV |
| Does the cross-site view find cases earlier than one site alone? | The Phase-1 signal audit on a partner's data | Whether there is a product |

## Sources

- Medical device QMS software: [Market Intelo](https://marketintelo.com/report/medical-device-quality-management-software-market), [Market Research Intellect](https://www.marketresearchintellect.com/product/medical-device-qms-software-market/)
- Complaint-handling software: [WiseGuy Reports](https://www.wiseguyreports.com/reports/medical-device-complaint-handling-software-market)
- Pharma QMS software: [Market.us](https://market.us/press-release/pharmaceutical-quality-management-software-market/)
- Device recall cost: [DOTmed (McKinsey 2013)](https://www.dotmed.com/news/story/55696?p_begin=1), [University of Hertfordshire](https://researchprofiles.herts.ac.uk/en/publications/fda-warning-letters-consequences-and-costs-to-the-us-medical-devi/), [Sparta Systems](https://www.spartasystems.com/resources/the-rising-cost-of-product-recalls-why-prevention-matters/)
- Warranty: [Warranty Week, April 2026](https://www.warrantyweek.com/archive/ww20260402.html), [Warranty Week, October 2025](https://www.warrantyweek.com/archive/ww20251030.html)
- AIOps: [GMI Insights](https://www.gminsights.com/industry-analysis/aiops-market), [IMARC](https://www.imarcgroup.com/global-aiops-market)
- Insurance fraud detection: [IMARC](https://www.imarcgroup.com/insurance-fraud-Detection-market), [Research and Markets](https://www.researchandmarkets.com/reports/6188521/insurance-fraud-detection-market-outlook)
- Establishment counts: FDA establishment registration and device listing, through [openFDA](https://open.fda.gov/apis/device/registrationlisting/)
