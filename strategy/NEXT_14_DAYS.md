# Next 14 days (Thu 8 Oct – Wed 21 Oct 2026)

**Goal:** by day 14, know whether network root cause (Option C) has real pull. Either way, have a YC application ready for the **2 November 2026, 8pm PT** on-time deadline.

**Rule for the 14 days:** half of every day is customer work. Architecture work that no customer or demo needs is banned.

**Day-14 gate (pre-committed):**

| Result by day 14 | Decision |
|---|---|
| At least 2 operators have shared historical alarms and tickets, **and** the founder's telecom story is documented | Commit to C and apply to YC with it |
| Otherwise | Switch to G1 (contradiction and duplicate detection across a team's coding agents, on the existing MCP surface) and apply with usage data |

## Day-by-day plan

### Day 1, Thu 8 Oct: verify the story and build the list

- **Founder:**
  - Write a one-page account of the Fujitsu/AT&T comparator. Cover the process, what was compared, the scale, how 48h → 10min was measured, the dates and the team.
  - Ask one former colleague or manager to be a reference.
  - If neither can be produced, flag it now. It changes the plan.
- **Both founders:**
  - Build a target list of 300 mid-size operators, using Fiber Broadband Association members, WISPA, NTCA, NANOG and regional-NOG attendees, and BEAD awardees.
  - Record for each: device-count estimate, vendors and NOC contact.
- **Eng:** freeze all non-network work. Branch `network-rca`.

### Day 2, Fri 9 Oct: outreach and the harness

- **Outreach:** send 60 personalised messages, by email and LinkedIn. The ask: "20-minute call about alarm storms. If useful, send us 90 days of alarms and tickets and we'll show what we'd have found. Read-only, under NDA."
- **Eng:**
  - Get containerlab running with a 40-node FRR topology (3 sites, ring plus dual-homed access).
  - Centralise syslog and SNMP traps to a local collector.
  - Write the fault injector for link down, interface flap and BGP neighbour loss.

### Day 3, Sat 10 Oct: the 48-hour demo, part 1

- **Eng:**
  - Write collector parsers for FRR syslog and traps that emit `Observation` → `Claim`.
  - Write a topology importer (LLDP or a static YAML file) that replaces the org-chart hierarchy with device, site and region.
- **Write the pre-registration** in `research/netrca/PREREG.md`. It must state:
  - hypotheses, thresholds and kill criteria (copy them from the decision report §17);
  - **the cases where we expect to lose.**

### Day 4, Sun 11 Oct: the 48-hour demo, part 2

- **Eng:**
  - Independence classifier: shared causal ancestor and shared channel.
  - Hypothesis generation over the topology layering.
  - The explains and contradicts sets.
  - Incident card output (Markdown to a Slack webhook).
- **Run:** a fiber cut plus a decoy flap. Record a rough screen capture.

### Day 5, Mon 12 Oct: calls and verification

- **Customer calls:** take every booked call. Ask the same 8 questions each time:
  1. What tools do you use today?
  2. Tell me about the last bad alarm storm.
  3. How long did it take to find the cause?
  4. Who was paged?
  5. What did it cost?
  6. What do vendor tools miss?
  7. Would you share data?
  8. What would you pay?
- **Second outreach wave:** 60 more messages.
- **Eng:**
  - Information-gain question policy.
  - Read-only verification executor that runs allowlisted `show` commands in the containers.

### Day 6, Tue 13 Oct: baselines

- **Eng:** implement the baselines.
  1. Frontier LLM with all logs (chunked long context).
  2. RAG over logs.
  3. Topology-rule correlator.
  4. Per-device agents with no aggregation.
- **Metering:** tokens, dollars, bytes moved and wall time.
- **Calls:** continue. Find a NOC-manager advisor; offer equity (0.25–0.5%, standard advisor terms).

### Day 7, Wed 14 Oct: the 7-day benchmark

- **Eng:**
  - Scale to 120+ nodes and add Nokia SR Linux for multi-vendor logs.
  - Cover 8 fault classes × 20 injections, plus 20 decoys and 10 cascades where a symptom fires before the root.
  - Run every system.
- **Publish the results honestly** in `research/netrca/RESULTS.md`, including any loss.

### Day 8, Thu 15 Oct: the first real data

- **Goal:** the first design partner's data arrives. If none has, chase the warmest 5 leads.
- **Eng:**
  - Write the replay harness: historical alarms plus tickets in, cards plus timing out.
  - Score each card against the ticket's resolution.
  - Map the partner's ticket fields.

### Day 9, Fri 16 Oct: the first replay

- **Run the replay** on partner #1's 90 days.
- **Report to the partner:** what we would have found, how fast, and what we got wrong.
- **Ask for a paid 60-day pilot** at $5k–15k, credited to year 1.

### Day 10, Sat 17 Oct: product hardening

- **Collector packaging:** a Docker image plus an OVA plan, with outbound-only connections.
- **Read-only allowlist** per vendor: FRR, SR Linux, and Cisco/Juniper syntax stubs.
- **Card delivery:** Slack, Teams and email.
- **Write `SECURITY.md` for the collector:** credentials, read-only enforcement and data handling.

### Day 11, Sun 18 Oct: demo and narrative

- **Record the 2-minute demo video:** the emulated cascade, the side-by-side baseline, then the real replay slide.
- **Draft `YC_PITCH.md` answers** into the actual YC application form.

### Day 12, Mon 19 Oct: second replay and pricing

- **Partner #2:** ingest their data and run the replay.
- **Calls:** test the pricing hypothesis on 5 more operators: per device, pilot fee and ACV range.
- **Log objections verbatim.**

### Day 13, Tue 20 Oct: synthesis

- **Update the scorecard** in `MYCELIC_DIRECTION_DECISION.md` §27 with real evidence: interviews, data shares, replay accuracy and objections.
- **Re-run the sensitivity analysis.**
- **Write a one-page memo of what we learned,** including what disconfirms C.

### Day 14, Wed 21 Oct: the gate

- **Apply the gate above.**
- **If C passes:**
  - lock the YC application;
  - book an office-hours or alumni practice interview;
  - ask partners for LOIs;
  - prepare a short Speedrun/seed outreach list.
- **If C fails:**
  - switch the next 12 days to G1;
  - ship the open-source MCP "agent contradiction and duplicate-work detector" to 20 teams that use Claude Code;
  - apply with usage data by 2 November.

## Metrics tracked daily

| Metric | Day-14 target |
|---|---:|
| Operators contacted | 300 |
| Calls held | 20+ |
| Operators that shared data | 2+ |
| Paid pilots or LOIs | 1+ |
| Emulated benchmark published, with losses | Yes |
| Real-replay Top-3 accuracy on multi-device incidents | Measured, against the pre-registered 70% |
| Median time to first correct hypothesis (replay) | Measured, against the pre-registered 5 minutes |
| Founder reference secured | Yes |
| Advisor signed | 1 |

## Explicitly not doing in these 14 days

- Universal comms ingestion (Slack, Teams, Gmail connectors).
- LoCoMo or chat-memory work.
- EMERGENCE synthetic-benchmark extensions.
- Kubernetes hardening.
- Rebranding, category naming or a website beyond one landing page.
