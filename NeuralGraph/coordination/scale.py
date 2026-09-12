"""Scale and generalization sweep: does the mechanism hold beyond the 4-node pair?

The capability-survival benchmark fixes one capability shape -- two premises,
four nodes -- and varies the placement strategy.  That is enough to show the
mechanism exists, and not enough to show it is a property of the mechanism
rather than of that particular fixture.  Two objections follow naturally:

    Gate 4 (scale)          Does the advantage survive more premises, more
                            holders, more nodes?  Or does it wash out once the
                            system is big enough that something always
                            survives by accident?
    Gate 5 (generalization) Is "reconstruct a pair" special?  Does anything
                            change for a 3-, 5- or 8-premise capability?

This module answers both with the same generator, because they are the same
question asked along two axes of one fixture family:

    CapabilitySpec(slots=K, holders_per_slot=H, independent_roots=I)

K premises must all be recovered; each premise is held by H nodes; those H
holders draw from I distinct lineage roots.  I=1 is the correlated-replica case
(H copies, one root, the thing the paper argues is not redundancy); I>=2 means
at least one holder of each premise is genuinely independently rooted.

Everything runs through ``benchmark.run_cell`` -- the same coordinator, router,
repair planner and metrics as the headline matrix -- rather than a parallel
harness that could quietly drift from it.

Routing stays bounded, as it must for repair to be observable at all, but the
bound now scales with the capability: ``max_nodes = K + 1`` contacts one holder
per premise plus one spare.  Preference is interleaved (the first holder of
every premise, then the second of every premise, ...) so that a bounded route
covers all K premises at any K.  Sorted-by-name preference would group all
holders of premise 0 first and simply fail to reach premise K-1, which would
measure the naming scheme rather than the mechanism.

Verification budget is ample (every holder may be verified) on purpose.  A tight
budget confounds two different questions: whether independent support exists to
be found, and how efficiently a policy finds it.  With an ample budget, survival
answers the first and ``recovery_steps`` answers the second, and the policies
turn out to differ on both.

THE PREDICTION UNDER TEST, stated before the numbers: if lineage independence is
what buys survival, then survival should depend on I and be flat in K and H.
Replica count rising (H) with I=1 should buy nothing at any scale.  If instead
survival improved with H, the paper's central claim would be wrong.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .adapters import PrivateMemoryRecord
from .benchmark import (
    CAPABILITY_ID,
    DEFAULT_SEED,
    Placement,
    _scope_for,
    generate_symbols,
    run_cell,
    structural_diversity,
)

SCALE_VERSION = "scale-generalization-v1"

#: Interventions worth sweeping at scale.  ``node_failure`` is the control: it
#: removes one holder, so replication genuinely should help and any strategy
#: with a spare holder should survive.  The other two are the discriminating
#: ones, where correlated copies all fail together.
SCALE_INTERVENTIONS = ("node_failure", "lineage_root_failure", "authorization_revocation")

REPAIR_POLICIES = ("source_count", "lineage_aware")


@dataclass(frozen=True)
class CapabilitySpec:
    """One point in the fixture family."""

    slots: int
    holders_per_slot: int
    independent_roots: int

    def __post_init__(self) -> None:
        if self.slots < 2:
            raise ValueError("a reconstruction capability needs at least two premises")
        if self.holders_per_slot < 1:
            raise ValueError("each premise needs at least one holder")
        if not 1 <= self.independent_roots <= self.holders_per_slot:
            raise ValueError("independent_roots must be between 1 and holders_per_slot")

    @property
    def label(self) -> str:
        return f"K{self.slots}_H{self.holders_per_slot}_I{self.independent_roots}"

    @property
    def slot_names(self) -> tuple[str, ...]:
        return tuple(f"slot{index}" for index in range(self.slots))

    @property
    def node_count(self) -> int:
        return self.slots * self.holders_per_slot


def _slot_value(symbols, slot_index: int) -> str:
    """A distinct opaque value per premise, derived from the seeded symbols.

    Values must differ across premises so that a synthesizer cannot accidentally
    satisfy premise 3 with premise 1's answer and inflate survival.
    """
    base = symbols.left if slot_index % 2 == 0 else symbols.right
    return f"{base}_S{slot_index}"


def build_placement(spec: CapabilitySpec, seed: int, repair_policy: str) -> Placement:
    """Generate the placement for one (spec, repair policy) point.

    Holder ``j`` of premise ``i`` is rooted at ``R{i}_{j % I}``: the first I
    holders get distinct roots and any further holders are correlated copies of
    one of them.  So H grows replica count while I alone grows independence,
    which is exactly the separation the sweep needs.
    """
    symbols = generate_symbols(seed)
    records_by_node: dict[str, tuple[PrivateMemoryRecord, ...]] = {}
    for slot_index, slot in enumerate(spec.slot_names):
        value = _slot_value(symbols, slot_index)
        for holder in range(spec.holders_per_slot):
            root_index = holder % spec.independent_roots
            root = f"R{slot_index}_{root_index}"
            domain = f"FD{slot_index}_{root_index}"
            node_id = _node_id(slot_index, holder)
            memory_id = f"memory-{slot_index}-{holder}"
            records_by_node[node_id] = (PrivateMemoryRecord(
                memory_id=memory_id,
                capability_id=CAPABILITY_ID,
                slot=slot,
                value=value,
                confidence=round(0.95 - 0.01 * holder, 6),
                source_ids=(f"source-{slot_index}-{root_index}",),
                parent_memory_ids=(f"parent-{memory_id}",),
                lineage_root_ids=(root,),
                failure_domains=(domain,),
                edge_path=(f"edge-{node_id}",),
                required_scope=_scope_for(root),
            ),)
    return Placement(
        name=f"{spec.label}_{repair_policy}",
        description=(
            f"{spec.slots} premises, {spec.holders_per_slot} holders each, "
            f"{spec.independent_roots} independent root(s) per premise, "
            f"{repair_policy} repair"
        ),
        records_by_node=records_by_node,
        repair_policy=repair_policy,
        # One holder per premise, plus one spare. Bounded, and scales with the
        # capability instead of being pinned at the two-slot fixture's 3.
        max_nodes=spec.slots + 1,
        coordinates=True,
        slots=spec.slot_names,
        # Ample: every holder may be verified. This deliberately separates two
        # questions a tight budget confounds. With an ample budget, survival
        # measures whether independent support is *findable at all*, and
        # ``recovery_steps`` measures how efficiently a policy finds it -- which
        # is where the policies actually differ. A budget fixed at K while the
        # uncontacted pool grows as K*(H-1) would report the budget choice as a
        # scaling limit of the mechanism, which it is not.
        max_verifications=spec.node_count,
    )


def _node_id(slot_index: int, holder: int) -> str:
    return f"node-s{slot_index:02d}-h{holder:02d}"


def route_preference(spec: CapabilitySpec) -> tuple[str, ...]:
    """Interleaved preference: first holder of every premise, then the second, ...

    A bounded route must be able to cover all K premises, or the baseline fails
    for reasons that have nothing to do with the intervention under test.
    """
    return tuple(
        _node_id(slot_index, holder)
        for holder in range(spec.holders_per_slot)
        for slot_index in range(spec.slots)
    )


async def run_point(
    spec: CapabilitySpec,
    repair_policy: str,
    intervention: str,
    seed: int = DEFAULT_SEED,
) -> dict[str, Any]:
    """One (spec, policy, intervention) point, through the standard machinery."""
    placement = build_placement(spec, seed, repair_policy)
    cell = await run_cell(
        seed,
        placement.name,
        intervention,
        placement=placement,
        preferred_node_ids=route_preference(spec),
    )
    diversity = structural_diversity(placement)
    return {
        "spec": {
            "slots": spec.slots,
            "holders_per_slot": spec.holders_per_slot,
            "independent_roots": spec.independent_roots,
            "label": spec.label,
            "node_count": spec.node_count,
        },
        "repair_policy": repair_policy,
        "intervention": intervention,
        "baseline_correct": cell["baseline_correct"],
        "capability_survival": cell["capability_survival"],
        "unrecovered_silent_forgetting": cell["unrecovered_silent_forgetting"],
        "recovery_steps": cell["recovery_steps"],
        "bytes_moved": cell["bytes_moved"],
        "record_count": cell["record_count"],
        "structural_min_cut": diversity["structural_min_cut"],
        "route_size": len(cell["phases"]["before_intervention"]["route"]),
    }


DEFAULT_SPECS = tuple(
    CapabilitySpec(slots=k, holders_per_slot=h, independent_roots=i)
    for k in (2, 3, 5, 8)
    for h in (2, 3)
    for i in (1, 2)
)


async def run_scale_sweep(seed: int = DEFAULT_SEED, specs=DEFAULT_SPECS) -> dict[str, Any]:
    points: list[dict[str, Any]] = []
    for spec in specs:
        for policy in REPAIR_POLICIES:
            for intervention in SCALE_INTERVENTIONS:
                points.append(await run_point(spec, policy, intervention, seed))

    # Does survival depend on independence, and only on independence?
    by_independence: dict[str, dict[str, Any]] = {}
    for policy in REPAIR_POLICIES:
        for intervention in SCALE_INTERVENTIONS:
            for independent in (1, 2):
                rows = [
                    point for point in points
                    if point["repair_policy"] == policy
                    and point["intervention"] == intervention
                    and point["spec"]["independent_roots"] == independent
                ]
                key = f"{policy}::{intervention}::I{independent}"
                by_independence[key] = {
                    "points": len(rows),
                    "survival_rate": round(
                        sum(1 for row in rows if row["capability_survival"]) / len(rows), 6
                    ),
                    "invariant_across_scale": len(
                        {row["capability_survival"] for row in rows}
                    ) == 1,
                    "mean_recovery_steps": round(
                        sum(row["recovery_steps"] for row in rows) / len(rows), 6
                    ),
                    "max_recovery_steps": max(row["recovery_steps"] for row in rows),
                }

    # Does raising replica count with correlated lineage buy anything?
    replication_effect = {}
    for policy in REPAIR_POLICIES:
        for holders in (2, 3):
            rows = [
                point for point in points
                if point["repair_policy"] == policy
                and point["spec"]["holders_per_slot"] == holders
                and point["spec"]["independent_roots"] == 1
                and point["intervention"] == "lineage_root_failure"
            ]
            replication_effect[f"{policy}::H{holders}::I1"] = round(
                sum(1 for row in rows if row["capability_survival"]) / len(rows), 6
            )

    cost_by_arity = {}
    for slots in sorted({point["spec"]["slots"] for point in points}):
        rows = [point for point in points if point["spec"]["slots"] == slots]
        cost_by_arity[f"K{slots}"] = {
            "mean_bytes_moved": round(sum(r["bytes_moved"] for r in rows) / len(rows), 6),
            "mean_record_count": round(sum(r["record_count"] for r in rows) / len(rows), 6),
            "mean_route_size": round(sum(r["route_size"] for r in rows) / len(rows), 6),
            "all_baselines_correct": all(r["baseline_correct"] for r in rows),
        }

    return {
        "schema_version": "1.0",
        "scale_version": SCALE_VERSION,
        "seed": seed,
        "interventions": list(SCALE_INTERVENTIONS),
        "repair_policies": list(REPAIR_POLICIES),
        "specs": [spec.label for spec in specs],
        "survival_by_independence": by_independence,
        "replication_effect_under_correlated_lineage": replication_effect,
        "cost_by_arity": cost_by_arity,
        "points": points,
    }


def render_markdown(result: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append(f"# Scale and generalization sweep (seed {result['seed']})")
    lines.append("")
    lines.append(
        f"`{result['scale_version']}` · {len(result['specs'])} capability shapes "
        f"× {len(result['repair_policies'])} repair policies "
        f"× {len(result['interventions'])} interventions "
        f"= {len(result['points'])} points"
    )
    lines.append("")
    lines.append("## Survival depends on independence, not on scale")
    lines.append("")
    lines.append("| repair policy | intervention | independent roots | survival | "
                 "invariant across K and H | mean verifications |")
    lines.append("|---|---|---|---|---|---|")
    for key in sorted(result["survival_by_independence"]):
        policy, intervention, independence = key.split("::")
        row = result["survival_by_independence"][key]
        flat = "yes" if row["invariant_across_scale"] else "**no**"
        lines.append(
            f"| `{policy}` | `{intervention}` | {independence} | "
            f"{row['survival_rate']:.2f} | {flat} | {row['mean_recovery_steps']:.1f} |"
        )
    lines.append("")
    lines.append("## Raising replica count under correlated lineage")
    lines.append("")
    lines.append("| policy · holders per premise · 1 root | survival vs lineage-root failure |")
    lines.append("|---|---|")
    for key in sorted(result["replication_effect_under_correlated_lineage"]):
        lines.append(f"| `{key}` | {result['replication_effect_under_correlated_lineage'][key]:.2f} |")
    lines.append("")
    lines.append("## Cost grows with capability arity")
    lines.append("")
    lines.append("| premises | mean bytes moved | mean records | mean route size | baselines correct |")
    lines.append("|---|---|---|---|---|")
    for key in sorted(result["cost_by_arity"], key=lambda k: int(k[1:])):
        row = result["cost_by_arity"][key]
        lines.append(
            f"| {key[1:]} | {row['mean_bytes_moved']:.0f} | {row['mean_record_count']:.1f} | "
            f"{row['mean_route_size']:.1f} | {'yes' if row['all_baselines_correct'] else 'NO'} |"
        )
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    args = parser.parse_args(argv)
    result = asyncio.run(run_scale_sweep(args.seed))
    payload = (
        render_markdown(result) if args.format == "markdown"
        else json.dumps(result, sort_keys=True, indent=2) + "\n"
    )
    if args.output:
        args.output.write_text(payload, encoding="utf-8")
    else:
        print(payload, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
