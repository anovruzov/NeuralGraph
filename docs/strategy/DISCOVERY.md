# Discovery calls (X6): who to call, what to ask, what the answers decide

Twenty calls replace three guesses in the market sizing (`MARKET.md` section 3.1): the **constraint share** (30–60%
assumed), the **ACV** ($100–300k assumed) and the **cost of a late signal**. This kit fixes the questions and the
decision rules before the first call, so the answers cannot be read to fit. No call has been made yet.

## 1. Who to call

**First list: the 53 owners with at least 10 manufacturing or complaint-handling sites in at least 3 countries**
(FDA owner/operator number; [COUNT: `docs/strategy/data/market-count.json`, view
`owner/manufacturing_or_complaint`, run 37851835003]). The 25 largest, as the data name them:

| Owner (as registered) | Sites | Countries |
|---|---|---|
| ABBOTT LABORATORIES | 62 | 14 |
| STRYKER CORP. | 58 | 13 |
| Philips Medical Systems International BV | 52 | 8 |
| Medtronic, Inc. | 51 | 13 |
| GE HealthCare Technologies Inc. | 50 | 14 |
| Boston Scientific Corporation | 37 | 8 |
| BAXTER HEALTHCARE CORPORATION | 32 | 14 |
| Becton Dickinson and Company | 30 | 11 |
| DENTSPLY SIRONA INC | 29 | 12 |
| Cardinal Health 200, LLC * | 29 | 10 |
| Essilor of America, Inc | 26 | 13 |
| Jabil Inc. * | 25 | 8 |
| Hoya Optical Labs of America, Inc. | 24 | 8 |
| Medline Industries, LP * | 23 | 5 |
| Alcon Laboratories, Inc. | 21 | 8 |
| BECKMAN COULTER, INC. | 21 | 8 |
| Solventum US LLC | 20 | 8 |
| Covidien llc | 20 | 8 |
| C. R. Bard, Inc. | 18 | 5 |
| OLYMPUS CORPORATION | 17 | 7 |
| SYBRON DENTAL SPECIALTIES, INC. | 17 | 6 |
| Procter & Gamble * | 17 | 6 |
| INSTITUT STRAUMANN AG | 16 | 7 |
| BIOMERIEUX SA | 16 | 4 |
| Vantive US Healthcare LLC | 15 | 9 |

\* Among the groups `MARKET.md` 3.1 flags as distributors, contract manufacturers or consumer firms: check that the
group has a complaint-trending function before calling. Owner numbers split some companies (Covidien and C. R. Bard
register apart from their parents), so count one company once.

**Mix:** at least 10 of the 20 calls from this list; up to 10 from the wider band (at least 3 sites in at least 2
countries: 344 owners), to learn whether smaller groups have the problem at all.

**Roles:** the VP or director of quality; the head of post-market surveillance or complaint handling; the head of
quality systems. One account counts once however many people join.

## 2. The call (30 minutes): ask about the last time, not about the idea

The product is shown only at question 7, so answers 1–6 are not shaped by it. Ask for the last real case, not for
opinions or forecasts ("would you use...").

1. "Tell me about the last time a complaint trend turned out to involve more than one site. Who saw it first, and
   how long after the first complaint?" *(whether cross-site cases happen, and the lag)*
2. "Where do complaint narratives and investigation records live? Can a central team read every site's text today?
   If not, what stops it?" *(the constraint; record which reason: privacy law, works council, customer contract,
   separate systems, language, or none)*
3. "How is trending across sites done today: which tool, which team, how often? What does it cost a year, licences
   and people?" *(current spend: the ACV anchor)*
4. "Your last field action or recall: what did it cost, all in? Looking back, when did the first signal sit in your
   data?" *(the value of an earlier signal)*
5. "When a complaint is coded, how often does the code alone tell you the failure mode?" *(whether internal codes are
   coarser than the public ones, which are mostly specific: `MARKET.md` 3.5)*
6. "When HQ needs a fact from a site (a lot's complaint count, whether a failure was seen), how is it asked and
   answered, and how long does that take?" *(the verification loop, the demo beat that is ready today)*
7. Show the 60-second cut, then: "Would you run a 4–6 week audit on two years of history from three sites, on the
   terms of the pilot offer?" *(pilot conversion)*

## 3. The sheet: one row per call

Record what was said, not impressions; a number not given stays blank and is never estimated. Keep the sheet outside
this repository (it holds people's names and companies' figures); the repository gets only the counts and the
decisions in section 4.

| Date | Company | Role | Cross-site case in 2 years | Text cannot be pooled | Reason | Trending spend per year | Last field action cost | Code gives failure mode | Audit | Next step |
|---|---|---|---|---|---|---|---|---|---|---|
| | | | yes / no | yes / no | | $ | $ | often / sometimes / rarely | yes / maybe / no | |

## 4. Decision rules, fixed before the first call

Read after 10 calls (interim) and after 20 (final). Each rule names what it changes.

| Measure | Rule | What changes |
|---|---|---|
| Constraint share = calls where text cannot be pooled ÷ calls | Below 30%: the low case of `MARKET.md` 3.1 is too high; 30–45%: the low-to-mid range holds; above 45%: the mid case holds | The beachhead is recomputed with the measured share; below 30%, the pitch leads with verification and speed, not "text cannot move" |
| ACV anchor = median stated trending spend per year | Used only when at least 5 calls give a number | Replaces the $100–300k range in `MARKET.md` 3.1 |
| Cross-site cases | Fewer than 5 of 20 recall one in two years | Early warning is a rare need: verification leads the pitch |
| Audit | At least 2 "yes" | The Phase-1 audit is scheduled; at 0, the offer is revised, not the questions |
| Codes | Most say the code rarely gives the failure mode | N1 on a partner's internal records is the next experiment; if most say "often", discovery from narratives falls behind verification |

These thresholds were written before any call. The founder may change them before the first call, recording the change
and its date in this file; after the first call they stand.

## 5. What not to do

- Do not describe the product before question 7.
- Do not repeat one company's answers to another.
- Do not quote a call's figures outside the sheet without that company's permission; the repository and the YC
  application carry only aggregates ("7 of 20").
