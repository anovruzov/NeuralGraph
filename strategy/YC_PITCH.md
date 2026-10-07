# Mycelic: YC Winter 2027 pitch

*The on-time deadline is **2 November 2026, 8pm PT**, per reported YC guidance. Decisions are due by 11 December; the batch runs January–March in San Francisco.*
*Every bracketed `[ ]` must be replaced with a real, verifiable fact before submission. Do not submit placeholders.*

## 50-character description

**AI that finds the root cause of network outages** (47 characters)

Alternatives:
- "Root cause for network outages, in minutes" (42)
- "AI root cause for multi-vendor networks" (39)

## One sentence

Mycelic tells a network operator's NOC what actually broke within minutes of an alarm storm, with the evidence and a verification run on the device itself.

## Two sentences

When a fiber cut or a bad optic triggers thousands of alarms across a multi-vendor network, NOC engineers spend hours separating the root cause from its symptoms. Mycelic runs inside the operator's network, counts each symptom once, checks the few devices that can confirm the cause, and posts a verified root cause in minutes.

## 30-second pitch

When a network breaks, it doesn't send one alarm. It sends three thousand. Every downstream device complains, and a NOC engineer has to work out which complaint is the cause. Industry surveys put the average time to find and fix a network outage at about 11 hours.

At Fujitsu, working on AT&T network data, [founder] built a comparator that cut one troubleshooting process from 48 hours to 10 minutes. [Replace with the verified specifics.]

Mycelic generalises that. Collectors sit inside the operator's network. They treat symptoms of the same failure as one piece of evidence, not thousands, and run read-only checks on the two or three devices that can confirm the cause. A verified root cause lands in Slack or the ticket.

On [design partner]'s last 90 days of incidents, we found the root cause for [X]% of them in [Y] minutes. We start with the hundreds of mid-size fiber and cable operators whose small NOCs drown in alarms, then move to MSPs, telcos and AI datacenters.

## Problem

- **Alarm storms.** One failure in a network produces cascades of correlated alarms across many devices and vendors.
- **The real work is attribution.** Is this the cause or a symptom? Is it ours or upstream? Engineers do this by hand, with `show` commands, across vendor CLIs.
- **Resolution is slow and getting slower.** Average outage find-and-resolve time is about **11.2 hours**, up about 2 hours since 2020 (Opengear, reported). High-impact outages cost mid/large companies **over $300k per hour** (ITIC, reported).
- **Mid-size operators are under-served.** Regional fiber, cable and rural telcos have small NOCs and multi-vendor gear. AI-native root-cause tools such as Selector, Nokia and Kentik sell mainly to tier-1s and large enterprises. [Validate in interviews.]

## Solution

- **A collector inside the network.** It runs read-only and outbound-only, and ingests syslog, SNMP traps, streaming telemetry, the monitoring tool's events and the topology.
- **Attribution that counts evidence once.** Symptoms that share a causal ancestor in the topology collapse into one. Independent observation channels that converge on a hypothesis raise confidence.
- **Active verification.** It picks the single read-only check that best separates the competing hypotheses and runs it locally on the device, from an allowlist.
- **One incident card** in Slack, Teams or the ticket. It shows the root cause, the alarms it explains, the alarms it doesn't, the checks run, a confidence, and the full evidence trail.
- **It learns from outcomes.** Every resolved ticket becomes a label.

## Why now

1. **LLMs make multi-vendor log and CLI normalisation cheap.** Per-vendor parsers used to be the moat for incumbents.
2. **Operators are being pushed toward autonomous operations.** Most are still at Level 2, and over half of leaders expect Level 4 within two years (GSMA Intelligence, reported).
3. **AI SRE has proven buyers pay for autonomous root cause.** Resolve AI is valued at $1.5B, and Traversal and incident.io raised $48M and $62M (reported). Networks are next, and they are harder, because devices can't run your agent and data is spread across vendors.
4. **Fiber build-outs add networks run by small teams** (BEAD: thousands of funded projects, about 65% fiber, reported).

