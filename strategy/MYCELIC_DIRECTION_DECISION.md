# Mycelic: direction decision

*Prepared 2026-10-07. Chief strategist's decision memo. Adversarial by design.*

| | |
|---|---|
| **WE SHOULD BUILD** | An AI root-cause engine for network operations. It deploys as a read-only collector inside the operator's own network. It finds, verifies and explains the cause of an incident across thousands of devices. It runs on the existing Mycelic kernel (lineage, independent support, targeted questioning, retraction), which stays internal and is not the pitch. |
| **WE SHOULD SELL FIRST TO** | The network-operations / NOC lead at mid-size, multi-vendor network operators: regional fiber ISPs, cable operators, rural telcos and co-ops running roughly 1,000–50,000 devices with a 3–30 person NOC. |
| **THE PRODUCT THEY BUY IS** | One incident card, minutes after an alarm storm, posted to their Slack, Teams or ticket queue. It contains: the most probable root cause; every alarm that root explains and every alarm it does not; the read-only checks Mycelic ran on specific devices to verify it; a confidence; and the evidence trail. |
| **THE CORE TECHNICAL MOAT IS** | Independence-aware causal attribution: 3,000 symptoms of one fiber cut count as one witness, not 3,000. It is verified by targeted, read-only questions to the few devices that can tell the hypotheses apart. It is trained by an outcome-labelled incident corpus that grows with every customer. Later, a cross-operator early-warning network shares *claims*, never raw data. |
| **THE DEMO WE BUILD THIS WEEK IS** | A 100+ router emulated multi-vendor network (containerlab, FRR + Nokia SR Linux). We inject a fiber cut, a degrading optic, an MTU mismatch and a coincidental decoy flap, producing thousands of alarms. Mycelic names each root cause with evidence in under a minute. A frontier LLM handed all the same logs and a classic topology-rule correlator run side by side, scored on accuracy, time, tokens and bytes moved. |
| **THE CLAIM WE MUST PROVE IS** | On a design partner's *real* historical incidents, Mycelic puts the correct root cause in its top 3 for at least 70% of multi-device incidents within 5 minutes. It must beat both a frontier LLM given the same logs and the operator's existing alarm correlation. Correctness is scored against the resolution recorded in the ticket. Pre-registered; we publish it if we lose. |
| **THE THING WE MUST STOP DOING IS** | Building horizontal agent-memory and "company brain" infrastructure before one paying customer exists in one vertical. That covers LoCoMo tuning, the chat-memory product, synthetic org-chart benchmarks, and the universal comms-ingestion layer specified in `prompts/UNIVERSAL_COMMS_INGESTION_PROMPT.md`. |

### Probability that each option is the best move for the next 12–24 months

| Option | Probability | One-line reason |
|---|---:|---|
| **C. Network / telecom intelligence (mid-size operators first, kernel kept inside)** | **38%** | Founder fit, fast buyers, measurable ROI. Networks hand you the causal schema the founders' benchmark says is the hardest missing piece. |
| G1. Discovery across a company's coding-agent fleet (the current MCP/Claude Code surface, "multiplayer AI") | 18% | Fastest demo, highest code reuse, matches a YC Fall 2026 request. But crowded (Memory Store YC S26, Dust, Cursor, Anthropic) and easily absorbed. |
| F. Horizontal hive kernel + one narrow app | 14% | Right *architecture*, wrong *pitch* for a pre-revenue team. It becomes correct once C or G1 has customers. |
| G2. AI-datacenter / GPU-fabric root cause first | 14% | Biggest pain per customer and strongest a16z fit. Slower access, no GPUs to demo on, and the founders lack HPC credibility. It is C's expansion market. |
| A. Horizontal Company Discovery OS | 6% | Hostile, crowded market: Glean, Microsoft Work IQ, OpenAI company knowledge, Dust, Memory Store. The founders' own benchmark says centralised beats them on recall. |
| D. Industrial / manufacturing | 5% | Real cross-site pain (warranty, quality), but slow cycles, no domain expertise, and Viaduct-class incumbents. |
| B. Cyber defense hive | 3% | Incumbents already ship "local sensors + central correlation + multi-agent investigation". No security pedigree. |
| E. Agent memory / state platform | 2% | Commoditising: Mem0 has $24M, OpenAI and Anthropic memory are native, a dozen startups compete. NeuralGraph's 72.2% LoCoMo loses the first bake-off slide. |

> **Process note, stated first because it bounds everything below.** The 15 parallel research teams were stopped by the founder mid-run. Only the Zeroset team completed. The founder then asked for this research to be done by one agent alone. The sandbox's egress proxy blocked direct page fetches (ycombinator.com, a16z.com, zeroset.com and others). **Every external fact here was therefore read from search-engine result summaries**, about 60 queries, with links given. Nothing external reaches **PROVEN**. Treat all funding and traction figures as "reported" and spot-check them before they go into a deck. The YC partners, a16z specialists, critics and debate rounds below are **simulations by the strategist, not real people**.

**Evidence tags used throughout:**

