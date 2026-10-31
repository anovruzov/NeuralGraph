"""Embed a JSONL file of {"text": ...} rows with a local Ollama model into a little-endian float32 matrix.

Resumable: rows already written to <out> are skipped. Writes <out>.meta.json with model, dim, row count, the
blake2b-256 of the matrix, and throughput. No prefix is added to the text, matching the engine's embed call
(crates/neuralgraph-memory/src/backend.rs sends the raw text).

usage: python3 -I embed_ollama.py IN.jsonl OUT.f32 [--model nomic-embed-text] [--batch 256] [--url http://localhost:11434]
"""
import argparse, hashlib, json, os, struct, time, urllib.error, urllib.request

ap = argparse.ArgumentParser()
ap.add_argument("inp"); ap.add_argument("out")
ap.add_argument("--model", default="nomic-embed-text"); ap.add_argument("--batch", type=int, default=256)
ap.add_argument("--url", default="http://localhost:11434")
a = ap.parse_args()

texts = [json.loads(l)["text"] for l in open(a.inp)]
dim = None
done = 0
if os.path.exists(a.out) and os.path.exists(a.out + ".dim"):
    dim = int(open(a.out + ".dim").read())
    done = os.path.getsize(a.out) // (4 * dim)
    with open(a.out, "r+b") as f:
        f.truncate(done * 4 * dim)
t0 = time.time()
with open(a.out, "ab") as f:
    for s in range(done, len(texts), a.batch):
        body = json.dumps({"model": a.model, "input": texts[s:s + a.batch]}).encode()
        for attempt in range(6):
            try:
                req = urllib.request.Request(a.url + "/api/embed", data=body, headers={"Content-Type": "application/json"})
                embs = json.load(urllib.request.urlopen(req, timeout=600))["embeddings"]
                break
            except urllib.error.HTTPError as ex:  # transient 400/500s observed under heavy host load
                print(f"rows {s}..: HTTP {ex.code} {ex.read()[:200]!r}; retry {attempt + 1}", flush=True)
                time.sleep(2 ** attempt)
        else:
            raise SystemExit(f"embedding failed at row {s} after retries")
        if dim is None:
            dim = len(embs[0]); open(a.out + ".dim", "w").write(str(dim))
        assert all(len(e) == dim for e in embs) and len(embs) == len(texts[s:s + a.batch])
        f.write(b"".join(struct.pack("<%df" % dim, *e) for e in embs)); f.flush()
        n = s + len(embs)
        if (n // a.batch) % 20 == 0:
            el = time.time() - t0
            print(f"{n}/{len(texts)} rows, {(n - done) / el:.1f} rows/s", flush=True)
h = hashlib.blake2b(digest_size=32)
with open(a.out, "rb") as f:
    for chunk in iter(lambda: f.read(1 << 20), b""):
        h.update(chunk)
meta = {"model": a.model, "dim": dim, "rows": len(texts), "input": os.path.basename(a.inp),
        "matrix_blake2b256": h.hexdigest(), "seconds_this_session": time.time() - t0,
        "rows_this_session": len(texts) - done, "url": a.url}
json.dump(meta, open(a.out + ".meta.json", "w"), indent=1)
print(json.dumps(meta))
