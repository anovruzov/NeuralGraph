# Internal investment memo (simulated): Mycelic

*Written in the voice of an a16z infrastructure partner preparing an internal argument. This is a simulation by Mycelic's strategist, not a real a16z document. Its purpose is to show the founders what a credible investment case must contain and what it currently lacks.*

**Stage:** pre-seed/seed (or Speedrun). **Practice:** AI Infrastructure, with an American Dynamism / Machine Age read-across.

## The one-line thesis

Every network, from a rural fiber ISP to a 100,000-GPU training cluster, fails as a cascade of correlated symptoms. The company that counts evidence once, verifies at the device and learns from every resolved incident becomes the root-cause layer for the physical internet and for AI datacenters.

## Why this fits our theses

1. **Agent-native infrastructure, applied to the physical world.**
   - Our Big Ideas 2026 framed the next wave as systems that act and infrastructure built for agents.
   - Separately, we raised a **$1.1B Machine Age Fund** explicitly covering *networks* and *data centers*.
   - Network reliability is the operating layer under both. [Reported, Aug 2026]
2. **"Your data agents need context."**
   - Agents fail without the right context.
   - In networks, the context is the topology and the causal layering (optics → link → IP → routing → service).
   - It is *available*, which makes this one of the few domains where an agent's causal schema doesn't have to be invented.
   - The founders' own research flags schema induction as the hardest unsolved problem in their general approach. Choosing a domain that supplies the schema is the smart move.
3. **AI SRE has proven the budget.**
   - Resolve AI ($1B → $1.5B valuation), Traversal ($48M) and incident.io ($62M) show enterprises pay for autonomous root cause.
   - Networks are the under-served cousin: harder data, multi-vendor, devices that can't run agents. [Reported]

## Why now

- Average network outage find-and-resolve time is about 11 hours, and rising.
- Operators are under autonomy mandates: most at Level 2, targeting Level 4.
- LLMs collapse the cost of multi-vendor normalisation.
- GPU clusters fail every few hours at scale; Meta reported 419 interruptions in 54 days on 16,384 H100s, 8.4% of them network.

## What is genuinely differentiated

- **Independence accounting.** Symptoms of one failure are dependent evidence. Incumbent correlators de-duplicate by rules. Mycelic models *why* evidence is or isn't independent: shared causal ancestor, shared observation channel, copy lineage.
- **Active verification.** It chooses the single read-only device check with the highest expected information gain. This is the downward-questioning mechanism that, in the founders' own simulation, accounted for most of the discovery gain (+0.55).
- **An evidence ledger.** Lineage, retraction and re-evaluation already run in their codebase with crash and broker-loss recovery tests. This is unusually rigorous for pre-seed.
- **Culture.** They published a benchmark in which their own architecture lost to a centralised baseline on recall, and explained why. That is a positive signal for scientific honesty and a negative signal about the horizontal thesis they started with. They are pivoting accordingly.

## Market

- **Wedge:** mid-size multi-vendor operators. Low-to-mid hundreds of millions of dollars globally; small, but fast-buying and data-rich.
- **Expansion:**
  - MSP-run NOCs;
  - enterprise NetOps;
  - tier-2 telcos;
  - **AI-datacenter fabrics**, the prize: hundreds of operators, $0.3–3M ACV, pain priced in idle GPU-hours;
  - a cross-operator early-warning data product.
- **Upper bound:** observability and AIOps (Splunk ~$28B exit, Datadog scale).
- **Realistic 5-year outcome if it works:** $20–60M ARR, with a path to more via AI datacenters.

## Moat (how it compounds)

1. **The labelled-outcome corpus.** Every resolved incident across customers sharpens attribution priors: vendor quirks, optic failure signatures, cascade shapes.
2. **The on-prem collector footprint.** Sticky, and trusted, because it is read-only.
3. **A cross-operator claim network.** Shared failure signatures without sharing raw data. If it forms, there are true increasing returns. *This is the bet that makes it venture scale; it is unproven.*

## Why we might pass (the case against)

- **The founder-market-fit claim is unverified.** The telecom/comparator story must hold up in references.
- **No customers yet.** The codebase is ahead of the market evidence.
- **The wedge is small-ACV.** It could stall below $5M ARR.
- **Competition:**
  - top-down: Selector ($66M+, AT&T Ventures), Nokia, Ericsson, Kentik;
  - AI-datacenter: Clockwork ($41.6M) and NVIDIA's own tooling.
- **Frontier models with tool access may make "good enough" root cause a commodity feature.** Moats then depend on outcome data and footprint, both of which take time.
- **Most of the codebase was authored with AI agents.** That is fine, but we need to see the founders debug a customer incident live.

## What would get us to yes

- [ ] 2+ operators sharing 90 days of history, and 1 paid pilot.
- [ ] A pre-registered real-data result. Top-3 ≥70% on multi-device incidents in under 5 minutes, beating a frontier-LLM-with-all-logs baseline on time and cost at equal or better accuracy.
- [ ] A verified founder reference from the prior telecom work.
- [ ] One AI-datacenter or neocloud conversation that says "we'd pilot this".

## Proposed terms posture

- **Pre-seed now:** a small cheque or Speedrun.
- **Seed:** lead after the first paid pilots and real-data results.
- **Series A trigger:**
  - about $1M ARR;
  - over 120% net retention;
  - one AI-datacenter customer;
  - the first cross-operator detection.
