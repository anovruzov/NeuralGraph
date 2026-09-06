"""N5: re-score existing LoCoMo answer files under three judges.

J1  lenient  = runner.py ACCURACY_PROMPT verbatim + runner's exact/substring auto-pass, judge = qwen3.6-35b-a3b
J2  strict   = strict prompt (same specific fact), exact-match auto-pass only, judge = qwen3.6-35b-a3b
J3  substring= gold parts (split on ',' and ' and ', parts > 2 chars) all in generated answer, no LLM

Sequential calls only (capstone run shares LM Studio). Judge outputs cached in n5_judge_cache.json
keyed by sha1(judge, question, gold, generated) so a crash resumes and duplicate answers across files
are judged once. Per-row labels written to n5_rescored.json.
"""
import hashlib
import json
import os
import re
import sys
import time
import urllib.request

OV = '/private/tmp/claude-501/-Users-nurmanmahammadov-Desktop-NeuralGraph/86b8b88e-d6ea-43ee-b99d-38ce1e4a3c41/scratchpad/overnight/'
DR = '/Users/nurmanmahammadov/Desktop/NeuralGraph/demo/results/'
FILES = [
    ('flat_single_hop', DR + 'flat_single_hop.json'),
    ('local_pairs_single_hop', DR + 'local_pairs_single_hop.json'),
    ('H8_control', OV + 'H8_control.json'),
    ('H8_treatment', OV + 'H8_treatment.json'),
    ('H5_control', OV + 'H5_control.json'),
    ('H5_T2_full', OV + 'H5_T2_full.json'),
]
CACHE_PATH = OV + 'n5_judge_cache.json'
OUT_PATH = OV + 'n5_rescored.json'
LOG_PATH = OV + 'n5_progress.log'
URL = 'http://127.0.0.1:1234/v1/chat/completions'
MODEL = 'qwen/qwen3.6-35b-a3b'

# Verbatim copy of demo/runner.py ACCURACY_PROMPT (campaign judge prompt).
ACCURACY_PROMPT = """
Your task is to label an answer to a question as 'CORRECT' or 'WRONG'. You will be given the following data:
    (1) a question (posed by one user to another user),
    (2) a 'gold' (ground truth) answer,
    (3) a generated answer
which you will score as CORRECT/WRONG.

The point of the question is to ask about something one user should know about the other user based on their prior conversations.
The gold answer will usually be a concise and short answer that includes the referenced topic, for example:
Question: Do you remember what I got the last time I went to Hawaii?
Gold answer: A shell necklace
The generated answer might be much longer, but you should be generous with your grading - as long as it touches on the same topic as the gold answer, it should be counted as CORRECT.

For time related questions, the gold answer will be a specific date, month, year, etc. The generated answer might be much longer or use relative time references (like "last Tuesday" or "next month"), but you should be generous with your grading - as long as it refers to the same date or time period as the gold answer, it should be counted as CORRECT. Even if the format differs (e.g., "May 7th" vs "7 May"), consider it CORRECT if it's the same date.

Now it's time for the real question:
Question: {question}
Gold answer: {gold_answer}
Generated answer: {generated_answer}

First, provide a short (one sentence) explanation of your reasoning, then finish with CORRECT or WRONG.
Do NOT include both CORRECT and WRONG in your response, or it will break the evaluation script.

Just return the label CORRECT or WRONG in a json format with the key as "label".
"""

STRICT_PROMPT = """
You are a STRICT grader. Decide whether a generated answer states the same specific fact as the gold answer.

Question: {question}
Gold answer: {gold_answer}
Generated answer: {generated_answer}

Rules:
- CORRECT only if the generated answer states the SAME SPECIFIC FACT as the gold answer: the same entity, number, date, place, or item. Wording, format and length may differ (e.g. "May 7th" vs "7 May", "Sweden" vs "she moved from Sweden") as long as the fact is identical.
- If the gold answer is a list, CORRECT only if EVERY gold item is present in the generated answer. Extra items are tolerated.
- Topical overlap is NOT enough. An answer that is vaguer or more general than the gold (e.g. "home country" for "Sweden", "pets" for "turtles", "a book" for "Becoming Nicole") is WRONG.
- An answer that gives a related but different fact, or contradicts the gold, is WRONG.
- Abstentions and hedges such as "Not found", "Unclear", "No evidence", "I don't know", "the memories do not mention" are WRONG, even if they mention the topic.

Return only a JSON object with the key "label" and the value "CORRECT" or "WRONG".
"""


def parse_judge_label(resp: str) -> bool:
    """Copy of runner.parse_judge_label: True only if label is exactly CORRECT."""
    try:
        obj = json.loads(resp.strip())
        return str(obj.get("label", "")).strip().upper() == "CORRECT"
    except Exception:
        pass
    try:
        m = re.search(r'\{[^}]+\}', resp)
        if m:
            obj = json.loads(m.group())
            return str(obj.get("label", "")).strip().upper() == "CORRECT"
    except Exception:
        pass
    m = re.search(r'"label"\s*:\s*"(CORRECT|WRONG)"', resp, flags=re.IGNORECASE)
    if m:
        return m.group(1).upper() == "CORRECT"
    return False


