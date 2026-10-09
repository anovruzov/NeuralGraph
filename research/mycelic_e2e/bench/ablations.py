"""Harness-only ablations (PLAN_v1 §B.8; BENCHMARK_CONTRACT §8). Each ablation removes ONE mechanism by replacing a function in the
imported ``mycelic`` modules, in-process, before the runtime is built. Nothing here is a production setting or flag: a process that
never calls :func:`apply` runs the unmodified code.

Names follow the ledger (``ledger.EXPECTED_GATE_FAILURES`` keys): the manifest records ``ablation`` = the short id (``A1`` ...) and
``ablation_name`` = the long name (``A1_ranker_off`` ...); the command line accepts either.

A function imported with ``from .x import f`` is bound by name in every importing module, so replacing ``x.f`` alone would leave the
copies in place. :func:`_rebind` therefore replaces every ``mycelic.*`` module attribute that IS the original function.

====== ====================== ====================================================== ==========================================
id     name                   what is replaced                                       expected gate failure (ledger)
====== ====================== ====================================================== ==========================================
A1     A1_ranker_off          ``inquiry.routing.rank_holders``: first N authorized   none (the G4 ranker assertion is skipped)
                              candidates, ``rank_method`` = ``ablated``
A2     A2_roots_off           ``knowledge.support.compute_support`` / ``root_groups``  none
                              count references, not source roots
A3     A3_verification_off    ``LoopEngine._ask_verification`` raises ValueError      none
                              (every caller already catches it: no verification
                              question is created; returning None would crash the
                              callers that index the result)
A4     A4_index_off           ``knowledge.hypergraph.upsert_entity_index_sync`` and  G8 (the G4 ranker assertion is skipped)
                              ``upsert_term_index_sync``, the two sinks every index
                              write goes through (``OrgService.holder_heartbeat`` and
                              the revoke / opt-out withdrawal): both do nothing and
                              return 0, so the entity and term indexes stay empty
                              whichever feeder delivers the beat (the embedded
                              callback, the raw transport beat, the shutdown beat);
                              counters ``entity_index_calls``, ``term_index_calls``
                              and ``entities_suppressed``, ``terms_suppressed`` (ids
                              the real function would have been asked to publish)
                              go to the manifest as ``ablation_calls``; every
                              route is then domain-ranked, so ``report.finalize``
                              does not expect a hypergraph ranker (as under A1)
A5     A5_authz_routing_off   ``Authorizer._can_route``, the one function every      G4 (proves the gate is load-bearing)
                              routing decision goes through (``can_route``,
                              ``can_route_many``, ``candidate_holders``,
                              ``domains_for_goal`` all call it): any non-revoked
                              holder of the tenant; counters ``can_route_calls``
                              and ``loosened`` (calls the real rule would have
                              refused) go to the manifest as ``ablation_calls``
A6     A6_dedupe_off          ``IngestPipeline.dedupe`` always returns ``new``        G10
====== ====================== ====================================================== ==========================================
"""
from __future__ import annotations

import sys
from typing import Any, Callable

NAMES: dict[str, str] = {"A1": "A1_ranker_off", "A2": "A2_roots_off", "A3": "A3_verification_off", "A4": "A4_index_off",
                         "A5": "A5_authz_routing_off", "A6": "A6_dedupe_off"}


def normalize(name: str | None) -> tuple[str | None, str | None]:
    """``(short id, long name)`` for ``A1`` / ``a1`` / ``A1_ranker_off``; ``(None, None)`` for none."""
    if name in (None, "", "none", "None", "full"):
        return None, None
    key = str(name).strip()
    short = key.split("_", 1)[0].upper()
    if short not in NAMES or (("_" in key) and key.lower() != NAMES[short].lower()):
        raise ValueError(f"unknown ablation {name!r}; choose one of {sorted(NAMES.values())}")
    return short, NAMES[short]


