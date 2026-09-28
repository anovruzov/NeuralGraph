# Demonstration organization and verification scenario

Everything here is fictional. The seed marks the tenant, its users, units, goals and documents with
`is_demo = 1`, and the UI shows a permanent "Demonstration data" banner for that tenant.

## Tenant 1 — Meridian Cold Chain (slug `meridian`)

A fictional cold-chain logistics group. The hierarchy deliberately omits a level in one branch
(Iberia has teams directly under the subsidiary) and contains a cross-functional project.

```
Meridian Cold Chain (executive)
└── Europe (region)
    ├── Meridian Nordics (subsidiary)
    │   ├── Operations (department)
    │   │   ├── Customer Support (team)
    │   │   └── Fleet Dispatch (team)
    │   └── Engineering (department)
    │       ├── Platform (team)
    │       └── Site Reliability (team)
    └── Meridian Iberia (subsidiary)          ← no department level
        └── Iberia Operations (team)
Project: Dispatch Modernization (project) spanning Fleet Dispatch + Platform
```

| Persona | Role(s) | Unit | Holder domains |
|---|---|---|---|
| Ingrid Solheim | executive | Meridian Cold Chain | — |
| Tomas Reyes | org_admin | Meridian Cold Chain | — (administrator: no evidence access) |
| Petra Lindqvist | regional_lead | Europe | — |
| Anders Vik | subsidiary_lead | Meridian Nordics | — |
| Diego Ruiz | subsidiary_lead + team_lead | Meridian Iberia / Iberia Operations | dispatch, customs |
| Maya Okafor | department_lead | Operations | — |
| Lucas Brandt | department_lead | Engineering | — |
| Sofia Berg | team_lead | Customer Support | support, metrics |
| Jonas Holm | team_lead | Fleet Dispatch | dispatch, approvals, on-call |
| Priya Nair | team_lead | Platform | deployments, approvals |
| Elin Dahl | employee | Customer Support | support, deployments |
| Marcus Lund | employee (+ project member) | Fleet Dispatch, Dispatch Modernization | dispatch, approvals |
| Ana Costa | employee (+ project member) | Platform, Dispatch Modernization | deployments |
| Noor Haddad | employee | Site Reliability | on-call, escalation |
| Rafael Ortega | employee | Iberia Operations | customs, dispatch |

Every persona with holder domains owns one evidence holder (its own SQLite file under
`<data_dir>/holders/<holder_id>/evidence.db`, its own key). Two of them (Elin's and Marcus's) run as
**separate processes** in the verification scenario; the rest are embedded.

Grants: Elin grants Sofia (her lead) `artifact` access to her holder ("sharing selected knowledge").
Nobody else has raw access to anyone's notes.

## Evidence (documents per holder)

Designed so that the deterministic fake model produces each situation the verification needs.

| Holder | Title | Content gist | observed_at | Purpose |
|---|---|---|---|---|
| Elin | Support retrospective, week 36 | "Deploy approvals for hotfixes take two days because the change board meets twice a week. Tickets stay open while we wait for the approval." | 3 days ago | root R1 |
| Ana | Forwarded: Support retrospective, week 36 | **exact copy** of Elin's text | 1 day ago | copy of R1 → copied support, not independent |
| Marcus | Dispatch incident log | "Route changes need deploy approvals; approvals take two days and delay dispatch fixes. Drivers wait for the approval before the route update goes live." | 2 days ago | root R2, complementary to R1 |
| Priya | Change board notes | "The change board approves deploys on Tuesdays and Thursdays; deploy approvals take two days on average." | 5 days ago | root R3, third independent source |
| Sofia | Ticket resolution metrics | "Median ticket resolution time is 52 hours; 30 percent of tickets wait on deploy approvals." | 2 days ago | measurement baseline |
| Noor | On-call roster (January) | "The on-call rotation has 4 engineers and pages take 45 minutes to acknowledge." | **200 days ago** | stale fact (R4) |
| Jonas | On-call roster (current) | "The on-call rotation has 6 engineers and pages take 20 minutes to acknowledge." | 1 day ago | genuine contradiction with R4 (R5) |
| Noor | Pager escalation policy | "Escalations go to the platform lead after 30 minutes without acknowledgement." | 200 days ago | single stale source → stale hypothesis |
| Rafael | Customs clearance delays | "Customs paperwork delays cross-border shipments by a day; the approval from the customs broker is the blocker." | 4 days ago | single source outside Nordics → hypothesis; blind verification finds no other holder |

## Demonstration goal (clearly labelled)

Owner: unit **Europe** (created by Petra Lindqvist). Title: *[Demo] Identify recurring operational
blockers across teams, verify their causes, and propose measurable actions that reduce resolution
time.* Success criterion: `resolution_time_hours` target 24 (decrease), baseline 52 (from Sofia's
metrics). Budget: tokens 200 000, usd 5, questions 40, follow-up depth 3. Loop active by default.
Subgoals: Customer Support — *[Demo] Cut ticket time waiting on approvals*; Fleet Dispatch — *[Demo]
Reduce route-update delay* (assigned to the team leads).

Expected loop behaviour with the fake model:

* domain `deployments` → question routed to Elin, Ana, Priya → agreement cluster → **supported**
  finding (roots R1, R3; R1's copy counted as copied support, not independent)
* domain `approvals` → Marcus, Priya, Jonas → supported (R2, R3)
* domain `on-call` → Noor, Jonas → numeric mismatch → two **contested** claims and a conflict object
* domain `escalation` → Noor only, 200 days old → **stale** hypothesis; re-verification finds nobody else
* domain `customs` → Rafael only → **hypothesis**; blind verification excludes Rafael → no authorized
  holders → uncertainty retained
* department / subsidiary / region / executive workspaces aggregate these with `aggregate_level`

## Tenant 2 — Orbital Bakery Co. (slug `orbital`)

Two users (an administrator/executive and one employee with a holder holding "Oven maintenance
schedule"). Used only to prove isolation: nothing of Meridian is visible, searchable, streamed or
routed to Orbital, and vice versa.

## Verification scenario (`python -m mycelic scenario`)

Runs against a temporary data directory with the fake model and the SQLite transport, starts the API
in-process on a random port, launches Elin's and Marcus's holders as **subprocesses**
(`python -m mycelic holder ...`), then checks, in order, each item in the task's list (goal creation and
loop activation, automatic question routing, responses from separate stores, supported discovery and
follow-up, level abstraction, private evidence protection, copied evidence, source revision,
worker restart without duplicated effects, pause/resume/budget/stop, worker progress without any
HTTP client, inspection after returning) and prints a PASS/FAIL table with the evidence for each
check. `docs/mycelic/VERIFICATION.md` records the latest run.