## Why us

- **[Founder]: telecom and network infrastructure at Fujitsu on AT&T data.** Built the 48h → 10min comparator [verify; name what was compared, the scale, and a reference].
- **Distributed systems we already run.** A durable event log (NATS JetStream), signed events, crash and broker-loss recovery, an evidence-lineage graph, corroboration rules and retraction cascades. All of it is tested in CI and runs today.
- **Research discipline.** We published an 18-architecture benchmark where our own design lost on recall to a centralised baseline. We publish failed experiments and judge-sensitivity analyses. We will publish our network benchmark the same way, losses included.
- [Add: anything about shipping speed, prior users, or a NOC advisor once signed.]

## Competition

| Who | What they do | Why we win in our wedge |
|---|---|---|
| Selector AI, Nokia, Ericsson, NetAI | AI root cause and autonomous ops, sold top-down to tier-1 telcos and large enterprises | Built for multi-vendor mid-size operators. Replay-first sale in weeks. Priced per device. |
| Kentik (AI Advisor) | Central network observability with agentic investigation | We verify at the device and attribute root vs symptom explicitly. Complementary to flow data. |
| Vendor tools (Calix, Juniper Marvis, Cisco) | Strong within one vendor's gear | Most mid-size networks are multi-vendor. |
| AI SRE (Resolve, Traversal, incident.io) | Application and cloud incidents | Different data (devices, optics, routing), different buyer (NOC). |
| "Just ask Claude with all the logs" | General LLM | Our pre-registered benchmark compares directly. We win on verification, time and cost, or we say so. |

## Moat

1. **Independence-aware attribution with active verification.** The technical core, already partly built: lineage, corroboration and retraction exist today.
2. **An outcome-labelled incident corpus.** Every resolved ticket across every customer teaches root-cause priors that no single operator's tool has.
3. **On-prem footprint plus trust.** A read-only collector inside the network is hard to displace once it is the place where incidents get explained.
4. **Later, a cross-operator early-warning network.** Operators share *claims*, such as "optic model X lot Y failing", never raw data. Each new operator makes detection faster for all of them. This is the network effect. [HYPOTHESIS]

## Demo narrative (2 minutes)

1. A live map of an emulated 150-router, multi-vendor network: FRR and Nokia SR Linux in containerlab.
2. Cut a core fiber. Around 3,000 alarms flood in, shown as a counter.
3. Forty seconds later Mycelic posts one card:
   - the root cause, "link A–B down at the A side";
   - 2,940 alarms explained;
   - 6 unexplained, including the decoy flap, kept separate on purpose;
   - two read-only checks run on the two candidate routers, with their output.
4. The same incident handed to a frontier LLM with all the logs, side by side. We show where it was wrong or slow, and the tokens and bytes it used. If it was right, we show that too, and show where we win on time and cost.
5. A real slide: [design partner]'s 90-day replay. Incidents, time to the first correct hypothesis, and alarms suppressed.

## Evidence needed before the YC interview

- [ ] **The founder's telecom story is documented.** What, where, when, measured how, and a reference who will take a call.
- [ ] **20+ operator conversations are logged.** Pain quotes, current tools, and willingness to share data and to pay.
- [ ] **At least 2 design partners have shared historical alarms and tickets.** At least 1 has agreed to a paid pilot (LOI or invoice).
- [ ] **The 7-day emulated benchmark is published.** It is pre-registered, with all baselines and the losses included.
- [ ] **At least one real-data replay result.** Top-3 accuracy and time-to-root-cause against the ticket's recorded resolution.
- [ ] **A 2-minute demo video is recorded.**
- [ ] **One NOC-manager advisor is named.**

## Words we will not use

Hive, swarm, emergent, collective intelligence, organisational intelligence, nervous system, immune system, kernel, platform, "agents are the future", "privacy-preserving" as the lead.