class Patch:
    """The set of replacements one ablation made; ``restore()`` puts every original back (tests; run.py never calls it)."""

    def __init__(self, short: str) -> None:
        self.short, self.name = short, NAMES[short]
        self._undo: list[Callable[[], None]] = []
        self.bound: list[str] = []
        self.counters: dict[str, int] = {}              # evidence that an ablation was live: how often its replacement ran (run_manifest.json ``ablation_calls``)

    def rebind(self, original: Any, replacement: Any) -> int:
        """Replace every ``mycelic.*`` module attribute that is ``original``."""
        n = 0
        for mname, mod in list(sys.modules.items()):
            if mod is None or not (mname == "mycelic" or mname.startswith("mycelic.")):
                continue
            for attr, val in list(vars(mod).items()):
                if val is original:
                    setattr(mod, attr, replacement)
                    self._undo.append(lambda m=mod, a=attr, o=original: setattr(m, a, o))
                    self.bound.append(f"{mname}.{attr}")
                    n += 1
        if n == 0:
            raise RuntimeError(f"ablation {self.name}: the target function is not bound anywhere (code moved?)")
        return n

    def setattr(self, owner: Any, attr: str, replacement: Any, label: str) -> None:
        original = getattr(owner, attr)
        setattr(owner, attr, replacement)
        self._undo.append(lambda o=owner, a=attr, v=original: setattr(o, a, v))
        self.bound.append(label)

    def restore(self) -> None:
        while self._undo:
            self._undo.pop()()
        self.bound = []

    def describe(self) -> dict[str, Any]:
        return {"ablation": self.short, "ablation_name": self.name, "patched": sorted(self.bound), "calls": dict(self.counters)}


def apply(name: str) -> Patch:
    """Install one ablation in this process. Call BEFORE ``build_runtime``."""
    short, _ = normalize(name)
    if short is None:
        raise ValueError("no ablation named")
    p = Patch(short)
    {"A1": _a1, "A2": _a2, "A3": _a3, "A4": _a4, "A5": _a5, "A6": _a6}[short](p)
    return p


