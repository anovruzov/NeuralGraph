"""DEV template bank (surface text only; no gold, no ids). Disjoint from the sealed holdout bank (tested).

Lexical contract with the deterministic provider (PLAN_v1 §B.5, hygiene.py):
* target records share the entity name, the word "service" and the two context words with the question (>= 3 shared content
  tokens) and no negation word;
* records of DIFFERENT patterns share < 3 stemmed content tokens: every observation / decoy / correction template carries at most
  ONE boilerplate content word besides the entity, the context and the number (all other words are stopwords of the provider's
  tokenizer), so even the same template used by two patterns shares at most {that word, "service"} = 2 tokens;
* question wording shares at most one content token with any record about another context.
"""
from __future__ import annotations

from .schema import TemplateBank

BANK = TemplateBank(
    name="dev",
    service_prefixes=("parcel", "ledger", "quote", "manifest", "dock", "fleet", "cargo", "invoice", "tally", "claim", "route", "batch",
                      "yard", "pallet", "freight", "crate", "depot", "lorry", "voucher", "tariff"),
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
        "The {ctx} is from {svc}-service, {n}.",
        "{svc}-service is the source of the {ctx}, {n}.",
        "We trace the {ctx} to {svc}-service, {n}.",
        "{svc}-service: origin of the {ctx}, {n}.",
        "The {ctx} is {svc}-service, as {n} show.",
        "{svc}-service, then, for the {ctx}: {n}.",
        "It is {svc}-service at the root of the {ctx}, {n}.",
        "Blame {svc}-service for the {ctx}, {n}.",
    ),
    decoy_templates=(
        "Renewal for {svc}-service, {n}.",
        "Window for {svc}-service, {n}.",
        "Seats of {svc}-service, {n}.",
        "Dashboards of {svc}-service, {n}.",
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
        "Correction: it is {svc}-service for the {ctx}, {n}.",
        "Edit: the {ctx} is {svc}-service, {n}.",
    ),
    question_templates=(
        "Which service sits behind the {ctx} that {a} and {b} flag?",
        "Both {a} and {b} mention a {ctx}. Which service stands under it?",
        "What service is responsible for the {ctx} seen by {a} together with {b}?",
        "Which service would you hold accountable for the {ctx} that {a} and {b} complain about?",
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
        ("Recurring blockers in {fam}", "Identify recurring operational blockers across the {fam} teams, for instance the {ctx}, and what causes them."),
        ("Find the cause of repeat blockers in {fam}", "Look for blockers that recur between teams in {fam}, such as the {ctx}, and verify what is behind them."),
    ),
    goal_decoy_templates=(
        "Operational, caused by {svc}-service: the {ctx}, {n}.",
        "Caused by {svc}-service, operational: {ctx}, {n}.",
        "The {ctx}, operational and caused by {svc}-service, {n}.",
        "{svc}-service caused it: operational {ctx}, {n}.",
    ),
)

# goal-only tasks: the production loop asks "What recurring operational blockers related to <domain> have you recorded, and what
# caused them?" (models/fake.py draft_question). The hidden pattern's records share exactly "recurring" and "blocker" with it
# (>= 2 content tokens); the in-scope decoy's records (goal_decoy_templates) share exactly "operational" and "caused" instead, so
# the two patterns never cluster with each other (< 3 shared tokens) yet both are retrieved by the loop's question.
GOAL_OBS_TEMPLATES = (
    "Recurring blocker: the {ctx}, {svc}-service, {n}.",
    "Blocker, recurring: {svc}-service for the {ctx}, {n}.",
    "Recurring blocker at {svc}-service: {ctx}, {n}.",
    "A recurring blocker: {ctx} from {svc}-service, {n}.",
)
