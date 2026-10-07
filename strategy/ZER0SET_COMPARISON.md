# Zeroset vs Mycelic: architecture and strategy comparison

*2026-10-07.*

**Sources.**
- Every Zeroset fact comes from search-engine summaries. The sandbox proxy blocked zeroset.com, Crunchbase, Dealroom, RuntimeWire and ycombinator.com, so nothing about Zeroset reaches PROVEN.
- **A human must open the primary pages before any Zeroset claim goes into a deck.**
  - Zeroset: [home](https://zeroset.com/), [memo](https://zeroset.com/memo/), [research](https://zeroset.com/research/), [Memory Is Not Retrieval](https://zeroset.com/research/memory-is-not-retrieval/), [Atlas benchmark](https://zeroset.com/research/atlas-benchmark/).
  - Funding coverage: [Dealroom](https://dealroom.co/news/160297-zeroset-raises-5-2m-to-teach-ai-agents-how-companies-actually-work/), [RuntimeWire](https://runtimewire.com/article/zeroset-raises-5-2m-agent-company-workflows), [Crunchbase](https://www.crunchbase.com/organization/zeroset), [2048 Ventures](https://2048.vc/companies).
- Mycelic facts come from reading this repository.

## 1. Who Zeroset is

- **"Zeroset / Atlas / Nebula" is one company, not three.** Nebula is the product. Atlas is Zeroset's own benchmark. [INFERRED, high confidence]
- **Zeroset** is an "applied AI lab" in San Francisco. Its product, **Nebula**, is described as "the state layer that gives agents current state and workflow history" and as turning "every workflow into a typed, queryable trace". [UNVERIFIED: search summaries]
- **Thesis paper, "Memory Is Not Retrieval" (January 2026).**
  - Memory is persistent state, and retrieval is a projection of it.
  - Enterprises "capture what happened but lose why". [UNVERIFIED]
- **Atlas v0 benchmark.** Nebula scores 76.9% overall (82.0% simple, 71.0% complex), compared against Mem0 and Supermemory. The vendor built and scored it; the method and judge are unknown. [UNVERIFIED]
- **Funding:** $5.2M pre-seed, co-led by Gradient and 2048 Ventures, with Leblon Capital. Date, likely 2026, not confirmed. [UNVERIFIED]
- **Team:** co-founders aged 18 (Stanford dropout) and 21 (UT dropout). [UNVERIFIED]
- **Stated focus:** long-running agents in manufacturing, supply-chain operations and financial research. [UNVERIFIED]
- **Narrative lineage:** Foundation Capital's "context graphs / decision traces" thesis, December 2025 ([link](https://ashugarg.substack.com/p/ais-trillion-dollar-opportunity-context)). [MEASURED that the thesis exists; INFERRED that Zeroset rides it]

## 2. Architecture side by side

| Dimension | Zeroset / Nebula (claimed or inferred) | Mycelic (in repo, PROVEN unless noted) |
|---|---|---|
| Core abstraction | Typed workflow traces → entity/fact state over time | Observations → claims (memories) → derived conclusions with lineage |
| Ingestion | Enterprise systems (CRM, ticketing, docs) + agent traces [INFERRED] | **Only Claude Code/MCP, SDK, HTTP API.** No connectors. |
| Storage | Central multi-tenant state store [INFERRED] | SQLite per service + NATS JetStream durable event log; rebuild from stream |
| Temporal model | Facts change over time; smallest context with sources [UNVERIFIED] | Supersession, versioned derived memories, retraction cascades with re-evaluation |
| Provenance | "With sources" [UNVERIFIED] | Full lineage DAG, per-principal redaction, reconstructability and fragility checks |
| Cross-unit reasoning | None evident | Slot-composition rules, `min_units` corroboration across org units, independent-team counting |
| Locality | Central SaaS [INFERRED] | Raw notes stay with the agent; only shared claims travel |
| Models | "Build models" of company operations [UNVERIFIED] | No model in the deployed data path |
| Benchmarks | Atlas (own) | LoCoMo 72.2% (lenient judge, 744 Qs, honest about judge sensitivity); a synthetic enterprise benchmark where centralised beat the hierarchy on recall (78% vs 57%) |
| Distribution | Platform with terms of service (self-serve implied) | MCP plugin inside Claude Code |

**The benchmark numbers do not compare.** 76.9% on Atlas and 72.2% on LoCoMo are different tasks with different judges. Never show them side by side. [PROVEN that they're incomparable]

## 3. Can either copy the other?

| Feature | Zeroset adds it | Mycelic adds it |
|---|---|---|
| Lineage DAG with redaction | Weeks; "with sources" is a start | Has it |
| Corroboration rules across org units | Weeks to a quarter on a central store | Has it |
| Retraction cascades | Weeks | Has it |
| Raw-data-stays-local | Hard. It inverts their central model and roadmap. | Has it |
| SaaS connectors / workflow traces | Has or will have | Months of commodity integration work. Slack now limits non-Marketplace apps to 1 request/min and 15 objects on history endpoints. |
| Learned company models | Roadmap | Needs data and capital Mycelic lacks |
| Investor narrative, capital | Has it | Lacks it |

**Net:**
- In the horizontal agent-state race, Zeroset can copy Mycelic's differentiators faster than Mycelic can copy Zeroset's capital and narrative.
- Locality is the one exception. It is not what horizontal memory buyers pay for, and the founders' own benchmark shows it costs recall. [INFERRED]

## 4. Which layer is more valuable?

- **State layer (Zeroset).** Valuable only until model labs and platforms ship it natively, which is already happening: Anthropic Team/Enterprise memory, OpenAI memory and company knowledge, Microsoft Work IQ, Salesforce agentic memory.
- **Its escape route** is "how the company actually works". That route runs into Glean ($300M+ ARR), Microsoft and OpenAI. [INFERRED]
- **"Discovery above state" (Mycelic's original pitch).** Less crowded but without demonstrated demand. Mycelic's own data says a centralised system wins it whenever centralisation is allowed. [MEASURED, synthetic]
- **Answer:** neither horizontal layer is a good place for a two-person team without capital. The valuable layer is **vertical outcome ownership**, being the system that resolves a specific costly event. For Mycelic that event is a network incident. [Decision]

## 5. Should Mycelic attack Zeroset's category or go above it?

- **Neither.** Go *sideways and down* into a vertical where:
  - the causal schema comes free (network topology);
  - local collectors are mandatory;
  - independence accounting is the job (root vs symptom);
  - the founder has prior experience.
- Zeroset becomes irrelevant to Mycelic's customers. Neither company sells to a regional ISP's NOC.

## 6. If Zeroset raised $50M tomorrow

**What they buy:**
- 30–50 engineers;
- connectors everywhere;
- enterprise sales into manufacturing and supply chain;
- probably "proactive insights" on their trace graph within 12 months.

**What still lets Mycelic win:**
1. **Be in a market they won't enter for years.** That means network operations: devices, optics, routing, multi-vendor CLIs.
2. **Own outcome-labelled data** that a horizontal state layer never sees: root cause confirmed by ticket resolution.
3. **Own the on-prem, read-only collector footprint.**
4. **Publish the network root-cause benchmark** and make others play on it.
5. **Optionally, interoperate.** Expose Mycelic's evidence ledger over MCP so agent-state layers, Nebula included, can consume network incident knowledge. They then become a channel, not a competitor.

## 7. Also in the same lane, and more dangerous than Zeroset for the MCP surface

- **Memory Store (YC S26)**: "one memory for your team's agents", built from meetings, Claude sessions, Slack and Gmail via MCP. $500k. [MEASURED: YC listing summaries]
- **Dust**: a $40M Series B (Sequoia) as the "multiplayer operating system for enterprise AI"; over $20M ARR. [MEASURED]
- **Mem0**: $24M; 41k stars; 186M API calls in Q3 2025. [MEASURED]
- **Anthropic, OpenAI and Microsoft native memory and company knowledge.** [MEASURED]

These are why the universal comms ingestion plus Claude Code memory path, which is the founders' current trajectory, is the wrong first market.

## 8. Brutally honest bottom line

- **Head to head, Zeroset is ahead** in the category the founders were drifting into: narrative, capital, and a benchmark with their name on it.
- **Mycelic is ahead on rigour**: lineage, recovery, corroboration, published failures.
- **Rigour does not win a horizontal memory race.** It can win a vertical where wrong answers are expensive and evidence must be shown.
- Leave the category. Do not mention Zeroset in the pitch. If asked: "They're building agent state for enterprises; we resolve network incidents. Different buyer, different data, different job."
