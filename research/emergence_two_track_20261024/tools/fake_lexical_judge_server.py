"""PLUMBING ONLY -- NOT INFERENCE. An OpenAI-compatible fake server whose replies are the shipped lexical judge's
verdicts on each judge_record payload. Used to prove that --routing sends the real run's record judgments over HTTP
through the site runtime and back into the gate unchanged. Every reply carries X-Mycelic-Fake: 1.

    python3.11 fake_lexical_judge_server.py --seed 0 --routing-out FILE
"""
import argparse, json, signal, sys, time
from pathlib import Path

from mycelic.collective.packs.canonical import Canonicaliser
from mycelic.collective.edge.verify import lexical_judge
from mycelic.collective.inference.fakeserver import FakeOpenAIServer, request_payload
from mycelic.collective.packs.loader import load_pack
from research.e2e_ingest import emergence_world as ew

ap = argparse.ArgumentParser()
ap.add_argument("--seed", type=int, default=0)
ap.add_argument("--routing-out", required=True)
a = ap.parse_args()
org, corpus, _ = ew.generate(200, a.seed)
union: dict[str, set] = {}
for per_site in ew.master_data(corpus).values():
    for t, ids in per_site.items():
        union.setdefault(t, set()).update(ids)
pack = load_pack(str(ew.PACK_DIR))
judge = lexical_judge(pack, Canonicaliser(pack, known={t: sorted(v) for t, v in union.items()}))
calls = {"n": 0}


def responder(req):
    calls["n"] += 1
    return judge(request_payload(req))


server = FakeOpenAIServer("valid", responder=responder).start()
out = Path(a.routing_out); out.mkdir(parents=True, exist_ok=True)
for sid in ew.site_ids(org):   # one file per site, bound to exactly that site
    (out / f"{sid}.routing.json").write_text(json.dumps({
        "schema_version": 1,
        "endpoints": {"fake-lexical": {"provider": "openai_compat", "boundary": f"site:{sid}",
                                       "base_url": server.base_url, "model": "fake-lexical-judge"}},
        "routes": {"judge_record": {"endpoint": "fake-lexical"}}}, indent=1))
print("listening", server.base_url, flush=True)
signal.signal(signal.SIGTERM, lambda *_: (print("calls", calls["n"], flush=True), sys.exit(0)))
while True:
    time.sleep(1)
