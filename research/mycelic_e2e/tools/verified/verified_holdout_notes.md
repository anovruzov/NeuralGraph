**The holdout system run is not a valid full-system result.** Its architecture gate failed **G7**, so under contract §8 the
scorer's 120/120 is **not** published as full-system accuracy. The row above shows what the scorer computed, labelled with
the gate verdict.

**What G7 found.**
- Three claims in the asker views (tasks holdout-033, -036, -074) were `hypothesis` both in the view and in the final
  coordinator database, at version 1 in both. Their independent-root counts grew after the view was captured (5→12, 5→6,
  2→8), with no version bump or revision row to record the change.
- The writer is `KnowledgeService.sync_support_sync` (`mycelic/knowledge/service.py:537`), added tonight with the hypergraph
  support invariant. It updates `claims.support` in place.
- G7 did what it is for: it found an audit-trail defect in the product. Hypothesis claims are never extracted (only
  `supported` claims count), so no scored answer depended on these three. The verdict does not depend on that.
- The holdout is not re-run (one shot per candidate), and no candidate change followed it.

**The rest of the evidence for the run.**
- **Every other gate passed:** G1–G6, G8–G10.
  - Connector provenance of all 6,325 ingested records.
  - One store per holder.
  - Support and roots consistent with the hypergraph.
  - All 19,921 routes authorized in the as-of replay.
  - Every claim with gate and revision rows; truthful provider label.
  - Hypergraph present; lineage resolvable.
  - Every injected fault disposed.
- **Disclosures:** 0. Every visible reference was raw-checked.
- **API errors:** 0.
- **Support quality:** independent-support counts correct on 72/78 positives; lineage exact on 72/78.
- **Run shape:** 1,120 holders created, 1,117 with records, 555 routed to, 488 activated; 2,030 questions; 2,824 s; peak RSS
  2.4 GB.
- **Comparison on the same holdout world:** the primary centralized baseline (`source`) scores 114/120 = 0.950
  [0.895, 0.977], missing 5 cross-department tasks and 1 fault task. The `single` variant scores 70/120.
- **The provider caveat applies throughout:** deterministic provider, single process.
