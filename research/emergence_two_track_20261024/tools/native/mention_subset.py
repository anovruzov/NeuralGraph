"""Is every record the lexical judge says mentions the question's entity also matched by the shipped entity retrieval?
If yes, no ranker (Tesseract, edges, multi-hop) can add a confirming record: confirms need mentions_entity=yes, and the
site always reads all matched records when they fit under L, and the ranker gets no room when they do not.
Every site of the seed-0 200-user world; every in-window record against fixed questions. Dev seed only."""
import sys, tempfile
from pathlib import Path
from research.e2e_ingest import emergence_world as ew
from mycelic.collective.edge.verify import lexical_judge, retrieve, judge_payload
n_q = int(sys.argv[1]) if len(sys.argv) > 1 else 60
w = ew.build_emergence_world(200, 0, Path(tempfile.mkdtemp()) / "w")
qs = ew.fixed_questions(w, n_q)
pairs = mism = yes_total = 0; examples = []
for sid, site in w.pipeline.sites.items():
    judge = lexical_judge(site.pack, site.canonicaliser)
    for q in qs:
        p, win = q["params"], q["window"]
        recs = site.store.window_records(win["start_week"], win["end_week"])
        matched, _ = retrieve(site.store, site.canonicaliser, entity_type=p["entity_type"], entity_id=p["entity_id"],
                              window=win, cap=max(1, len(recs)))
        m = {r.record_ref for r in matched}
        for r in recs:
            pairs += 1
            reply = judge(judge_payload(site.pack, q, r))
            if reply["mentions_entity"] == "yes":
                yes_total += 1
                if r.record_ref not in m:
                    mism += 1
                    if reply["describes_predicate"] == "yes": examples.append((sid, q["question_id"][:8]))
print(f"sites {len(w.pipeline.sites)} questions {len(qs)}: record-question pairs {pairs}, judge mentions=yes {yes_total}, "
      f"of which NOT retrieval-matched {mism}; of those also describes_predicate=yes (could confirm) {len(examples)}")