- **PROVEN**: verified directly here (code read or run, repo history).
- **MEASURED**: a number reported by a named primary source (company release, paper, the repo's own benchmark).
- **INFERRED**: reasoned from evidence.
- **HYPOTHESIS**: a bet to test.
- **UNVERIFIED**: background knowledge not checked this session, a single weak source, or a founder statement.

---

## 1. Executive verdict

1. **Mycelic should stop presenting itself as infrastructure and become a network-operations company for 12–18 months.**
   - First buyers: mid-size operators whose NOCs drown in alarm storms.
   - What they buy: the hours between "something broke" and "we know why".
   - The hive kernel is how the product works, not what the company sells. **[INFERRED]**
2. **The horizontal story fails on the founders' own evidence.**
   - In their synthetic enterprise benchmark, a centralised chunked-context model finds **78%** of hidden problems at 50,000 users against the hierarchy's **57%**, and **69% vs 31%** on the hardest problems. **[MEASURED, synthetic]**
   - The hierarchy's measured wins are modest: independent-support accuracy 77% vs 62%, decoys accepted 38% vs 47%, top-40 precision 32% vs 21%. Plus one categorical win: 0% of raw notes read centrally. **[MEASURED, synthetic]**
   - The founders' own critique calls the giveaway causal schema "the biggest unresolved weakness". Every system was handed it. **[PROVEN, `docs/MYCELIC_ENTERPRISE.md` §24]**
   - The models in that benchmark are a simulated capability dial, not real models. **[PROVEN, §25 A1]**
   - Conclusion: where data can be centralised, which is nearly every single-company knowledge setting, Mycelic is not the better discovery engine on today's evidence. **[INFERRED]**
3. **Networks are the domain where Mycelic's real strengths become the job itself.**
   - **Alarm storms are an independence problem.** Thousands of alarms are symptoms of one root event. **[MEASURED: an industry write-up reports ISP NOCs with 5,000+ devices see thousands of daily alarms, mostly symptoms of the same root event]**
   - **The causal schema exists already.** Topology and protocol layering (optics → link → IP → routing → service) provide much of it. **[INFERRED]**
   - **Targeted downward questioning is the job.** It is what an engineer does with `show` commands, and the founder reportedly automated exactly this before: a comparator that took troubleshooting from about 48 hours to about 10 minutes. **[UNVERIFIED]**
   - **Local agents are a deployment requirement, not a philosophy.** Operators do not give SaaS vendors inbound access to routers. Collectors live inside the network. **[INFERRED; Kentik/Auvik-style on-prem collectors are standard practice, UNVERIFIED]**
   - **The pain is measured.** Average time to find and resolve a network outage is about **11.2 hours** (Opengear). **[MEASURED]**
4. **This is not a forever-vertical.**
   - Same kernel and same moat, widening outward: regional operators, then MSPs and enterprise NetOps, then tier-2 telcos, then AI-datacenter fabrics, then a cross-operator early-warning network. **[HYPOTHESIS]**
   - The network effect is real only at the last step, and it is the a16z story.
5. **The YC Winter 2027 on-time deadline is 2 November 2026, 26 days from today.**
   - The batch runs January–March in San Francisco. **[MEASURED: reported deadline]**
   - The plan in `NEXT_14_DAYS.md` is built to apply by then with:
     - a real-data demo;
     - 20+ operator conversations;
     - at least two data-sharing design partners.
   - **If day 14 shows fewer than two design partners**, pivot to G1 (coding-agent fleet discovery) and apply with that. It is the fallback because it needs no domain access.

## 2. The one direction we recommend

**Option C, stated precisely:**
- **Mycelic is an AI root-cause engine for networks.**
- **Wedge:** mid-size multi-vendor operators.
- **Shipped as:**
  - a read-only on-prem collector (agents per site or POP);
  - a kernel that correlates, attributes, questions and verifies;
  - output in the tools the NOC already uses.

**Why not F (horizontal kernel + narrow app)?**
- F and C share an architecture. They differ in *identity*.
- A pre-revenue two-person team that says "kernel" gets asked "for whom?", and the honest answer is "network operators". So say that. **[INFERRED]**
- Becoming "the kernel" is earned after the second vertical works, not claimed before the first.

**Why not G1 even though it reuses the most code?**
- The current distribution, an MCP plugin inside Claude Code, sits in the most crowded and most absorbable spot in AI:
  - **Memory Store** (YC Spring 2026) already sells "one memory for your team's agents" built from meetings, Claude sessions and Slack, over MCP. **[MEASURED: YC launch page summary]**
  - **Dust** raised a $40M Series B (Sequoia) as the "multiplayer operating system for enterprise AI". **[MEASURED]**
  - **Anthropic** ships project memory for Team/Enterprise and is testing shared Managed Projects. **[MEASURED]**
- G1 is the fallback, not the plan.

## 3. Why now

| Driver | Evidence | Tag |
|---|---|---|
| Outage resolution is getting slower, not faster | Average time to find and resolve a network outage: 11.2 hours, up about 2 hours since 2020 (Opengear) | MEASURED |
| Downtime cost is high | New Relic 2025: median cost of a high-impact outage $2M/hour. ITIC: over $300k/hour for more than 90% of mid/large firms | MEASURED |
| Operators are being told to automate | GSMA Intelligence: more than two-thirds of operators are not beyond Level 2 autonomy, and more than half of leaders expect Level 4 within two years | MEASURED |
| AI-native network RCA is being funded but sold top-down | Selector ($66M+; AT&T Ventures, Bell, Singtel), NetAI (seed, MasOrange demo at MWC 2026), Kentik AI Advisor (Nov 2025), Nokia agentic NSP | MEASURED |
| AI SRE is a hot category, proving buyers pay for autonomous RCA | Resolve AI $1B then $1.5B valuation (2025–26); Traversal $48M (Sequoia, Kleiner); incident.io $62M; Cleric $5.5M | MEASURED |
| LLMs make multi-vendor normalisation cheap | Reading Cisco/Juniper/Nokia/MikroTik syslog and CLI output used to need per-vendor parsers; frontier models now read them | INFERRED |
| AI datacenters are networks that fail constantly | Meta: 419 unexpected interruptions in 54 days on 16,384 H100s (about one every 3 hours); 8.4% were network switch/cable. Clockwork estimates over $6M/year wasted per 2,048-GPU H200 deployment | MEASURED |
| Fiber build-outs add networks run by small teams | BEAD final proposals: 3,494 projects across 1.89M locations in 35 states; about 65% fiber | MEASURED (partial count) |

## 4. What Mycelic actually is

**Today (PROVEN from the repo):**
- **NeuralGraph**: a local graph memory for a single agent, exposed as an MCP server inside Claude Code.
- **The Mycelic service:**
  - an aiohttp API, a NATS JetStream durable event log and SQLite;
  - signed events, an outbox, and rebuild-from-stream after the database is deleted;
  - rule-based slot composition across an `agent → team → department → subsidiary → region → enterprise` hierarchy;
  - corroboration across units (`min_units`) and retraction cascades with re-evaluation;
  - lineage with per-principal redaction.
- **No LLM in the deployed data path.**
- **Ingestion is only through Claude Code/MCP, the SDK and the HTTP API.** There are no connectors to any SaaS, comms platform, log source or device (founder correction, confirmed by reading `mycelic/api.py` and `mycelic/sdk`).
- **A research simulator (`research/mycelic/`)** with sketches, question policy and descent routing. The models in it are a simulated capability axis, not real models.
- **Demonstrated:**
  - a 99-agent run in about 20 seconds;
  - crash, broker-loss and database-loss recovery;
  - a supply-risk conclusion composed across 3 teams;
  - a strategic rule across 2 regions with retraction.
- **History:**
  - 104 commits, 77 attributed to Claude, 23 to `anovruzov`, 4 to `Nurman Mahammadov`;
  - activity concentrated in September 2026 (52 commits on 2026-09-22).

**What it is not yet:**
- a product anyone has paid for;
- a system tested on real organisational data;
- a system with any domain detector.

**What it should become:** the reasoning core behind one sharp product. The reusable parts are:

| Kernel piece | Network meaning |
|---|---|
| `support`, independence accounting | Collapse symptoms that share a causal ancestor; count distinct observation channels |
| `question` / `verify` | Pick the read-only command on the device that best separates the hypotheses; run it locally |
| `contradict` | Evidence the hypothesis does not explain |
| `decay` | Alarm clears, maintenance windows, validity intervals |
| `promote` | Root cause confirmed by ticket resolution, which becomes a labelled example |
| lineage | The evidence trail a NOC engineer trusts |
| retraction cascade | A hypothesis withdrawn when its evidence clears |

## 5. Category

**Principle:**
- A seed company sells into an **existing budget line** with a **new mechanism**, and names a category later.
- The first purchase comes out of the NOC / network-operations tooling budget, the same line as network monitoring and AIOps.

Scores are 0–10 and come from the strategist's judgement. **[INFERRED]**

| Candidate | Understandable | Not BS | Differentiated | Accurate | Ownable | Searchable / collision | Expandable | Investors | Buyers | Verdict |
|---|---|---|---|---|---|---|---|---|---|---|
| Company Discovery OS | 6 | 4 | 3 | 3 | 3 | 5 (eDiscovery collision) | 8 | 4 | 2 | Reject |
| Organizational Intelligence | 6 | 4 | 2 | 3 | 1 (academic, many vendors) | 2 | 7 | 3 | 3 | Reject |
| Hive Intelligence | 3 | 2 | 6 | 4 | 6 | 6 | 7 | 3 | 1 | Ban from pitch |
| Collective Intelligence Infrastructure | 4 | 4 | 4 | 4 | 3 (MIT CCI) | 3 | 7 | 4 | 1 | Reject |
| Distributed Organizational Intelligence | 3 | 4 | 5 | 5 | 6 | 7 | 6 | 3 | 1 | Reject |
| Agent Intelligence Fabric | 4 | 3 | 2 | 4 | 2 ("agent fabric" used by vendors, UNVERIFIED) | 2 | 7 | 5 | 2 | Reject |
| Company Intelligence Kernel | 4 | 4 | 4 | 6 | 6 | 7 | 7 | 5 | 1 | Internal name only |
| Discovery Layer | 5 | 5 | 3 | 5 | 2 (data discovery, eDiscovery) | 2 | 7 | 4 | 3 | Reject |
| Distributed AI Nervous System | 4 | 2 | 3 | 3 | 2 | 2 | 6 | 3 | 1 | Ban |
| Company Nervous System | 6 | 3 | 3 | 3 | 2 | 2 | 6 | 3 | 2 | Ban |
| Distributed Immune System | 7 | 4 | 1 | 3 | 0 (Darktrace's "Enterprise Immune System") | 0 | 6 | 3 | 4 | Ban |
| Emergent Intelligence Infrastructure | 3 | 2 | 5 | 3 | 6 | 6 | 7 | 3 | 1 | Ban |
| **AI root cause for networks** (buyer phrase) | 10 | 9 | 6 | 9 | 4 | 4 (AIOps-adjacent) | 6 | 6 | **10** | **Use with buyers** |
| **Independent-evidence root cause** (investor phrase) | 7 | 8 | 8 | 9 | 7 | 7 | 8 | 8 | 5 | **Use with investors** |
| **Root-cause intelligence for distributed infrastructure** (category, later) | 8 | 8 | 7 | 8 | 6 | 6 | 9 | 8 | 6 | **Category name at Series A** |

**Banned in every pitch:**
- hive, swarm, emergent, collective, nervous system, immune system, organizational intelligence;
- "agents are the future";
- "privacy-preserving" as the lead claim.

**Keep for later:** "independent support" and "evidence that counts once". They are accurate and novel.

## 6. Initial wedge

- **Who:**
  - US regional fiber ISPs, cable operators, rural telcos and co-ops, plus fixed-wireless operators that are large enough;
  - 1,000–50,000 devices;
  - multi-vendor (for example Calix/Adtran/Nokia access, Juniper/Cisco/Arista/MikroTik core);
  - a NOC of 3–30 people;
  - an existing monitoring stack (LibreNMS, Zabbix, PRTG, SolarWinds, vendor EMS) and a ticket system.
- **Job:** an alarm storm or degraded service begins, and the first question is "what is the root cause, and is it ours?"
- **First product scope:**
  - read-only, recommend only;
  - one incident card;
  - verification commands executed only from an allowlist.
- **Why they buy fast:**
  - an owner or director can sign without enterprise procurement;
  - the pain lands on their phones at 3 a.m.;
  - ROI is countable in truck rolls, SLA credits and NOC hours. **[HYPOTHESIS: test in interviews]**
- **Why not tier-1 telcos first:** 12–24-month cycles, vendor lock-in and procurement. They are a later expansion. **[INFERRED]**

## 7. Ultimate market

TAM and realistic obtainable market are separated below. All counts are rough. US ISP counts in particular were not established this session. **[UNVERIFIED]**

| Segment (ordered by entry) | Plausible buyers | Realistic ACV | Realistic serviceable market | Notes |
|---|---:|---:|---:|---|
| US mid-size operators (wedge) | ~800–1,500 (UNVERIFIED) | $30k–120k | $40M–150M | Small but fast, and a beachhead for labelled data |
| Same, global (EU, LATAM, APAC regionals) | ~3,000–5,000 (UNVERIFIED) | $20k–80k | $100M–300M | LATAM ISP NOCs actively exploring AI (MEASURED: industry blog) |
| MSPs and enterprise NetOps | Thousands of network-heavy MSPs; ~2,000 large enterprises (UNVERIFIED) | $30k–250k | $300M–800M | 53% of MSPs already use AI for ticketing and monitoring (MEASURED: Kaseya 2026) |
| Tier-1/2 telcos | ~200–400 globally (UNVERIFIED) | $0.5M–5M | $300M–1B | Selector, Nokia, Ericsson and NetAI compete here |
| AI-datacenter / GPU fabrics | Hundreds of operators of large clusters (UNVERIFIED) | $0.3M–3M | $200M–1B, growing fast | Clockwork has $41.6M; NVIDIA tooling |
| Cross-operator early-warning network (data product) | All of the above | Usage / data | Unknown | The network-effect layer; HYPOTHESIS |

- **Long-run platform TAM** (upper bound, not a forecast): the AIOps / observability / network-intelligence spend that Splunk, Datadog, Kentik, Selector and the vendor EMS stacks capture today. That is tens of billions of dollars. Cisco paid about $28B for Splunk. **[UNVERIFIED, background]**
- **Realistic obtainable in 5 years:** $20M–60M ARR if the wedge works and two expansions land. **[INFERRED]**

**ARR path** (assumptions shown; **HYPOTHESIS**):

| Milestone | Composition | Earliest plausible |
|---|---|---|
| $1M ARR | 20 operators × $50k | 12–18 months |
| $10M ARR | 70 operators ($3.5M) + 15 MSP/enterprise ($2.5M) + 3 tier-2 telcos ($2.5M) + 2 GPU-fabric customers ($1.5M) | 30–42 months |
| $100M ARR | Telco and AI-datacenter segments at scale plus the early-warning data product | 6–8 years; probability ≤10% |
| Multi-billion revenue | Requires becoming the reliability brain for networks broadly (Splunk/Datadog scale) | ≤2–3% |

**The honest comparison.** A's theoretical TAM is larger. But the realistic market obtainable by *this team, now* is near zero, because Glean ($300M+ ARR), Microsoft, OpenAI and a dozen funded startups occupy it. **[INFERRED from MEASURED competitor data]**

## 8. Competitive landscape

Columns are condensed. "Discovers new cross-source knowledge?" means: does the product proactively find facts no single source states, as opposed to retrieving known facts? All rows are **MEASURED or UNVERIFIED as reported**; the inferences are mine.

| Company | What they sell | Architecture / locality | Retrieve vs discover | Independence / provenance | Funding / traction (reported) |
|---|---|---|---|---|---|
| **Glean** | Enterprise search + agents | Central index, permission-aware | Mostly retrieve; "Proactive Intelligence" marketing | Citations; no independence model | $7.2B valuation (Jun 2025); ARR over $300M (May 2026) |
| **Microsoft Work IQ / Fabric IQ / Foundry IQ** | Org-context layers for agents | Central (Graph, OneLake) | Retrieve + rule-based reasoning; Work IQ learns collaboration patterns | Governance; no independence model | Bundled with M365/Azure |
| **OpenAI company knowledge** | ChatGPT over Slack, SharePoint, Drive, GitHub, etc. | Central, permission-respecting | Multi-source synthesis with citations | Citations | 3M paying business users (Jun 2025) |
| **Dust** | Multiplayer enterprise agents | Central SaaS | Retrieve + agent actions | Citations | $40M Series B (Sequoia, May 2026); over $20M ARR; 3,000 orgs |
| **Memory Store** (YC S26) | Shared memory for a team's agents from meetings, Claude, Slack, Gmail via MCP | Central | Synthesis into a "company brain" | Unknown | $500k (Jan 2026) |
| **Zeroset** (Nebula; Atlas benchmark) | State layer for agents | Central SaaS (inferred) | State + history; not discovery | "With sources" | $5.2M pre-seed (Gradient, 2048 Ventures) |
| **Mem0** | Memory layer API | Central or self-host | Retrieve | Confidence and conflict resolution | $24M seed+A (Basis Set, Oct 2025); 41k GitHub stars; 186M API calls in Q3 2025 |
| **Letta** | Stateful agents (MemGPT) | Self-host / cloud | Retrieve | — | $10M seed at $70M post (Felicis, 2024) |
| **Zep / Graphiti** | Temporal knowledge-graph memory | Central / BYOC | Retrieve with temporal validity | Bi-temporal edges | Nearing $1M ARR; Graphiti about 27k stars |
| **Cognee / Supermemory / Byterover** | Memory layers | Mixed | Retrieve | Varies | $7.5M / $2.6M / undisclosed |
| **Enterpret / Unwrap** | Customer-feedback theme discovery | Central | **Discover** themes across feedback sources | Source links | $20.8M A / $15M A |
| **Gong** | Revenue intelligence | Central | **Discover** deal risk across calls, email and CRM | Source links | Over $500M ARR (Apr 2026) |
| **Celonis** | Process mining | Central (event logs) | **Discover** process bottlenecks | Event lineage | About $13B valuation |
| **CrowdStrike** | Falcon + Charlotte agentic SOC | Local sensors + central cloud correlation | **Discover**; coordinated multi-agent investigations across endpoint, identity, cloud and network (Fal.Con 2026) | Verdicts with evidence | Public |
| **Palo Alto Networks** | XSIAM, AgentiX, Protect AI (about $500M) | Central data lake + agents | Discover | — | Public |
| **Google / Wiz** | Cloud security | Agentless + sensor | Discover | — | Acquisition closed Mar 2026, $32B |
| **Darktrace** | "Self-learning" anomaly detection, the original "immune system" | Appliances/sensors + central models | Discover anomalies | — | Thoma Bravo, $5.3B; over 9,400 customers |
| **Dropzone / Prophet** (AI SOC) | Autonomous alert investigation | Central SaaS | Discover per alert | Investigation trail | $37M B / $30M A |
| **Selector AI** | Network/infra AIOps, LLM + digital twin | Central platform | Discover (correlation, RCA) | — | Over $66M; AT&T Ventures, Bell, Singtel; customers incl. Lumen, NBC |
| **Kentik** | Network observability + AI Advisor | Central data engine (a trillion points a day) + collectors | Agentic investigation | Transparent plans | Established vendor |
| **NetAI** | GNN deterministic RCA for telcos | Unknown | Discover | — | Seed; MasOrange demo (MWC 2026) |
| **Nokia / Ericsson** | Autonomous-network frameworks, rApps, agentic NSP | Vendor platforms | Discover within their domain | — | Incumbent vendors |
| **Resolve AI / Traversal / incident.io / Cleric** | AI SRE | Central telemetry + agents ("Production World Model", "Causal Search Engine") | Discover root cause | Investigation trail | $1.5B val / $48M / $62M B / $5.5M |
| **Clockwork.io** | GPU-fabric reliability, fault tolerance | Software fabric + fleet monitoring | Discover link/switch faults | — | $41.6M total (NEA) |
| **Viaduct** | Vehicle-fleet quality early warning | Central | **Discover** emerging defects | — | Series B $10M; claims $50M+ warranty savings |
| **Flower / Owkin** | Federated learning | Truly distributed | Train, not discover | — | Flower $20M A (YC-backed); Owkin pivoted to drug discovery (inferred from coverage) |

**What the table says:**

1. **Every successful "discovery" company is vertical and central.** Gong, Enterpret, Celonis, Viaduct, CrowdStrike and Selector each own one data shape, one buyer and one ROI. None sells "discovery" horizontally. **[INFERRED]**
2. **No one we found models evidence independence explicitly.** Collapsing echoed or copied evidence is closest in alarm correlation, where it is called "root vs symptom", and in truth-discovery research (copy detection). It is a real gap, but in most markets it is a feature. **[INFERRED]**
3. **"Distributed" is already standard where it is necessary.** EDR sensors and network collectors are local agents with central correlation. It is not a differentiator in itself. **[INFERRED]**
4. **Network RCA has funded players, but they sell top-down to tier-1s and large enterprises.** The mid-size, multi-vendor operator looks under-served by AI-native tools. **[HYPOTHESIS: verify in interviews; NetAI, Selector or Kentik could move down-market]**

## 9. Zeroset analysis

The detailed comparison is in `ZER0SET_COMPARISON.md`. The verdict:

- **Zeroset is one company.** Nebula is its product, a "state layer that gives agents current state and workflow history". Atlas is its self-run benchmark (Nebula 76.9%). It raised a **$5.2M pre-seed** co-led by Gradient and 2048 Ventures. The founders are 18 and 21. Its stated focus is long-running agents in manufacturing, supply chain and financial research. **[UNVERIFIED: search summaries only; primary pages blocked]**
- **What investors bought:** the "context graph / decision traces" narrative that Foundation Capital popularised in December 2025 ("AI's trillion-dollar opportunity: context graphs"), plus a research posture. **[INFERRED]**
- **Overlap with Mycelic:** per-agent memory, temporal state and provenance. That is *exactly* the horizontal memory layer we recommend leaving. **[INFERRED]**
- **Which layer is more valuable:** neither as a standalone horizontal. The state layer commoditises first (labs ship memory natively), and Zeroset's escape route ("how companies actually work") runs into Glean, Microsoft and OpenAI. **[INFERRED]**
- **Should Mycelic attack them?** No. A Zeroset with an extra $50M wins any horizontal agent-state race. It does nothing to help them win an ISP's NOC. **Our strategy holds regardless of Zeroset's funding.**

## 10. YC analysis

**What YC is buying (MEASURED from reported RFS lists, Fall 2026 and Summer 2026):**

- **Theme:** "AI is moving into the physical world".
- **Fall 2026 requests:**
  - The Primer
  - The Future of American Defense
  - A Cloud for Small Software
  - **Multiplayer AI** (Aaron Epstein: a shared, live workspace where a team watches, steers and hands off long-running agent tasks)
  - Compute at Sea
  - AI consumer products for 1B people
  - AI for the Aging Population
  - **New Operating Systems for the Physical World**
  - Crypto
  - **Data for the Real World**
  - Proving You're Human
  - **AI-Native Compliance Infrastructure**
  - Self-Maintaining APIs
- **Summer 2026:** 15 categories, with a pivot toward hard tech and vertical AI for legacy industries.
- **Batch mix:** W26 had about 60% AI-first companies. "Vertical B2B AI apps" is a large cluster. Robotics now equals AI infrastructure. **[MEASURED: third-party batch analyses]**
- **Standing partner view:** "Vertical AI agents could be 10x bigger than SaaS" (Lightcone: Friedman, Hu, Tan, Taggar). **[MEASURED]**
- **YC has backed adjacent infrastructure:** Mem0, Letta (YC participated in its seed), Flower, Memory Store (S26). A memory pitch is therefore *not novel to YC*. **[MEASURED]**
- **No NOC/telecom-AI company surfaced in 2026 batch analyses.** That is a white space, or a signal. **[UNVERIFIED]**

### Three simulated partners, evaluated independently first

*Simulations by the strategist. They are not real people's views.*

**Partner A, sceptical of broad infrastructure pitches.**

| Direction | Interview? | Kill sentence | Interest sentence |
|---|---|---|---|
| A Company Discovery OS | No | "We discover what your company knows collectively but nobody knows individually." (Glean, Copilot and ChatGPT say they do this.) | — |
| B Cyber hive | No | "A distributed immune system for AI infrastructure." (Darktrace said it in 2013.) | — |
| C Network RCA | **Yes** | "We'll start with tier-1 telcos." | "I cut AT&T troubleshooting from 48 hours to 10 minutes at Fujitsu, and I'm doing it for every regional ISP; here's an operator who gave us their alarm logs." |
| D Industrial | Maybe | "We help manufacturers discover cross-site insights." | "A medical-device complaint signal 6 weeks before the recall, on public FDA data." |
| E Agent memory | No | "Better LoCoMo score." | — |
| F Kernel | No | "The intelligence kernel connecting thousands of agents." | — |
| G1 Coding-agent fleet | Maybe | "Shared memory for Claude Code." (Memory Store is in the last batch.) | "50 engineers' agents contradicted each other 212 times last week; we caught 31 bugs before merge." |

- **What would confuse them:** the word "kernel", the hierarchy diagram, and synthetic benchmarks.
- **What they'd tell the founders to build before interview day:** one deployment on real data, and one chart of hours saved.

**Partner B, loves technically ambitious infrastructure.**

| Direction | Interview? | What makes it enormous | What's missing |
|---|---|---|---|
| A | Maybe | A truly new capability: provable cross-source discovery | Any real-data win over centralised RAG |
| B | Maybe | Machine-speed defense | Security credibility |
| C | **Yes** | "Independent evidence" plus active verification is a real algorithmic idea, and the network gives a clean physical testbed and the schema for free. Expands to AI datacenters. | Real-data accuracy against a frontier-LLM baseline |
| E | No | — | Differentiation |
| F | Yes, but sceptical | A protocol that every agent fleet speaks | Proof the abstraction holds across two domains |
| G1 | Yes | Agent fleets are a new substrate; contradiction detection across agents is new | Proof the pain exists at scale |
| G2 | **Yes** | AI clusters fail every few hours; root-cause attribution among thousands of correlated victims is exactly independence accounting | Cluster access |

- **Leaning-forward demo:** 3,000 alarms collapsing to one verified root cause, live, beating Claude given the same logs, with the bytes and tokens shown.

**Partner C, obsessed with growth and customer pull.**

| Direction | Interview? | Question they'd ask | Pull evidence they want |
|---|---|---|---|
| A | No | "Who has asked you for this?" | — |
| C | **Yes, if there are LOIs** | "How many operators replied to your cold email?" | 20+ conversations, 2+ data shares, 1 paid pilot |
| G1 | Yes, if usage | "How many teams installed it this week?" | Weekly active teams, retention |
| D | No | "How long is your sales cycle?" | — |
| F | No | "What does the first customer pay for?" | — |

- **Killer sentence for every direction:** "We haven't talked to customers yet; we've been perfecting the architecture."

**After comparing notes:**
- All three would interview **C** with real-data evidence and design partners.
- Two of three would interview **G1** with usage numbers.
- None would interview A, E or F as framed today.

**Pre-interview build list:**
- the emulated-network demo;
- one real-data replay;
- 20 interviews;
- two design partners;
- a verified founder story: artifacts or a reference from the Fujitsu/AT&T work.

## 11. a16z analysis

**Reported a16z positions (MEASURED from search summaries):**
- **Big Ideas 2026:**
  - multi-agent systems;
  - agent-native infrastructure that survives agent-scale workloads;
  - automating cybersecurity alert review and incident response;
  - cleaning and governing unstructured enterprise data;
  - "the database underneath becomes a commodity … build for execution, not storage".
- **"Your Data Agents Need Context"** (Jason Cui and Jennifer Li, March 2026): a context layer between data and agents.
- **Funds:**
  - infrastructure team: $1.7B;
  - **Machine Age Fund: $1.1B (August 2026) for chips, memory, *networks*, storage and *data centers***;
  - Speedrun accelerator: over 150 companies, over $180M deployed.

**Simulated specialists** (simulations by the strategist):

| Specialist | Why they'd care | Why they'd dismiss | Category size | Credible moat? | Too early? | Incumbents copy? | Network effects / increasing returns |
|---|---|---|---|---|---|---|---|
| AI infrastructure | C/G2: reliability for AI datacenters is a Machine Age thesis; independence-aware RCA is a real technical wedge | "Selector, Kentik, Nokia and NVIDIA exist; is this a feature?" | Observability-scale (tens of $B upper bound) | Moderate: labelled incident outcomes plus local verification | No; the pain is now | Vendors copy within their own gear; multi-vendor is harder | Yes, if a cross-operator claim network forms |
| Enterprise | A: company-brain spend is real | Glean, Microsoft and OpenAI own distribution; "services-as-software" needs a vertical | Large but taken | Weak | — | Yes, trivially | Weak |
| Cybersecurity | B: AI SOC is a thesis | CrowdStrike ships multi-agent cross-domain investigations | Huge | Weak for this team | — | Already have | Threat-intel networks exist |
| American Dynamism | C/G2: networks and AI datacenters are critical infrastructure; rural broadband is national policy | Small ACVs in the wedge | Mid | Moderate | — | Vendors | Cross-operator early warning has national-resilience value |

**Decisive a16z view:**
- **C** gets an infra or Machine-Age meeting only with the AI-datacenter expansion in the story and real-data proof.
- **G2** gets the fastest a16z interest but the slowest customers.
- **A** and **E** don't get a meeting.
- **Thesis line:** "Every network, from a rural fiber ISP to a 100k-GPU cluster, fails as a cascade of correlated symptoms. Mycelic counts evidence once, verifies at the device, and learns from every resolved incident." (Full memo in `A16Z_THESIS.md`.)

## 12. Founder-market fit

| Asset (demonstrated in the repo unless noted) | C | G1 | A | B | D | E | F |
|---|---|---|---|---|---|---|---|
| Distributed systems (JetStream, outbox, recovery): **PROVEN** | ++ | + | + | + | + | + | ++ |
| Lineage, retraction cascades, corroboration rules: **PROVEN** | ++ | ++ | + | + | + | + | ++ |
| Continual questioning and descent (simulation only): **MEASURED, synthetic** | ++ (active diagnosis) | + | + | + | + | 0 | + |
| Benchmark honesty (failed experiments, judge sensitivity, leakage audit): **PROVEN** | ++ | + | + | + | + | + | + |
| Telecom/network experience, 48h→10min comparator: **UNVERIFIED** | +++ | 0 | 0 | 0 | 0 | 0 | 0 |
| Heavy Claude Code users, built with agents: **PROVEN** (77 of 104 commits) | 0 | +++ | 0 | 0 | 0 | + | 0 |
| Security or manufacturing domain expertise | 0 | 0 | 0 | −− | −− | 0 | 0 |
| Enterprise sales credibility | − | 0 | −− | −− | −− | 0 | −− |

**Scores (0–10):** C 8 if the telecom claim verifies, 4 if not; G1 7; F 6; E 6; A 4; G2 5; D 2; B 2.

**Gaps and how to close them:**

- **C:**
  - Verify and document the Fujitsu/AT&T work: what was compared, what was measured, who can vouch.
  - Recruit one ex-NOC-manager advisor from a regional ISP within 30 days.
  - Get one design partner who lets you replay 90 days of alarms and tickets.
- **G1:** none needed for domain; the founders are the user.
- **All directions:** nobody on the team has sold software. The founder sells for the next 6 months.

**The AI-written codebase** (77 of 104 commits by Claude) is normal in 2026. A sceptical partner will still ask, "Which parts do you understand deeply enough to debug at 3 a.m. for a customer?" Prepare a crisp answer. **[INFERRED]**

## 13. Architecture implications

**The 16 technical questions, answered with a position:**

1. **Is organisational emergence real and measurable?**
   - Yes, formally. A fact F is *emergent* with respect to partition P if no single node's view entails F above confidence τ, but the union of views does.
   - It is measurable as the share of incidents whose root cause is not identifiable from any single device's or site's data. **[INFERRED]**
   - In networks it is common: a fiber cut is only *inferable* from the pattern across both ends and their neighbours. **[INFERRED]**
2. **What can Mycelic find that centralised RAG cannot?**
   - *Given the same data in one place,* nothing in principle.
   - The advantages are:
     - doing it without moving or reading everything, which matters when that costs time, bandwidth or access;
     - counting evidence correctly;
     - verifying actively.
   - The founders' own benchmark confirms that centralisation wins on recall when allowed. **[MEASURED, synthetic]**
3. **When distributed beats centralised:**
   - **Size:** local data is too big or too fast to ship (device telemetry at seconds resolution).
   - **Access:** management-plane reachability is only local.
   - **Latency:** the decision must be faster than central ingestion.
   - **Locality:** legal or contractual rules forbid moving the data.
   - **Echoes:** many correlated copies would swamp a central ranker. The founders found that more undifferentiated evidence *worsens* ranking: an oracle holding everything reports only 42.7%. **[MEASURED, synthetic]**
4. **When it necessarily loses:**
   - when local summarisation discards the discriminating detail (data-processing inequality);
   - on 1–2-witness signals (31% vs 69%);
   - on small systems where everything fits in one context window. **[MEASURED / INFERRED]**
5. **Is independent-support reasoning defensible?** Yes, with a long literature behind it:
   - copy detection in truth discovery (Dong, Berti-Équille and Srivastava);
   - double-counting ("data incest") in distributed sensor fusion;
   - correlated votes in Condorcet-style aggregation.
   - It is defensible only if independence is *measured*, not assumed. **[MEASURED: literature]**
6. **How to measure independence in networks:**
   - shared causal ancestor in the topology/protocol graph, meaning a symptom of the same upstream element;
   - same observation channel (the same SNMP poller, the same syslog relay);
   - temporal precedence;
   - copy lineage (forwarded tickets, duplicate alarms).
   - **Score:** distinct channels on distinct causal paths that converge on the hypothesis.
7. **Contradictions:** truth-maintenance-style. A hypothesis carries the set of observations it explains and the set it contradicts. Retraction cascades already exist in the code. **[PROVEN]**
8. **Decay:**
   - event-driven invalidation first (alarm clear, config change, maintenance window);
   - a per-predicate half-life second;
   - never a global time decay.
9. **Information-gain questions:**
   - Pick the read-only check whose expected outcome most reduces entropy over the current hypothesis set, divided by its cost (device load, latency).
   - The command allowlist is per vendor.
10. **Domain independence:** keep the *protocol* universal, and make detectors, schema and policies plug-ins. A universal aggregation equation is the wrong abstraction. **[INFERRED]**
11. **In the kernel:**
    - observation, claim, evidence edge (with an independence class), hypothesis, question, verification result, validity interval and lineage;
    - the operators `aggregate`, `support`, `contradict`, `route`, `question`, `verify`, `decay`, `promote` and `retract`.
12. **Application-specific:**
    - parsers and normalisation;
    - the causal schema (topology layering);
    - detectors;
    - the allowlisted verification commands;
    - thresholds and the action policy.
13. **Latency, bandwidth and privacy gains:**
    - Ship claims and sketches instead of raw telemetry.
    - Query devices only when a question needs them.
    - Measure bytes moved per incident against "ship everything to a cloud LLM".
14. **When hierarchy destroys information:**
    - Org-chart summarisation found **0%** of hidden problems in the founders' study. **[MEASURED, synthetic]**
    - Replace the org-chart topology with the **network's own topology graph** (device, site/POP, region) and an entity index.
    - The value is in the downward path. **[MEASURED, synthetic: +0.546 from questioning]**
15. **Scale (10 to 1M agents):**
    - For pilots (10–1,000 collectors, about 50k devices): a single kernel on SQLite with JetStream is fine.
    - Next: sharded kernels per region, JetStream subjects per site, sketches upward and questions downward.
    - At 1M: gossip of sketches and hierarchical kernels.
    - **The single SQLite writer breaks first.** Plan to move to Postgres or event-sourced partitions after about 10 customers. **[INFERRED]**
16. **Strongest publishable claim:**
    - "Independence-aware evidence aggregation with active verification identifies root causes in correlated alarm cascades with higher precision and lower data movement than a frontier LLM given all the data."
    - Tested on emulated networks *and* real operator incidents, with copy and cascade decoys.
    - It is publishable at a systems or networking venue (for example NSDI/SIGCOMM workshops, IMC) if it holds. **[HYPOTHESIS]**

**Kernel spec v0** (types):

```
Observation(src_device, channel, ts, raw_ref, parsed)                      # stays local
Claim(entity, predicate, polarity, valid=[t0,t1], conf, evidence=[obs_ref], channel, causal_ancestor?)
Hypothesis(root_entity, mechanism, explains=[claim], contradicts=[claim], support_independent=n, conf)
Question(target_device, command_id, expected_gain, cost, hypothesis_ids)
Verification(question_id, result, ts, raw_ref)                             # runs locally, allowlisted
Promotion(hypothesis_id, ticket_ref, outcome)                              # the label that trains everything
```

## 14. 48-hour demo

- **Build:**
  - a 40–60 node containerlab network with FRR routers in a 3-site ring with dual-homed access;
  - one Mycelic collector per site, which parses syslog and SNMP traps from the emulated devices;
  - kernel rules for 3 fault classes: link or fiber cut, interface flapping, BGP session loss caused by an upstream failure.
- **Script:**
  1. Cut a core link. About 600 alarms arrive.
  2. Mycelic emits one card. The root cause is the link between site A and site B. It shows the evidence and lists the 580 alarms explained as symptoms. It runs two allowlisted checks (`show interface`, `show bgp summary`) on the two candidate devices to verify.
  3. A decoy happens at the same time: an unrelated access-port flap. It is listed as *unexplained*, not merged.
- **Baseline:** Claude (frontier) is handed all the logs in one prompt, then the comparison table: correct?, time, tokens, bytes moved.
- **Why it is hard to dismiss as "just an LLM demo":** the attribution rules and independence accounting run without an LLM. The LLM, if used, only writes the card text.

## 15. 7-day demo

- **Scale:** 100–200 nodes, mixing FRR and Nokia SR Linux, so logs are genuinely multi-vendor.
- **Fault set:** 8 fault classes:
  - fiber cut;
  - degrading optic (rising errors);
  - MTU mismatch;
  - BGP route leak;
  - OSPF cost misconfiguration;
  - CPU-starved device;
  - power loss at a POP;
  - flapping LAG member.
- **Injections:** 20 per class, plus 20 coincidental decoys and 10 cascades where symptoms precede the root's own alarm.
- **Baselines:**
  1. a frontier LLM with all logs (chunked);
  2. RAG over logs;
  3. a classic topology/dependency-rule correlator;
  4. per-device agents with no aggregation;
  5. Mycelic.
- **Metrics:** see §17.
- **Real data:** a 90-day replay from one design partner, if obtained. This is the most important deliverable of the week.

## 16. 30-day product

- **Deployment:**
  - collector VM (syslog, SNMP traps, gNMI where available);
  - pollers for LibreNMS, Zabbix and PRTG;
  - device inventory and topology import (LLDP/CDP, the monitoring tool's maps, CSV);
  - an outbound-only connection to the kernel (hosted or on-prem).
- **Output:**
  - an incident card to Slack, Teams or email;
  - ticket notes to Jira SM, Zendesk or Freshdesk via webhook;
  - read-only verification from a per-vendor allowlist;
  - human-approved commands only.
- **Learning loop:** a ticket resolution is promoted to a label, and per-operator priors improve.
- **Proof artefact:** a weekly report covering incidents, time to the first correct hypothesis, and alarms suppressed.
- **Reused from the repo:** the event log, outbox, lineage, retraction, rules engine, auth, redaction, Docker/k8s, the smoke-test discipline, and the `CommsEvent`/independence design from the comms prompt, narrowed to NOC chat and ticket ingestion.

## 17. Benchmark design (built to be able to falsify us)

- **Pre-registration:** written and committed *before* the runs. It covers:
  - the hypotheses;
  - the thresholds;
  - where we expect to **lose**.
- **Datasets:**
  1. the emulated networks from §15;
  2. design-partner real incidents (ground truth from ticket resolutions);
  3. a public sanity check on adjacent RCA data: **RCAEval**, 735 microservice failure cases, 15 baselines. The best LLM agentic workflow there reaches 42.22% Top-1 (GALA). **[MEASURED]**
- **Systems:**
  - Mycelic;
  - a frontier LLM with all logs, long-context/chunked (the founders' winning centralised baseline, `A2`);
  - RAG over logs;
  - a topology-rule correlator;
  - GraphRAG over a log-derived knowledge graph;
  - independent local agents;
  - the operator's existing correlation (where available).
- **Metrics:**
  - Top-1/Top-3 root-cause accuracy;
  - precision and recall of the set of explained alarms;
  - false-positive rate (cards with a wrong root);
  - time to first correct hypothesis;
  - time to verification;
  - independent-support correctness against planted cascades and duplicated alarms;
  - lineage accuracy;
  - bytes moved off-site;
  - tokens and dollars;
  - robustness to a missing collector, a network partition, stale topology, decoys and contradictory telemetry;
  - scaling from 50 to 5,000 devices.
- **Conditions swept:**
  - log volume per device;
  - topology accuracy (10–40% stale);
  - decoy rate;
  - cascade depth;
  - the share of devices reachable for verification.
- **Pre-registered predictions:**
  - **Mycelic must win** on Top-3 accuracy at 1,000+ devices with decoys; on bytes moved; on time to verification; and on independent-support correctness.
  - **Expected to lose or tie:**
    - networks under about 50 devices, where everything fits in context;
    - single-device faults;
    - stale topology above 30%.
- **Kill criteria:** on real partner incidents, if Mycelic trails the frontier-LLM baseline on Top-3 by more than 5 points **and** does not beat it on time or cost, the hive mechanism is not the product. The company becomes a thin LLM workflow, and we should reconsider G1.

## 18. Customer profile

- **Primary:** Director or VP of Network Operations, or the CTO, at a regional operator.
  - 20k–300k subscribers.
  - Multi-vendor access and core.
  - 24/7 NOC, often partly outsourced.
  - Monitoring tools: LibreNMS, Zabbix, PRTG, SolarWinds or a vendor EMS.
  - Tickets: Zendesk, Freshdesk, Jira SM, or OSS/BSS-native.
- **User:** NOC tier-1/2 engineers.
- **Signals of pain:**
  - alarm storms during weather;
  - repeated "is it us or upstream?" questions;
  - truck rolls to the wrong site;
  - SLA credits to business customers.
- **Disqualifiers:**
  - under about 500 devices;
  - fully single-vendor with a capable vendor AI;
  - no ticket history to replay.

## 19. Pricing hypothesis

**HYPOTHESIS: test in the first 20 conversations.**

- **Paid pilot:** $5k–15k for 60 days. It is credited to the first year.
- **Production:** priced per monitored device per month.
  - $1.00–2.50 for access devices.
  - $5–15 for core/aggregation devices.
  - Typical ACV $30k–120k, with a floor of $24k.
- **Later:**
  - an enterprise tier with on-prem kernel, SSO and audit;
  - the early-warning network as an add-on, priced by membership;
  - GPU-fabric customers priced per GPU (for example $3–10 per GPU per month), anchored on wasted GPU-hours.
- **Gross margin:** 75–85% if LLM use is limited to card text and parsing fallback. The core attribution runs without a model. **[INFERRED]**

## 20. Go-to-market

1. **Founder-led outbound.**
   - Build a list of 300 operators from trade associations and conference exhibitor lists: Fiber Broadband Association (its Fiber Connect 2026 showcase featured AI operations), WISPA, NTCA, NANOG and regional NOGs. **[MEASURED: Fiber Connect showcase]**
   - The offer: "Send us 90 days of alarms and tickets; we'll show you what we would have found."
2. **Replay-first sales.**
   - Every pitch is a replay of *their* history.
   - Sales cycle target: 2–6 weeks to a paid pilot.
3. **Partners:**
   - monitoring tools (LibreNMS/Zabbix communities, PRTG resellers);
   - MSPs that run NOCs for several small ISPs. One MSP gives 10–50 networks.
4. **Content:** publish the benchmark and the losses; this is the founders' strength.
5. **Expansion triggers:**
   - an MSP customer → MSP channel;
   - first tier-2 telco intro from a partner;
   - an AI-datacenter design partner once the case studies exist.

## 21. 12-month roadmap

| Month | Goal | Exit criteria |
|---|---|---|
| 0–1 | 48h/7d demos; 20+ interviews; 2 data shares; YC application (2 Nov) | Real-data replay done; 2 design partners |
| 1–3 | 3 paid pilots; collector v1; card in Slack/Teams/tickets | Top-3 ≥70% on partner replay (pre-registered) |
| 3–6 | 10 customers; $250k ARR; per-vendor verification allowlists; Postgres migration plan | Net revenue retention signal; time-to-root-cause cut ≥50% |
| 6–9 | MSP channel; first tier-2 telco POC; publish the benchmark paper | 2 MSPs live; paper submitted |
| 9–12 | Seed/Series A raise; $750k–1M ARR; early-warning network beta (claims shared across 5+ operators); AI-datacenter pilot | 1 cross-operator detection that no single operator saw |

## 22. What to STOP building

1. LoCoMo tuning and the NeuralGraph chat-memory product as a company direction. Keep it as an open-source artefact.
2. The synthetic enterprise org-chart benchmark (EMERGENCE) as a fundraising asset. Its own critique says it is an upper bound with a giveaway schema and simulated models.
3. **The universal comms ingestion layer** (`prompts/UNIVERSAL_COMMS_INGESTION_PROMPT.md`), as a horizontal build.
   - It walks straight into Glean, Microsoft Work IQ, OpenAI company knowledge, Dust and Memory Store (YC S26).
   - Slack now limits non-Marketplace commercial apps to **1 request per minute and 15 objects** on `conversations.history`/`replies`, and has tightened data-use terms. **[MEASURED: Slack changelog]**
   - Keep the prompt as a parked option. Salvage the `CommsEvent` schema, copy-aware independence and deletion-as-retraction for NOC chat and ticket ingestion only.
4. Org-chart hierarchy as the default topology.
5. Category language: hive, emergent, organisational intelligence.
6. Any "kernel/platform" pitch before two verticals work.

## 23. What to KEEP

- The JetStream event log, outbox and rebuild-from-stream.
- Lineage with redaction.
- Rule composition with `min_units` corroboration.
- Retraction cascades with re-evaluation.
- Deterministic derived ids.
- Signed events.
- Auth and scopes.
- The smoke-test and CI discipline.
- The *downward questioning* insight from the research. It is the most important measured result: +0.546 discovery from questioning versus upward propagation alone. **[MEASURED, synthetic]**
- The honesty culture: published failures, judge-sensitivity reporting, leakage audits.

## 24. What to BUILD NEXT (in order)

1. The containerlab fault-injection harness, plus parsers for FRR and SR Linux syslog and SNMP traps.
2. A topology-graph hierarchy, replacing the org chart in `mycelic/hierarchy.py` with device, site and region from LLDP and an inventory import.
3. An independence classifier: shared causal ancestor, same channel, copy lineage.
4. Hypothesis generation over the topology causal schema, plus the explains/contradicts sets.
5. An information-gain question policy, plus read-only verification executors with per-vendor allowlists.
6. The incident card renderer, plus Slack, Teams and ticket webhooks.
7. The replay harness for partner history, plus the pre-registered benchmark runner.
8. The collector packaging: OVA/VM, Docker, outbound-only.

## 25. Top existential risks

| Risk | Probability | Mitigation |
|---|---|---|
| The founder's telecom story doesn't hold up under partner scrutiny | Unknown | Verify in week 1; get a reference; otherwise move to G1 |
| Mid-size operators won't share data or pay enough | Medium | Replay-first offer; MSP route; day-14 gate |
| Vendor AI (Nokia, Juniper Marvis, Calix) is "good enough" for single-vendor shops | Medium | Target multi-vendor networks explicitly |
| Selector, Kentik or NetAI move down-market | Medium | Speed, price, replay-first and design-partner depth |
| A frontier LLM over centralised logs matches accuracy (Critic 3) | Medium-high | Win on verification, cost and time; if not, kill criteria apply |
| The demo looks like a toy (emulated network) | Medium | Real partner replay as the main proof |
| Too little time before the YC deadline | High | Apply with what exists on 2 Nov; keep the 14-day plan |
| The SQLite single writer fails at a real customer | Low early | Plan the Postgres migration after the first 10 customers |

## 26. Red-team objections (three rounds each)

**Critic 1: "This is just RAG plus agents."**

| Round | Critic | Proponent | Critic again |
|---|---|---|---|
| 1 | An LLM reading logs is RAG. | The attribution (root vs symptom, independence, explains/contradicts) runs without a model. The LLM writes prose. | Then it's a rules engine. |
| 2 | Rules engines for alarm correlation have existed for decades. | Old correlators need hand-maintained rules per vendor and topology. We learn priors from ticket outcomes, verify actively, and normalise multi-vendor logs with LLMs. | Prove it beats them. |
| 3 | Show the topology-rule baseline losing. | It is in the benchmark as baseline 3 and is pre-registered. | **Accepted conditionally.** The bar is real data. |

**Critic 2: "This should simply be centralised."**

| Round | Critic | Proponent | Critic again |
|---|---|---|---|
| 1 | Ship all logs to one place. Your own benchmark says centralised wins. | In networks, collectors *are* local by necessity (no inbound access). We ship claims and pull detail on demand. | Kentik ships a trillion points a day centrally. |
| 2 | Bandwidth is cheap. | Agreed, it's not the main argument. The argument is evidence counting and active verification, which work centrally too. | So "distributed" isn't the moat. |
| 3 | — | Correct. We stop selling distribution. The moat is attribution quality plus the labelled-outcome corpus. | **Critic wins the framing; the strategy already concedes it.** |

**Critic 3: "OpenAI, Anthropic or Microsoft will make this obsolete."**

| Round | Critic | Proponent | Critic again |
|---|---|---|---|
| 1 | Claude Code plus an MCP to your NMS will do RCA next year. | Possibly for small incidents. A frontier model can't SSH into a rural POP, doesn't hold a labelled incident history, and costs per token on alarm floods. | Agents with tools will. |
| 2 | — | Then we become the trusted read-only verification layer and evidence store those agents call. MCP distribution is one we already have. | Thin. |
| 3 | — | **Partly conceded.** This is the largest long-run risk. Mitigation: own the outcome data and the on-prem footprint. | Unresolved; it is tracked by the kill criteria. |

**Critic 4: "Nobody needs emergent organisational intelligence."**

| Round | Critic | Proponent | Critic again |
|---|---|---|---|
| 1 | No budget line exists for it. | Agreed. We stopped selling it. NOCs have a budget line, and outages cost $300k+/hour at mid/large firms. | Regional ISPs aren't mid/large firms. |
| 2 | Their pain is smaller. | Smaller ACVs, but faster cycles, and they give us labelled data and case studies. | OK as a wedge only. |
| 3 | — | — | **Accepted as a wedge.** |

**Critic 5: "Technically interesting, commercially useless."**

| Round | Critic | Proponent | Critic again |
|---|---|---|---|
| 1 | You have 0 customers and 100k lines of architecture. | True. The 14-day plan is mostly customer work. | Prove there is pull. |
| 2 | — | Day-14 gate: 2 design partners or pivot. | Fine. |
| 3 | — | — | **Accepted.** The gate is the answer. |

**Critic 6, added on the evidence: "Memory Store / Dust already did your MCP thing."**
- **Accepted.** That is why G1 is the fallback, not the plan.

**Critic 7, added on the evidence: "Selector has AT&T Ventures; you're late."**
- Selector sells to tier-1s and large enterprises.
- The mid-size, multi-vendor operator is our wedge.
- If interviews show Selector or Kentik already in those accounts, we move to MSPs or GPU fabrics.
- **Unresolved until interviews.**

## 27. Final scorecard

**Weighting** (sums to 100):
- Speed to first customer (9), defensibility (8) and founder-market fit (8) weigh most. A two-person seed team dies of no customers, no moat or no credibility before it dies of a small TAM.
- Pain (7) comes next.
- TAM (6), speed to demo (6), technical differentiation (6), existing-code leverage (6) and YC attractiveness (6) follow.
- Lower weights go to the rest.
- **Inverted criteria:** "competitive intensity", "incumbents absorb" and "merely a feature" are scored so that **10 means favourable**: least crowded, least likely to be absorbed, least likely to be a feature.

All scores are strategist judgements. **[INFERRED]**

**Options:**
- **A** Company Discovery OS
- **B** Cyber
- **C** Network root cause (recommended)
- **D** Industrial
- **E** Agent memory
- **F** Kernel + narrow app
- **G1** Coding-agent fleet discovery
- **G2** AI-datacenter fabric root cause

| Criterion | Weight | A | B | C | D | E | F | G1 | G2 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Urgency | 5 | 4 | 8 | 7 | 5 | 5 | 4 | 6 | 9 |
| Pain | 7 | 4 | 8 | 7 | 7 | 4 | 4 | 5 | 9 |
| TAM | 6 | 9 | 9 | 6 | 7 | 6 | 10 | 8 | 7 |
| Speed to first customer | 9 | 4 | 2 | 7 | 2 | 7 | 4 | 8 | 3 |
| Speed to demo | 6 | 7 | 5 | 7 | 4 | 8 | 6 | 9 | 4 |
| Technical differentiation | 6 | 4 | 4 | 6 | 5 | 2 | 6 | 5 | 6 |
| Defensibility | 8 | 2 | 3 | 6 | 6 | 1 | 4 | 3 | 6 |
| Founder-market fit | 8 | 4 | 2 | 8 | 2 | 6 | 6 | 7 | 5 |
| Existing-code leverage | 6 | 6 | 3 | 6 | 4 | 9 | 8 | 9 | 5 |
| Competitive intensity (10 = least crowded) | 5 | 1 | 1 | 5 | 5 | 0 | 5 | 2 | 4 |
| Scalability | 3 | 8 | 8 | 7 | 5 | 8 | 9 | 9 | 7 |
| Gross margin potential | 2 | 7 | 7 | 7 | 6 | 7 | 8 | 8 | 7 |
| Network effects | 4 | 3 | 5 | 6 | 5 | 2 | 6 | 4 | 6 |
| Data advantage | 4 | 3 | 4 | 7 | 6 | 2 | 4 | 4 | 7 |
| YC attractiveness | 6 | 4 | 4 | 7 | 5 | 2 | 3 | 7 | 6 |
| a16z attractiveness | 4 | 5 | 5 | 5 | 5 | 3 | 6 | 6 | 8 |
| Legendary-demo potential | 3 | 6 | 6 | 7 | 4 | 4 | 6 | 8 | 5 |
| Incumbents absorb it (10 = unlikely) | 3 | 1 | 2 | 6 | 6 | 1 | 4 | 2 | 4 |
| Becomes merely a feature (10 = unlikely) | 3 | 2 | 3 | 6 | 5 | 1 | 4 | 2 | 5 |
| Creates a new category | 2 | 3 | 2 | 4 | 4 | 1 | 6 | 4 | 4 |
| **Weighted (0–10)** | 100 | **4.32** | **4.40** | **6.48** | **4.75** | **4.20** | **5.48** | **5.98** | **5.80** |
| **Unweighted (0–10)** | — | **4.35** | **4.55** | **6.35** | **4.90** | **3.95** | **5.65** | **5.80** | **5.85** |

**Sensitivity:**
- **C's lead rests on two scores.** If founder-market fit falls from 8 to 4 (the telecom claim fails) *and* speed to first customer falls from 7 to 4 (operators won't engage), C drops to 5.89, below G1 (5.98).
- That is exactly what the day-14 gate tests.

## 28. Evidence and citations

All were read via search-engine summaries; direct fetches were blocked by the sandbox proxy.

**YC:**
- [Fall 2026 RFS summary (IBTimes)](https://www.ibtimes.sg/A000Ncu)
- [Fall 2026 RFS (explainx)](https://explainx.ai/blog/yc-requests-for-startups-fall-2026)
- [Multiplayer AI RFS (modelence mirror)](https://modelence.com/yc-rfs-fall-2026/multiplayer-ai)
- [Summer 2026 RFS (roundfunded)](https://www.roundfunded.com/blogs/yc-summer-2026-request-for-startups-15-categories)
- [YC deadlines 2026–27](https://www.roundfunded.com/en/blogs/yc-application-deadlines-2026-2027)
- [W27 batch (heysuccess)](https://www.heysuccess.com/opportunity/Y-Combinator-(winter-2027-batch)-41093)
- [W26 batch breakdown](https://www.extruct.ai/research/ycw26)
- [What YC is funding in 2026](https://pinggy.io/blog/what_yc_is_funding_in_2026/)
- [Vertical AI agents 10x SaaS (Lightcone)](https://onecerebral.beehiiv.com/p/vertical-ai-agents-could-be-10x-bigger-than-saas)
- [Memory Store (YC launch)](https://www.ycombinator.com/launches/QPs-memory-store-your-company-s-brain)
- [Memory Store (YC company page)](https://ycombinator.com/companies/memory-store)

**a16z:**
- [Big Ideas 2026: the agentic interface](https://a16z.com/podcast/big-ideas-2026-the-agentic-interface/)
- [Big Ideas 2026: the enterprise orchestration layer](https://a16z.com/podcast/big-ideas-2026-the-enterprise-orchestration-layer/)
- [a16z AI ideas 2026 summary](https://www.the-ai-corner.com/p/a16z-ai-ideas-2026-partners)
- [Your Data Agents Need Context](https://a16z.com/your-data-agents-need-context/)
- [a16z infrastructure fund (Jennifer Li)](https://gtmnow.com/a16z-ai-infrastructure-bet-jennifer-li/)
- [Machine Age Fund $1.1B](https://www.kucoin.com/news/flash/a16z-raises-1-1-billion-for-ai-infrastructure-fund)
- [What a16z funds in AI infra (TechCrunch)](https://techcrunch.com/podcast/what-a16z-is-actually-funding-and-what-its-ignoring-when-it-comes-to-ai-infra/)

**Context-graph thesis:**
- [Foundation Capital: context graphs](https://ashugarg.substack.com/p/ais-trillion-dollar-opportunity-context)

**Memory / agent state:**
- [Mem0 $24M (VCA)](https://www.vcaonline.com/news/2025102807/mem0-raises-24m-series-a-to-build-memory-layer-for-ai-agents/)
- [Letta seed](https://www.vcaonline.com/news/2024092404/berkeley-ai-research-lab-spinout-letta-raises-10m-seed-financing-led-by-felicis-to-build-ai-with-memory/)
- [Letta valuation](https://traded.co/vc/deal/letta-secures-10-million-seed-funding-led-by-felicis-ventures-at-70-million-valuation/)
- [Zep paper](https://arxiv.org/abs/2501.13956)
- [Zep 2026 notes](https://rywalker.com/research/zep)
- [Cognee seed](https://www.cognee.ai/cognee-raises-seven-million-five-hundred-thousand-dollars-seed)
- [Supermemory](https://nz.dealroom.co/news/feed/supermemory-secures-2-6m-for-ai-memory)
- [Byterover 2.0](https://www.producthunt.com/products/byterover/launches/byterover-2)
- [Contexo (Cursor forum)](https://forum.cursor.com/t/contexo-give-your-cursor-agent-a-shared-versioned-memory-of-your-context-open-source-mcp/163879)
- [Anthropic memory for Team/Enterprise](https://venturebeat.com/ai/anthropic-adds-memory-to-claude-team-and-enterprise-incognito-for-all)
- [Claude Managed Projects](https://pasqualepillitteri.it/en/news/8685/claude-managed-projects-anthropic-autonomous)
- [MCP adoption (97M monthly SDK downloads)](https://dev.to/akaranjkar08/mcp-hit-97m-downloads-what-developers-need-to-know-in-2026-1552)
- [Claude Code plugin marketplace](https://www.alexcloudstar.com/blog/claude-code-plugin-marketplace-skills-2026)

**Enterprise knowledge:**
- [Glean $300M ARR](https://valueaddvc.com/blog/glean-valuation-revenue-2026-300m-arr-enterprise-ai-search)
- [Microsoft Work IQ / Fabric IQ](https://www.neowin.net/news/microsoft-introduces-work-iq-and-fabric-iq-unified-intelligence-layers-for-agentic-ai/)
- [OpenAI company knowledge](https://www.maginative.com/article/openai-launches-company-knowledge-for-chatgpt/)
- [Dust $40M B](https://tech.eu/2026/05/18/dust-raises-40m-series-b-to-build-the-multiplayer-operating-system-for-enterprise-ai/)
- [Enterpret A](https://pulse2.com/enterpret-ai-based-customer-feedback-intelligence-company-raises-20-8-million-series-a)
- [Gong over $500M ARR](https://en.globes.co.il/en/article-1001542789)
- [Celonis valuation](https://www.processexcellencenetwork.com/process-mining/news/celonis-makes-visual-capitalists-list-of-worlds-50-most-valuable-private-companies/amp)
- [Nango seed](https://nango.dev/blog/nango-raises-7-5m-led-by-gradient)

**Slack API limits:**
- [Rate-limit changes for non-Marketplace apps](https://docs.slack.dev/changelog/2025/05/29/rate-limit-changes-for-non-marketplace-apps)
- [Terms and FAQ](https://api.slack.com/changelog/2025-05-terms-rate-limit-update-and-faq)
- [Dust's Slack terms note](https://dust-tt.notion.site/Slack-Terms-of-Service-update-and-API-Changes-21728599d94180f3b2b4e892e6d20af6)

**Security:**
- [CrowdStrike agentic SOC (Fal.Con 2026)](https://softprom.com/crowdstrike-agentic-soc-next-evolution)
- [Charlotte AgentWorks](https://www.businesswire.com/news/home/20260325196103/en/CrowdStrike-Launches-the-Charlotte-AI-AgentWorks-Ecosystem-for-Building-Secure-Agents)
- [Google–Wiz close](https://www.clearygottlieb.com/news-and-insights/news-listing/google-completes-32-billion-acquisition-of-wiz)
- [PANW–Protect AI](https://siliconangle.com/2025/04/28/palo-alto-networks-buys-protect-ai-reported-500m-debuts-new-cybersecurity-tools/)
- [Darktrace–Thoma Bravo](https://www.sdxcentral.com/news/private-equity-giant-thoma-bravo-to-buy-darktrace-for-53b-to-bolster-its-45b-cybersecurity-portfolio/)
- [Prophet $30M](https://www.businesswire.com/news/home/20250729681026/en/Prophet-Security-Raises-$30M-Series-A-Announces-Industrys-Most-Comprehensive-Agentic-AI-SOC-Platform-to-Transform-Security-Operations)
- [Dropzone](https://www.maginative.com/article/dropzone-ai-raises-16-85m-to-empower-socs-with-autonomous-ai-investigations/)
- [Anthropic AI-orchestrated espionage report](https://assets.anthropic.com/m/ec212e6566a0d47/original/Disrupting-the-first-reported-AI-orchestrated-cyber-espionage-campaign.pdf)

**Network / telecom / SRE:**
- [Selector $33M B](https://www.businesswire.com/news/home/20241119934906/en/Selector-AI-Raises-33M-Series-B-to-Eliminate-Downtime-for-the-Worlds-Largest-Networks)
- [Kentik AI Advisor](https://www.helpnetsecurity.com/2025/11/18/kentik-ai-advisor/)
- [NetAI at MWC 2026](https://www.4yfn.com/exhibitors/36731-netai-inc)
- [ISP NOC AI reality](https://ayuda.la/en/blog/ia-noc-isp-gos-2026-huawei-hype-vs-realidad/)
- [Fiber Connect 2026 AI-ops showcase](https://fiberbroadband.org/2026/05/11/fiber-connect-2026-proof-of-concept-showcase-to-feature-ai-powered-operations-device-management-and-subscriber-experience/)
- [GSMA autonomy levels](https://telecomlead.com/5g/gsma-intelligence-telecom-operators-must-accelerate-shift-to-intent-driven-autonomous-networks-125105)
- [AI-RAN / agentic 2026](https://techblog.comsoc.org/2026/06/22/ai-ran-and-agentic-ai-get-real-ericsson-nokia-verizon-other-network-operators-enter-into-a-new-automation-era/)
- [Resolve AI $125M A](https://techcrunch.com/2026/02/04/ai-sre-resolve-ai-confirms-125m-raise-unicorn-valuation/)
- [Traversal $48M](https://traversal.com/blog/launch-announcement)
- [incident.io $62M](https://techcrunch.com/2025/04/10/incident-io-raises-62m-at-a-400m-valuation-to-help-it-teams-move-fast-when-things-break)
- [Cleric](https://www.clay.com/dossier/cleric-funding)
- [Causely seed](https://www.preqin.com/data/profile/asset/causely--inc-/552616)
- [Clockwork.io](https://www.hpcwire.com/2026/03/11/clockwork-io-introduces-live-gpu-migration-for-ai-cluster-failures/)
- [Meta Llama 3 interruptions](https://datacenterdynamics.com/en/news/meta-report-details-hundreds-of-gpu-and-hbm3-related-interruptions-to-llama-3-training-run)
- [Opengear MTTR / downtime statistics](https://virima.com/blog/it-downtime-cost-statistics/)
- [New Relic outage cost 2025](https://www.businesswire.com/news/home/20250917981661/en/New-Relic-Study-Reveals-Businesses-Face-an-Annual-Median-Cost-of-%2476-Million-from-High-Impact-IT-Outages)
- [Kaseya MSP AI 2026](https://www.flamingo.run/blog/future-of-ai-in-msp-business)
- [BEAD tracker](https://connectednation.org/blog/connected-nation-launches-bead-tracker-to-monitor-funding-for-broadband-expansion)
- [BEAD by technology](https://rcrwireless.com/20251024/fundamentals/states-bead-progress)

**Industrial / federated:**
- [Viaduct](https://pulse2.com/viaduct-10-million-raised-to-solve-and-predict-product-failures)
- [Flower $20M](https://pulse2.com/flower-20-million-raised-to-train-better-ai-on-distributed-data)
- [Owkin trajectory](https://businessmodelcanvastemplate.com/blogs/growth-strategy/owkin-growth-strategy)

**Research:**
- [MAST: why multi-agent LLM systems fail](https://arxiv.org/abs/2503.13657v2)
- [Towards a science of scaling agent systems](https://arxiv.org/pdf/2512.08296)
- [Truth discovery with source dependence (Dong et al.)](https://lunadong.com/publication/dependence_vldb.pdf)
- [Hidden profile](https://en.wikipedia.org/wiki/Hidden_profile)
- [RCAEval](https://arxiv.org/abs/2412.17015)
- [GALA](https://arxiv.org/html/2508.12472v1)
- [Public JIRA dataset](https://arxiv.org/abs/2201.08368)
- [NHTSA complaints](https://catalog.data.gov/dataset/nhtsas-office-of-defects-investigation-odi-complaints)

**Repository (read locally, PROVEN):**
- `README.md`
- `docs/MYCELIC_ARCHITECTURE.md`
- `docs/MYCELIC_ENTERPRISE.md` §1, §2, §24–§26
- `mycelic/models.py`, `mycelic/api.py`, `mycelic/sdk/__init__.py`
- git history

## 29. Confidence level

| Claim | Confidence |
|---|---|
| A (horizontal company discovery) and E (agent memory) are wrong first moves | High (about 85%) |
| A hard vertical beats a horizontal pitch for this team now | High (about 80%) |
| C is the best vertical | Moderate (about 55%) |
| The specific wedge (mid-size operators) is right inside C | Moderate-low (about 45%); MSPs or GPU fabrics may be better entry points; interviews decide |

## 30. What evidence would change the decision

1. **The founder's telecom work cannot be documented or referenced** → founder-market fit drops; switch to G1 or G2.
2. **Fewer than 2 of 20+ operators will share historical data by day 14** → switch to G1 (coding-agent fleets) and apply to YC with usage data.
3. **On real replays, a frontier LLM given all logs matches Mycelic's Top-3 accuracy at similar cost and time** → the mechanism is not the moat. Become an LLM workflow company in NetOps, or reconsider.
4. **Interviews show Selector, Kentik or NetAI already entrenched in mid-size operators** → move the wedge to MSPs running multi-tenant NOCs, or to GPU fabrics.
5. **A GPU-cluster operator offers immediate access and a paid pilot** → lead with G2. Same kernel, larger ACV, stronger a16z story.
6. **Real customer pull for G1 appears unprompted** (teams asking for cross-agent contradiction detection) → run G1 in parallel as an open-source MCP product for distribution.