def llm(prompt: str) -> str:
    body = json.dumps({
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "max_tokens": 150,
        "stream": False,
        "reasoning_effort": "none",
    }).encode()
    last = None
    for attempt in range(4):
        try:
            req = urllib.request.Request(URL, data=body, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=180) as r:
                obj = json.loads(r.read())
            return obj["choices"][0]["message"]["content"].strip()
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"judge call failed after retries: {last}")


def key(judge, q, gold, gen):
    return hashlib.sha1(('\x1f'.join([judge, q, gold, gen])).encode()).hexdigest()


def norm_bool(v):
    return v is True or str(v).strip().lower() == 'true'


def j3_substring(gold: str, gen: str) -> bool:
    g = gen.lower()
    parts = [p.strip().lower() for p in re.split(r',| and ', gold)]
    parts = [p for p in parts if len(p) > 2]
    if not parts:
        return gold.lower().strip() in g and bool(gold.strip())
    return all(p in g for p in parts)


def load_cache():
    if os.path.exists(CACHE_PATH):
        with open(CACHE_PATH) as f:
            return json.load(f)
    return {}


def save_cache(cache):
    tmp = CACHE_PATH + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(cache, f)
    os.replace(tmp, CACHE_PATH)


def log(msg):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(LOG_PATH, 'a') as f:
        f.write(line + '\n')


def main():
    cache = load_cache()
    out = {}
    n_calls = 0
    t0 = time.time()
    for name, path in FILES:
        with open(path) as f:
            rows = json.load(f)['results']
        scored = []
        for i, r in enumerate(rows):
            q = str(r['question']); gold = str(r['gold_answer']); gen = str(r['generated_answer'])
            gl, ge = gold.lower().strip(), gen.lower().strip()
            rec = {
                'id': r.get('id'), 'question': q, 'gold': gold, 'generated': gen,
                'gemma': norm_bool(r.get('correct')),
                'j3': j3_substring(gold, gen),
            }
            # J1: runner auto-pass (exact or substring), else lenient LLM judge
            if gl and (ge == gl or gl in ge):
                rec['j1'] = True; rec['j1_src'] = 'autopass'
            elif not gl:
                rec['j1'] = False; rec['j1_src'] = 'nogold'
            else:
                k = key('J1', q, gold, gen)
                if k not in cache:
                    raw = llm(ACCURACY_PROMPT.format(question=q, gold_answer=gold, generated_answer=gen))
                    cache[k] = {'raw': raw, 'label': parse_judge_label(raw)}
                    n_calls += 1
                    if n_calls % 10 == 0:
                        save_cache(cache)
                rec['j1'] = cache[k]['label']; rec['j1_src'] = 'llm'; rec['j1_raw'] = cache[k]['raw']
            # J2: exact-match auto-pass only, else strict LLM judge
            if gl and ge == gl:
                rec['j2'] = True; rec['j2_src'] = 'exact'
            elif not gl:
                rec['j2'] = False; rec['j2_src'] = 'nogold'
            else:
                k = key('J2', q, gold, gen)
                if k not in cache:
                    raw = llm(STRICT_PROMPT.format(question=q, gold_answer=gold, generated_answer=gen))
                    cache[k] = {'raw': raw, 'label': parse_judge_label(raw)}
                    n_calls += 1
                    if n_calls % 10 == 0:
                        save_cache(cache)
                rec['j2'] = cache[k]['label']; rec['j2_src'] = 'llm'; rec['j2_raw'] = cache[k]['raw']
            scored.append(rec)
            if (i + 1) % 25 == 0:
                log(f"{name}: {i+1}/{len(rows)} rows, {n_calls} llm calls, {time.time()-t0:.0f}s")
        out[name] = scored
        save_cache(cache)
        n = len(scored)
        log(f"DONE {name}: n={n} gemma={sum(x['gemma'] for x in scored)} "
            f"J1={sum(x['j1'] for x in scored)} J2={sum(x['j2'] for x in scored)} J3={sum(x['j3'] for x in scored)}")
        with open(OUT_PATH, 'w') as f:
            json.dump(out, f, indent=1)
    save_cache(cache)
    log(f"ALL DONE: {n_calls} llm calls in {time.time()-t0:.0f}s")


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == 'count':
        # dry run: how many LLM calls are needed
        need = {'J1': set(), 'J2': set()}
        for name, path in FILES:
            for r in json.load(open(path))['results']:
                q = str(r['question']); gold = str(r['gold_answer']); gen = str(r['generated_answer'])
                gl, ge = gold.lower().strip(), gen.lower().strip()
                if gl and not (ge == gl or gl in ge):
                    need['J1'].add(key('J1', q, gold, gen))
                if gl and ge != gl:
                    need['J2'].add(key('J2', q, gold, gen))
        print({k: len(v) for k, v in need.items()})
    else:
        main()
