"""Decode microbenchmark against a running llama-server: k concurrent
requests, fixed 64 decoded tokens, with or without grammar (record of the
container benchmark; see research/mycelic/live/RESULTS.md).

    python3 research/mycelic/artifacts/live/bench/decode_microbench.py URL 1,8 g,n
variants: g = edge grammar, c = cheap index grammar, n = no grammar,
ie = ignore_eos; suffix s = top_k-only sampler chain."""
import json, sys, time, urllib.request
from concurrent.futures import ThreadPoolExecutor
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), *[".."] * 5)))
from research.mycelic.runner import build_world
from research.mycelic.live.notes import NoteStore
from research.mycelic.live import agents as A
url = sys.argv[1]
w = build_world(400, 700); st = NoteStore(w.corpus)
ags = st.agents()
def req(i, gram, n=64):
    notes = st.notes(ags[i])[:20]
    body = {"messages": [{"role": "system", "content": A.SYSTEM_PROMPT},
                         {"role": "user", "content": A.user_message(notes)}],
            "max_tokens": n, "temperature": 0.0, "top_k": 1, "cache_prompt": True,
            "chat_template_kwargs": {"enable_thinking": False}}
    if gram.startswith("g"): body["grammar"] = A.grammar(len(notes))
    if gram.startswith("c"):
        g = A.grammar(len(notes)).split("\n")
        g[2] = 'idx ::= [0-9] | [1-9] [0-9] | [1-9] [0-9] [0-9]'
        body["grammar"] = "\n".join(g)
    if gram.endswith("s"): body["samplers"] = ["top_k"]
    if gram == "ie": body["ignore_eos"] = True
    r = urllib.request.urlopen(urllib.request.Request(url + "/v1/chat/completions", json.dumps(body).encode(), {"Content-Type": "application/json"}), timeout=900)
    d = json.loads(r.read()); t = d["timings"]
    return t["predicted_n"], t["predicted_ms"], t["prompt_n"], t["prompt_ms"]
for k in [int(x) for x in sys.argv[2].split(",")]:
    for gram in sys.argv[3].split(","):
        # warm prompts (prefill) first so the measured run is decode only
        with ThreadPoolExecutor(k) as ex: list(ex.map(lambda i: req(i, gram, 1), range(k)))
        t0 = time.time()
        with ThreadPoolExecutor(k) as ex: res = list(ex.map(lambda i: req(i, gram), range(k)))
        wall = time.time() - t0
        n = sum(r[0] for r in res); pms = sum(r[3] for r in res)
        print(f"k={k} {gram}: wall {wall:.1f}s, {n} tok, agg {n/wall:.1f} tok/s, per-stream {sum(r[0]/(r[1]/1000) for r in res)/k:.2f} tok/s, prefill tok {sum(r[2] for r in res)}", flush=True)
