# Job Application Campaign — Ali Novruzov

Generated 2026-09-06. Source of truth: `ali_s_novruz_resume.pdf`.

## Bottom line

**0 applications were submitted, and none could be.** This session runs behind an egress
allowlist that permits only Anthropic APIs and package registries. Everything else —
Greenhouse, Lever, Ashby, Indeed, company career pages, and even Wikipedia — returns
`403 connect_rejected` at the proxy. Verified this session:

| Channel | Result |
|---|---|
| `curl` to `boards-api.greenhouse.io`, `api.lever.co`, `api.ashbyhq.com` | `403 connect_rejected` |
| `curl https://example.com` | `403 connect_rejected` |
| `WebFetch` on any domain (Greenhouse, Indeed, Wikipedia tested) | `EGRESS_BLOCKED` |
| Chromium + Playwright (installed at `/opt/pw-browsers`) | present, but no network path |
| `WebSearch` | **works** — this is the only web channel |

`WebSearch` returns titles, URLs and snippets. It cannot open a page, and it cannot fill or
POST a form. So `SEARCH → FILTER → SCORE → LOG` ran; `VERIFY` ran only as far as snippets
allow; `APPLY` could not run at all.

Two further blockers that would stop unattended submission **even on an open network**:

1. **Required fields the resume does not support.** Work authorization, sponsorship,
   citizenship, clearance eligibility, salary expectation, and relocation appear on nearly
   every application. The resume states none of them, and the mission instructions say to
   stop and ask. See the questions at the end of this file.
2. **509 is not an achievable count of *qualifying* roles.** The filter is: posted ≤7 days,
   remote, US-eligible, AI/ML-relevant, and compatible with a May 2027 graduation. That pool
   is realistically tens of roles per week, not 509. Hitting 509 would require abandoning
   the filters — which inverts the stated primary objective.

## What is in this directory

| File | Contents |
|---|---|
| `candidate_profile.json` | Structured profile extracted from the PDF, with an explicit `NOT_ON_RESUME_never_assert` list |
| `ledger.csv` | The application ledger, 24 rows, full schema as specified |
| `answer_bank.md` | Reusable truthful answers, tech claim/don't-claim list, resume-version map |
| `strategy_and_targets.md` | The eligibility finding, ranked target boards, working search patterns |
| `build_ledger.py` | Regenerates `ledger.csv`; scoring sub-scores are inline and auditable |

## The most important thing I found

**Ali graduates May 2027, which makes him ineligible for most Summer 2027 internships.**
Those programs generally require enrollment to continue *after* the internship ends — Intuit
requires graduation after the cohort's Aug/Sep 2027 end date; Google requires the applicant
to be in their penultimate year. A May 2027 graduate fails both.

An agent that had blindly pursued the brief's "internship" target would have burned most of
its applications on roles Ali cannot hold. The correct targets are **New Grad 2027**,
**Fall 2026 / Spring 2027 internships**, and **contract AI research work**. Details and
company-by-company targets in `strategy_and_targets.md`.

## Final report

| Metric | Count |
|---|---|
| Requisitions/programs discovered and recorded | 24 |
| Companies/boards identified as targets | ~50 across 4 tiers |
| Scored | 16 |
| Scored ≥65 (auto-apply threshold) | 13 |
| **Submitted** | **0 — mechanically impossible** |
| Skipped with a recorded reason | 8 |
| Duplicates detected | 0 |
| Blocked from submission | 24 (all — egress) |
| Requiring Ali's input before submission | 3 rows + 8 global fields |

### Top opportunities by fit score

| Score | Company | Role | Why |
|---|---|---|---|
| 91 | Arize AI | AI Product Engineer, New Grad | LLM observability/eval *is* the product; his Mercor eval + rubric work is the domain |
| 90 | OpenAI | Residency / Emerging Talent | Explicitly recruits math-adjacent backgrounds and 0–3 yrs; his adversarial-math-vs-LLM work is on point |
| 88 | Cohere | Applied AI Engineer, Agents & Automations | Agents lane; lead with the Mycelic patent and the Fujitsu agent |
| 85 | Scale AI | Software Engineer, New Grad | Grad window explicitly "Fall 2026 or Spring 2027" → **eligible**; eval/post-training is their core business |
| 80 | Handshake AI | AI Fellowship | Perfect work match but states PhD/Masters — **ask, don't misclaim** |
| 76 | Neuromorphic Labs | Founding Forward Deployed AI Engineer | Fujitsu adoption/enablement work is real forward-deployed evidence |
| 72 | Clera | Founding AI Engineer | Build-from-scratch signal from Mycelic |
| 69 | Twilio | Machine Learning Engineer | Remote-US and TX is an eligible state |

### Recurring reasons for skipping

1. **Graduation-window ineligibility** (Google, Intuit, and most Summer 2027 programs) — the dominant reason.
2. **Not actually remote** (NewsBreak: Bellevue + Mountain View; PDT Partners: NYC quant).
3. **Stale posting, fails the 7-day filter** (TechNovaTime Apr 2026; hapiy.ai ~3 months) — all from aggregators.
4. **Seniority mismatch beyond a reasonable stretch** (Rula Sr. Staff).
5. **Credential Ali does not hold** (Handshake AI PhD/Masters requirement).
6. **Citizenship/clearance required and unknown** (Anduril and defense generally).

### Needs follow-up

- **Handshake AI** — email asking whether undergraduates with frontier-eval experience qualify.
- **Rula** — no junior req today, but they have an applied-AI function; watch the board.
- **Samsung Research America** — two internship reqs found, titles unreadable; open and score.
- **C3 AI Ascend** — board index only; score individual reqs.
- **Mercor** — he is already inside. Ask about expanded or engineering-track scope. Cheapest possible win.

### Recruiter contact information

None was legitimately available. No job page could be opened, so no posted recruiter contact
was observed. Nothing was inferred or guessed.

## How to finish this campaign

The blocker is environmental, not analytical. On a machine with normal network access:

1. Answer the 8 questions below once; drop them into `answer_bank.md`.
2. Run the ATS JSON endpoints in `strategy_and_targets.md` over the Tier 1–3 slugs and filter
   to `publishedAt >= today - 7d`. This is the only reliable way to enforce the 7-day rule.
3. Score with the sub-score scheme in `build_ledger.py`; apply at ≥65.
4. Submit **manually or semi-automated with a human on the final Submit click** — the QC gate
   in the brief (correct company, remote eligibility, no fabrication, no duplicate) needs eyes,
   and automated bulk submission trips the anti-bot controls the brief says not to bypass.

## Questions I need answered before any application can be submitted

1. Are you legally authorized to work in the US without sponsorship?
2. Will you now or in the future require visa sponsorship?
3. Are you a US citizen or permanent resident? (Gates all defense/clearance roles.)
4. Do you hold, or are you eligible for, a security clearance?
5. What is your GPA, and do you want it disclosed when optional?
6. What compensation range should I state when a number is required?
7. Are you willing to relocate, and to where?
8. Are you targeting a **New Grad 2027 full-time start**, a **senior-year internship**, or
   **both**? This determines which half of the target list to work first.

I did not answer any of these on your behalf, and I did not answer any demographic or
self-identification question.
