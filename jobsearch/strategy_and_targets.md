# Campaign Strategy & Target Boards

## The finding that changes the plan

Ali graduates **May 2027**. As of September 2026 he is in his **final** academic year.

Most Summer 2027 internships require the applicant to still be enrolled *after* the
internship ends. Two examples captured from live search results this session:

- **Intuit** — requires an expected graduation date *after* the internship ends; its 2027
  cohorts end Aug 6 and Sep 3, 2027. A May 2027 graduate is ineligible.
- **Google** — requires applicants to be in their **penultimate** academic year, or returning
  to a degree program after the internship. A May 2027 graduate is in their final year.

A naive campaign that fired hundreds of applications at "Summer 2027 internship" postings
would have been **structurally ineligible for most of them**. That is the single most
expensive mistake available here, and it is invisible unless you read the grad-date window.

### What Ali is actually eligible for

| Lane | Why it fits | Timing |
|---|---|---|
| **New Grad 2027** (start summer/fall 2027) | Graduation May 2027 = "Spring 2027", which is inside the standard new-grad window. Scale AI's req explicitly says "Fall 2026 or Spring 2027". | **Open now** and through spring 2027 |
| **Fall 2026 / Spring 2027 internships & co-ops** | Runs during senior year; no post-internship enrollment requirement conflict. Often remote and part-time. | **Open now** |
| **Contract / part-time AI research & evaluation** | Ali already does exactly this at Mercor. Remote by default, hires undergraduates, no grad-window rule. | **Rolling** |
| **Full-time roles with a post-May-2027 start** | Some startups will simply hold a start date. | Rolling |
| ~~Summer 2027 internships~~ | Mostly ineligible — verify the grad window before spending an application. | Check individually |

## Priority target list

Ranked by overlap with Ali's demonstrated evidence, not by brand. Board slugs follow each
ATS's standard URL pattern but **were not reachable from this session — confirm each before
trusting it.**

### Tier 1 — evaluation / post-training (his strongest, most defensible lane)
His two Mercor roles and his rubric/benchmark/error-taxonomy work are directly the product
or the core business at these companies.

| Company | What they do | Board |
|---|---|---|
| Arize AI | LLM observability + evaluation (Phoenix) | `job-boards.greenhouse.io/arizeai` |
| Scale AI | Eval + post-training data at frontier scale | `job-boards.greenhouse.io/scaleai` |
| Surge AI | RLHF / human preference data | company site |
| Snorkel AI | Programmatic training-data development | `job-boards.greenhouse.io/snorkelai` |
| LangSmith / LangChain | Agent tracing + eval harnesses | `jobs.ashbyhq.com/langchain` |
| Braintrust | LLM eval infrastructure | `jobs.ashbyhq.com/braintrust` |
| Patronus AI, Galileo, Humanloop, Freeplay | Eval / guardrail startups | company sites |
| OpenAI (Residency, Emerging Talent) | 0–3 yrs pathway, math-adjacent backgrounds welcome | `openai.com/careers` |
| Anthropic (Fellows, university reqs) | Empirical research incl. evals & benchmarks | `anthropic.com/careers` |

### Tier 2 — agent infrastructure / memory / retrieval (the Mycelic lane)
| Company | Board |
|---|---|
| Cohere | `jobs.ashbyhq.com/cohere` |
| LlamaIndex, Chroma, Weaviate, Qdrant, Zilliz (Milvus) | company sites — **Milvus and Pinecone are on his resume** |
| Pinecone | `job-boards.greenhouse.io/pinecone` |
| Letta (MemGPT), Mem0, Zep | company sites — direct agent-memory overlap |
| Temporal, Inngest | durable execution / event-driven, matches his NATS + event-sourcing work |
| LangChain, CrewAI, E2B, Modal | agent runtime + infra |

### Tier 3 — applied AI at scale-ups (Fujitsu-style delivery evidence)
Twilio, Databricks, Datadog, Grammarly, Notion, Ramp, Vercel, Sierra, Decagon, Harvey,
Abridge, Cresta, Glean, Perplexity, Together AI, Fireworks AI, Baseten, Modal.

### Tier 4 — immediate paid contract work (highest hit rate, zero grad-window friction)
Mercor (already there — ask about expanded scope), Surge AI, Outlier, micro1, Turing,
Invisible, DataAnnotation, Snorkel expert network. **Handshake AI states PhD/Masters
eligibility — do not claim eligibility; email and ask instead.**

## Search patterns that worked

These returned real ATS requisition URLs rather than aggregator spam:

```
"new grad" 2027 machine learning engineer remote greenhouse.io apply
"founding engineer" AI agents remote jobs lever.co ashbyhq 2026
<role keywords> site:job-boards.greenhouse.io
<role keywords> site:jobs.ashbyhq.com
<role keywords> site:jobs.lever.co
```

Prefer `job-boards.greenhouse.io`, `jobs.ashbyhq.com`, `jobs.lever.co` results and skip
`indeed.com`, `ziprecruiter.com`, `glassdoor.com`, `builtin.com` — the aggregators mostly
resurfaced months-old reposts, which is exactly what the 7-day filter is meant to exclude.

## Direct-API shortcut (works from an unrestricted network, not from this session)

Each of these returns JSON with **real posting dates**, which is the only reliable way to
enforce the 7-day filter:

```
https://boards-api.greenhouse.io/v1/boards/<slug>/jobs?content=true   # updated_at
https://api.lever.co/v0/postings/<slug>?mode=json                     # createdAt
https://api.ashbyhq.com/posting-api/job-board/<slug>                  # publishedAt
```

Loop those over the Tier 1–3 slugs, filter `publishedAt >= today-7d`, and the discovery
problem is solved properly in one pass. All four were tested this session and all four
returned `403 connect_rejected` from the egress proxy.
