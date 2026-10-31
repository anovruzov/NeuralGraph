#!/bin/zsh
# Parity: the crate's `answer` path (CLI) over the 50 lme_base2 questions vs the harness routed run, same judges.
set -u
ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"; cd "$ROOT"
export LLM_NO_THINK_PREFILL=1 LLM_MODEL=qwen3.8-9b-mlx
R=.context/overnight/results; LOG=.context/overnight/logs/parity_answer.log
rm -rf /tmp/parity_db && cp -r .context/overnight/db/lme_base2 /tmp/parity_db
python3 - >> $LOG 2>&1 <<'PY'
import json, subprocess, re, time
rows=json.load(open('.context/overnight/results/lme_base2_q1raw3_6k_ctx.json'))['rows']; out=[]
for r in rows:
    m=re.match(r'(\d{4})/(\d{2})/(\d{2})', r.get('question_date','') or ''); today=f"{m[1]}-{m[2]}-{m[3]}" if m else ''
    cmd=['./target/release/neuralgraph-memory','answer','--db',f"/tmp/parity_db/{r['id']}.db",'--json']+(['--today',today] if today else [])+[r['question']]
    t=time.perf_counter(); p=subprocess.run(cmd,capture_output=True,text=True); dt=time.perf_counter()-t
    try: j=json.loads(p.stdout); ans=j['text']; routed=j['routed']; casc=j['cascaded']
    except Exception: ans=''; routed=None; casc=None; print('ERR', r['id'], p.stderr[-300:])
    out.append({'id':r['id'],'answer':ans,'raw':'','answer_seconds':round(dt,2),'routed':'compute' if routed else None,'cascaded':casc})
    print(f"[{len(out)}/{len(rows)}] routed={routed} {ans[:60]!r}", flush=True)
json.dump({'rows':out}, open('.context/overnight/results/lme_base2_crateanswer_pre.json','w'))
PY
python3 research/overnight/scripts/answer_eval.py --contexts $R/lme_base2_q1raw3_6k_ctx.json --regrade-from $R/lme_base2_crateanswer_pre.json --out $R/lme_base2_crateanswer_acc.json --extra-judges --official-judge --threads 4 >> $LOG 2>&1
echo "[parity] crate answer path vs harness routed (same DBs, same judges)" >> $LOG
python3 research/overnight/scripts/compare.py $R/lme_base2_routed_acc.json $R/lme_base2_crateanswer_acc.json --md --metric strict,strict_v2,lenient_nonidk,official >> $LOG 2>&1
echo "[parity] $(date '+%F %T') done" >> $LOG; touch $LOG.done
