#!/usr/bin/env python3
"""Throughput probe for a llama-server: ~300-token prompt, 60-token answer, N concurrent requests.
Each request has a distinct prompt (no prefix-cache reuse between slots). Prints server-side timings per request."""
import json, os, random, sys, threading, time, urllib.request

URL = sys.argv[1]; MODEL = sys.argv[2]; CONC = [int(x) for x in sys.argv[3].split(",")]; REPS = int(sys.argv[4]) if len(sys.argv) > 4 else 2
NW = int(os.environ.get("NW", "70"))
WORDS = ("ledger invoice shipment vendor audit renewal contract latency queue deploy rollback incident owner budget "
         "forecast retention payroll compliance schema migration cluster tenant quota backlog handoff roadmap").split()

def prompt(seed):
    r = random.Random(seed)
    body = " ".join(r.choice(WORDS) + str(r.randint(1, 999)) for _ in range(NW))
    return ("Here are some notes with unique tags:\n" + body +
            "\n\nWrite a detailed paragraph summarising the themes in these notes. Keep writing until you are stopped.")

def call(seed, out, i):
    body = {"model": MODEL, "messages": [{"role": "user", "content": prompt(seed)}], "max_tokens": 60, "temperature": 0,
            "chat_template_kwargs": {"enable_thinking": False}}
    t0 = time.time()
    req = urllib.request.Request(URL + "/v1/chat/completions", json.dumps(body).encode(), {"content-type": "application/json"})
    d = json.load(urllib.request.urlopen(req, timeout=600))
    out[i] = {"wall_s": round(time.time() - t0, 2), "prompt_tokens": d["usage"]["prompt_tokens"], "completion_tokens": d["usage"]["completion_tokens"],
              "prefill_tok_s": round(d["timings"]["prompt_per_second"], 2), "decode_tok_s": round(d["timings"]["predicted_per_second"], 2),
              "finish": d["choices"][0]["finish_reason"]}

res = []
for n in CONC:
    for rep in range(REPS):
        out = [None] * n; ths = []
        la0 = os.getloadavg()[0]
        t0 = time.time()
        for i in range(n):
            th = threading.Thread(target=call, args=(1000 * n + 10 * rep + i + random.randint(0, 10**6), out, i)); th.start(); ths.append(th)
        for th in ths: th.join()
        wall = time.time() - t0
        agg = sum(o["completion_tokens"] for o in out) / wall
        row = {"concurrent": n, "rep": rep, "wall_s": round(wall, 2), "loadavg_before": round(la0, 2), "loadavg_after": round(os.getloadavg()[0], 2),
               "per_stream_decode_tok_s_mean": round(sum(o["decode_tok_s"] for o in out) / n, 2),
               "per_stream_prefill_tok_s_mean": round(sum(o["prefill_tok_s"] for o in out) / n, 2),
               "end_to_end_completion_tok_s_aggregate": round(agg, 2), "requests": out}
        print(json.dumps(row), flush=True); res.append(row)
