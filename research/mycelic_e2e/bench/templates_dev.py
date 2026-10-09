"""DEV template bank (surface text only; no gold, no ids). Disjoint from holdout/templates_holdout.py (tested).

Lexical contract with the deterministic provider (PLAN_v1 §B.5): every observation shares the entity name, the word
"service" and the two context words with the question (>= 2 shared content tokens question<->record; >= 3 between agreeing
records); no template uses a negation word (not/no/never/longer) or a department-name word, and decoy/filler text shares
at most one content token with any question (tests/test_world.py checks this with the provider's own tokenizer).
"""
from __future__ import annotations

from .schema import TemplateBank

BANK = TemplateBank(
    name="dev",
    service_prefixes=("parcel", "ledger", "quote", "manifest", "dock", "fleet", "cargo", "invoice", "tally", "claim", "route", "batch",
                      "yard", "pallet"),
    service_suffixes=("router", "bridge", "gate", "sync", "pulse", "link", "forge", "hub", "desk", "loom"),
    ctx_adjectives=tuple("""amber brisk cobalt dusty ember frosty gilded hollow ivory jagged lunar mossy nimble opal pewter quartz rusty sable
        tawny umber velvet waxen zesty arctic bronze cedar dapper elfin feral glacial hardy indigo jovial lofty mellow noble onyx plucky
        rugged timber upbeat vivid wicker young zinc agile bold dim eager fond grand hushed icy jolly lucid modest neat oaken proud quaint
        ruddy sunny tidy vast wiry zonal burly crisp deft fiery gaudy husky""".split()),
    ctx_nouns=tuple("""anchor beacon carton easel ferry gantry harbor island jetty kiln ladder magnet nozzle orchard pallet quay ramp spindle
        turbine valve wharf zipper abacus bobbin conveyor drum elevator funnel gasket hopper inkwell jig keel lever mast needle outpost
        pulley quill rudder sprocket trolley urn vessel winch axle bracket chute dolly engine flange girder hinge ingot jack knob lathe
        mallet nacelle oven piston rivet shuttle tether utensil vane anvil barrel capstan derrick eyelet""".split()),
    departments=(
        ("Warehouse Operations", "operations.supply-chain"), ("Carrier Integration", "operations.logistics"),
        ("Billing Reconciliation", "finance.billing"), ("Treasury Planning", "finance.forecasting"),
        ("Support Desk Americas", "customer-support.tickets"), ("Rapid Response Cell", "customer-support.escalations"),
        ("Platform Reliability", "infrastructure.reliability"), ("Release Engineering", "infrastructure.ci-cd"),
        ("Merchant Onboarding", "sales.accounts"), ("Contract Review", "legal.contracts"),
        ("Identity Security", "security.access-control"), ("Data Platform", "engineering.data-platform"),
    ),
    obs_templates=(
        "Triage note: the {ctx} we chase every week traces back to {svc}-service, which retried about {n} times an hour before it recovered.",
        "Going through last week's tickets, the {ctx} lines up with {svc}-service; the queue held roughly {n} stuck items each morning.",
        "Our team confirms {svc}-service is the origin of the {ctx}; every incident involved about {n} affected jobs.",
        "Post-incident remark: the {ctx} began right after {svc}-service changed its batching, and about {n} requests piled up per shift.",
        "Weekly digest: the {ctx} is tied to {svc}-service once more, with around {n} occurrences logged by the helpline.",
        "Customer case summary: {svc}-service explains the {ctx}; we counted about {n} affected accounts.",
        "On-call handoff: suspect {svc}-service for the {ctx}, since the backlog stayed near {n} entries.",
        "Retro item: the {ctx} comes from {svc}-service; we measured about {n} delayed orders per day.",
    ),
    decoy_templates=(
        "Housekeeping: certificate renewal for {svc}-service finished on schedule and {n} hosts were rotated.",
        "Calendar note: the {svc}-service maintenance window moved to the weekend, which affects {n} reviewers.",
        "License audit: {svc}-service seats were trimmed this quarter, saving {n} subscriptions.",
        "Welcome reminder: new analysts get read access to {svc}-service dashboards after {n} days.",
    ),
    filler_templates=(
        "Reminder: the quarterly offsite agenda needs a final review before Friday.",
        "Lunch order for the planning day is due by noon; vegetarian options are limited.",
        "The shared calendar for next month now shows the training sessions.",
        "Please submit expense receipts before the end of the week.",
        "Welcome to the new colleagues joining the group on Monday.",
        "The printer on the third floor has a fresh toner cartridge.",
        "Draft minutes from the last staff meeting are in the shared folder.",
        "Parking passes for the visitor lot can be requested at reception.",
        "Fire drill practice is planned for Thursday morning, please use the east stairs.",
        "The team photo is scheduled right after the all-hands gathering.",
    ),
    correction_templates=(
        "Correction after rechecking my own logs: the {ctx} is explained by {svc}-service, about {n} cases in the log.",
        "Update from the owner: I mislabeled this earlier; {svc}-service is the cause of the {ctx}, with about {n} cases.",
    ),
    question_templates=(
        "Which service sits behind the {ctx} that {a} and {b} flag?",
        "Both {a} and {b} mention a {ctx}. Which service stands under it?",
        "What service is responsible for the {ctx} seen by {a} together with {b}?",
        "Which service would you blame for the {ctx} that {a} and {b} complain about?",
    ),
    current_question_templates=(
        "Which service is currently behind the {ctx} that {a} and {b} flag?",
        "As of now, which service stands under the {ctx} seen in {a} and {b}?",
    ),
    goal_templates=(
        ("Cross-team check: {ctx}", "Find out which service explains the {ctx} raised across {a} and {b}."),
        ("Shared issue review: {ctx}", "Work out what is behind the {ctx} that {a} and {b} both see."),
    ),
    goal_only_templates=(
        ("Recurring blockers in {fam}", "Identify recurring operational blockers across the {fam} teams and what causes them."),
        ("Find the cause of repeat blockers in {fam}", "Look for blockers that recur between teams in {fam} and verify what is behind them."),
    ),
)

# goal-only tasks: the production loop asks "What recurring operational blockers related to <domain> have you recorded, and
# what caused them?" (models/fake.py draft_question), so these observations share "recurring", "blocker" and "caused"
# with it (>= 2 content tokens) as well as the entity and the context with each other.
GOAL_OBS_TEMPLATES = (
    "Recurring operational blocker in our area: the {ctx} is caused by {svc}-service, roughly {n} times a week.",
    "Blocker we recorded again: the {ctx}, caused by {svc}-service, about {n} cases this month.",
    "Recurring blocker for the team, caused by {svc}-service: the {ctx}, around {n} tickets so far.",
    "Operational blocker recorded this quarter: the {ctx} keeps coming back, caused by {svc}-service, about {n} times.",
)
