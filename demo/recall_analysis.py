"""Offline recall@k against the evidence stored in a benchmark results JSON.

Usage:  python demo/recall_analysis.py [results.json]

Needs no model, no Ollama and no network - it reads the retrieved_memories
already saved by demo/runner.py, so it runs in seconds instead of hours.

For every question, find the rank of the first retrieved memory that supports the
gold answer. That yields a recall@k curve without running any model, and tells us
whether to raise k, fix ranking, or cut context.
"""
import json, re, sys

PATH = sys.argv[1] if len(sys.argv) > 1 else 'demo/maximal.json'
R = json.load(open(PATH))['results']

STOP = set("""a an the of in on at to for and or is was were are be been being with by from as
that this these those it its he she they them his her their i you we my your our not no yes do
did does have has had will would can could there here what when where who whom which how why""".split())
MONTHS = {m: i for i, m in enumerate(
    "january february march april may june july august september october november december".split(), 1)}
for m, i in list(MONTHS.items()):
    MONTHS[m[:3]] = i


def toks(s):
    return [t for t in re.sub(r'[^a-z0-9 ]', ' ', str(s).lower()).split() if t and t not in STOP]


def dates(s):
    out, low = set(), str(s).lower()
    for d, mo, y in re.findall(r'(\d{1,2})\s+([a-z]{3,9})\.?,?\s+(\d{4})', low):
        if mo in MONTHS: out.add((int(d), MONTHS[mo], int(y)))
    for mo, d, y in re.findall(r'([a-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})', low):
        if mo in MONTHS: out.add((int(d), MONTHS[mo], int(y)))
    return out


def supports(gold, text):
    gd = dates(gold)
    if gd:
        return bool(gd & dates(text))
    gt = set(toks(gold))
    if not gt:
        return False
    return len(gt & set(toks(text))) / len(gt) >= 0.6


ranks, unsorted_ct, depths = [], 0, []
per_cat = {}

for r in R:
    mems = r.get('retrieved_memories') or []
    depths.append(len(mems))
    ch = [m.get('charge', 0) for m in mems]
    if any(ch[i] < ch[i + 1] - 1e-9 for i in range(len(ch) - 1)):
        unsorted_ct += 1
    gold = str(r.get('gold_answer') if r.get('gold_answer') is not None else '')
    hit = None
    for i, m in enumerate(mems, 1):
        if supports(gold, m.get('text', '')):
            hit = i
            break
    ranks.append(hit)
    per_cat.setdefault(r['category'], []).append(hit)

n = len(ranks)
print('=' * 70)
print('RECALL@K  (n=%d, context depth median=%d)' % (n, sorted(depths)[n // 2]))
print('=' * 70)
print('%-6s %10s %10s' % ('k', 'RECALL', 'MARGINAL'))
prev = 0
for k in (1, 3, 5, 10, 20, 30, 50):
    hits = sum(1 for x in ranks if x is not None and x <= k)
    rec = 100 * hits / n
    print('%-6d %9.1f%% %9.1f pt' % (k, rec, rec - prev))
    prev = rec

print()
print('=' * 70)
print('RECALL@K BY CATEGORY')
print('=' * 70)
print('%-12s %8s %8s %8s %8s' % ('CATEGORY', '@5', '@10', '@20', '@50'))
for cat, rk in sorted(per_cat.items(), key=lambda x: -len(x[1])):
    row = [100 * sum(1 for x in rk if x is not None and x <= k) / len(rk) for k in (5, 10, 20, 50)]
    print('%-12s %7.1f%% %7.1f%% %7.1f%% %7.1f%%' % (cat, *row))

# where does evidence sit, when it is found at all?
found = [x for x in ranks if x is not None]
found.sort()
print()
print('=' * 70)
print('RANK OF SUPPORTING EVIDENCE  (when present at all, n=%d)' % len(found))
print('=' * 70)
print('median rank %d | p90 rank %d | p99 rank %d'
      % (found[len(found) // 2], found[int(len(found) * .9)], found[int(len(found) * .99)]))
top10 = sum(1 for x in found if x <= 10)
print('%.0f%% of found evidence sits in the top 10 of a %d-deep context'
      % (100 * top10 / len(found), sorted(depths)[n // 2]))

print()
print('=' * 70)
print('CONTEXT ORDERING')
print('=' * 70)
print('questions whose context is NOT sorted by charge: %d / %d (%.0f%%)'
      % (unsorted_ct, n, 100 * unsorted_ct / n))

# accuracy as a function of evidence rank
print()
print('=' * 70)
print('ACCURACY vs RANK OF EVIDENCE')
print('=' * 70)
print('%-16s %8s %10s' % ('EVIDENCE AT', 'N', 'ACCURACY'))
buckets = [('rank 1-5', 1, 5), ('rank 6-10', 6, 10), ('rank 11-20', 11, 20),
           ('rank 21-50', 21, 50), ('not retrieved', None, None)]
for lab, lo, hi in buckets:
    if lo is None:
        sel = [r for r, x in zip(R, ranks) if x is None]
    else:
        sel = [r for r, x in zip(R, ranks) if x is not None and lo <= x <= hi]
    if sel:
        acc = 100 * sum(1 for r in sel if r['correct']) / len(sel)
        print('%-16s %8d %9.1f%%' % (lab, len(sel), acc))
