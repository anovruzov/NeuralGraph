# Analysis: ablation A5 (authorized routing off) vs. architecture gate G4

Analyst: ANALYST-2 (`claude-sonnet-5-5`), read-only, dev runs only (C7-S1 vs C7abl-A5-S1, code `7d2e63b`, S/1). Written
2026-10-09 ~10:18 UTC and transcribed by the orchestrator. Counts come from each run's coord.db, opened read-only, plus the real,
unpatched `Authorizer` run on a backup copy.

## Summary
1. **A5 patched the wrong symbol.** It replaces `Authorizer.can_route`. The routing path calls `Authorizer._can_route`
   directly:
   - `inquiry/service.py:395` `candidate_holders`;
   - `can_route_many` (`authz.py:506-510`), used by `discovery/engine.py:321` `domains_for_goal`.

   The patch was live (`ablation_patches` = `mycelic.authz.Authorizer.can_route`; all 25,004 `response.accept` audit rows read
   "ok (ablation A5 ...)"). But it only loosened redundant use-time rechecks (`service.py:554`, `599`; `engine.py:1174`; …).
2. **C7-S1 and A5 route the same way.**
   - 9.87 holders per question in both runs.
   - The authorized pool averages ~25.5 of each tenant's 68 holders in both.
   - The question.route audit rows still list scope and domain rejections at the same rates (A5: 23,082 "owner outside the
     question's scope", against 24,300 in C7-S1).
   - Of the 110 task root questions, 101 differ in 1-2 of their 10 holders. That is tie-break noise from per-run random
     `holder_id`s, not an A5 effect.
3. **G4's replay is genuine and could have caught a leak.**
   - `arch_gate.py:701` `_replay_can_route` runs the real `can_route` in the separate gate process (report.py never imports
     ablations) against a backup of coord.db, with `published_domains` rewound to route time.
   - ANALYST-2's own replay in a fresh process: 25,004 checked, 0 denied. A check that also passes the asker, for the 1,100
     user-asked routes: 0 denied.
   - The org is static in this world (0 revocations), so the final state equals the as-of state.
4. **Fix: patch `Authorizer._can_route`**, the single choke point, and record how often the replacement is reached on the route
   path, so an ablation that is never reached is visible.
   - Rough counterfactual: ranking 300 sampled A5 questions over the whole tenant pool put 62 of 3,000 picks (2.1 %) outside
     the real `can_route`. With term ids off (no secret key), this is approximate.
   - That would mean hundreds of G4 replay denials over ~25k routes.
   - Accuracy may still stay high, because holder-side `_policy_reason` (`evidence/service.py:1406`) and the claim audience
     filters sit downstream.
5. **The `denied` task class does not test routing reach.**
   - Its hidden holders sit inside the asker's scope, and the denial is at record level (`vis == "members"`,
     `world.py:856-872`).
   - For example, dev-105 in A5 routed to 2 hidden holders, both legitimately authorized by `can_route`.
   - To make routing leaks matter for accuracy, the world needs tasks whose only evidence holder is outside the asker's scope
     closure and whose domain matches the question.