# ------------------------------------------------------------------------------------------------ A1
def _a1(p: Patch) -> None:
    from mycelic.inquiry import routing

    def rank_holders_off(db: Any, authz: Any, org: Any, question: dict[str, Any], candidates: list[dict[str, Any]], *, support_edge: dict[str, Any] | None,
                         max_holders: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Identity ordering: the first ``max_holders`` authorized candidates in registry order (what routing did before the ranker)."""
        return list(candidates)[:max(1, int(max_holders))], {"rank_method": "ablated", "scores": {}}
    p.rebind(routing.rank_holders, rank_holders_off)


# ------------------------------------------------------------------------------------------------ A2
def _a2(p: Patch) -> None:
    from mycelic.knowledge import support

    orig_cs, orig_rg = support.compute_support, support.root_groups

    def compute_support_refs(refs: Any, *, roles: Any = ("supports",)) -> dict[str, Any]:
        """Same summary, but ``independent_roots`` counts the active supporting references (a copy counts as independent)."""
        res = dict(orig_cs(refs, roles=roles))
        res["independent_roots"] = sum(int(r.get("ref_count") or 0) for r in res.get("roots") or [])
        res["copied_refs"] = 0
        return res

    def root_groups_refs(refs: Any, *, roles: Any = ("supports",)) -> dict[str, list[Any]]:
        wanted = set(roles)
        return {str(r["ref_id"]): [r] for r in refs if (r.get("role") or "supports") in wanted and support.is_active(r)
                and r.get("root_known") and r.get("source_root_id")}
    p.rebind(orig_cs, compute_support_refs)
    p.rebind(orig_rg, root_groups_refs)


# ------------------------------------------------------------------------------------------------ A3
def _a3(p: Patch) -> None:
    from mycelic.discovery.engine import LoopEngine

    async def ask_verification_off(self: Any, *args: Any, **kwargs: Any) -> None:
        raise ValueError("verification questions are disabled (harness ablation A3_verification_off)")
    p.setattr(LoopEngine, "_ask_verification", ask_verification_off, "mycelic.discovery.engine.LoopEngine._ask_verification")


# ------------------------------------------------------------------------------------------------ A4
def _a4(p: Patch) -> None:
    """Replace the two index sinks, not a feeder. ``OrgService.holder_heartbeat`` has three feeders (the embedded callback through
    ``holder.embedded._heartbeat_stats``, the raw transport beat applied by ``LoopEngine._on_transport``, and the shutdown beat), and the first
    A4 patched only the first: the transport beat re-added the terms every minute and the stop-time beat refilled them, so G8 read a full term
    index (dev run C6abl-A4-S1: 112/112). Every write to ``term_index`` and to the ``entity_index`` hyperedges goes through
    ``hypergraph.upsert_term_index_sync`` / ``upsert_entity_index_sync`` (``org.py`` reaches them as ``hg.<name>``, a module attribute looked up
    at call time; ``withdraw_holder_entities_sync`` calls the entity one by its module global), so replacing them keeps both indexes empty
    for the whole run. Both return 0 ("rows changed"), the value the callers test before writing an audit row, so no ``holder.entities_published``
    or ``holder.terms_published`` row appears either. The holder's own reported stats (``entities_reported`` / ``terms_reported``) are left
    alone: the holder still reports what it has, the coordinator indexes none of it."""
    from mycelic.entities import sanitize_term_counts
    from mycelic.knowledge import hypergraph as hg

    p.counters.update(entity_index_calls=0, term_index_calls=0, entities_suppressed=0, terms_suppressed=0)

    def upsert_entity_index_off(c: Any, *, tenant_id: str, holder_id: str, unit_id: str | None, entities: dict[str, int], domains: list[str], now: str,
                                retract: bool = True, withdraw_kinds: tuple[str, ...] = ()) -> int:
        p.counters["entity_index_calls"] += 1
        p.counters["entities_suppressed"] += len(hg.sanitize_entity_counts(entities))
        return 0

    def upsert_term_index_off(c: Any, *, tenant_id: str, holder_id: str, terms: dict[str, int], now: str, retract: bool = True) -> int:
        p.counters["term_index_calls"] += 1
        p.counters["terms_suppressed"] += len(sanitize_term_counts(terms))
        return 0
    p.rebind(hg.upsert_entity_index_sync, upsert_entity_index_off)
    p.rebind(hg.upsert_term_index_sync, upsert_term_index_off)


# ------------------------------------------------------------------------------------------------ A5
def _a5(p: Patch) -> None:
    """Replace ``Authorizer._can_route``, the single choke point (same signature, same ``(allowed, reason)`` result). ``can_route`` and
    ``can_route_many`` call it, and so do ``QuestionService.candidate_holders`` and ``LoopEngine.domains_for_goal`` (which go straight to
    it with a shared context), so patching ``can_route`` alone left the routing filter running unablated (dev run C7abl-A5-S1: 120/120)."""
    from mycelic.authz import Authorizer

    original = Authorizer._can_route
    p.counters.update(can_route_calls=0, loosened=0)

    def can_route_tenant_only(self: Any, question: Any, holder: Any, ctx: Any) -> tuple[bool, str]:
        p.counters["can_route_calls"] += 1
        if holder.get("tenant_id") != question.get("tenant_id"):
            return False, "tenant mismatch"
        if holder.get("status") == "revoked":
            return False, "holder revoked"
        if not original(self, question, holder, ctx)[0]:
            p.counters["loosened"] += 1                  # the real rule would have refused this holder: the ablation changed the outcome
        return True, "ok (ablation A5: no scope, domain, answer-scope or asker check)"
    p.setattr(Authorizer, "_can_route", can_route_tenant_only, "mycelic.authz.Authorizer._can_route")


# ------------------------------------------------------------------------------------------------ A6
def _a6(p: Patch) -> None:
    from mycelic.ingest.pipeline import IngestPipeline

    def dedupe_off(self: Any, ev: Any) -> str:
        return "new"
    p.setattr(IngestPipeline, "dedupe", dedupe_off, "mycelic.ingest.pipeline.IngestPipeline.dedupe")
