#!/bin/zsh
# Round 4e (latency): (1) brief-reasoning prompt for routed calls; (2) Qwen3.6-35B-A3B (3B active) as the ROUTED thinker only.
# Judge: Gemma must be resident for (1); for (2) the 35B is loaded and Gemma unloaded (memory) → answers judged by the 9B as a placeholder,
# then regraded with Gemma after the swap back. Compared with routed (+estop) on lme_base2.
set -u
ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"; cd "$ROOT"
export LLM_NO_THINK_PREFILL=1
R=.context/overnight/results; LOG=.context/overnight/logs/round4e.log; CTX=$R/lme_base2_q1raw3_6k_ctx.json
A=research/overnight/scripts/answer_eval.py; C=research/overnight/scripts/compare.py
until [ -f .context/overnight/logs/round4d.log.done ]; do sleep 20; done
lat() { python3 - "$@" >> $LOG <<'PY'
import json,sys
for f in sys.argv[1:]:
    j=json.load(open(f)); o=j['summary']['overall']; rows=j['rows']; secs=sorted(r['answer_seconds'] for r in rows)
    rt=[r['answer_seconds'] for r in rows if r.get('routed')=='compute']
    print(f"   {f.split('/')[-1][:46]:<46} mean_s {o['mean_answer_s']:.2f} p50 {secs[len(secs)//2]:.2f} p95 {secs[int(len(secs)*0.95)]:.2f} routed_mean {sum(rt)/max(1,len(rt)):.2f}")
PY
}
# (1) brief reasoning
python3 $A --contexts $CTX --out $R/lme_base2_r4_brief_acc.json --prompt concise --cascade auto_v1n --route-compute auto_v1n_brief --route-compute-think --extra-judges --official-judge --threads 2 --label "brief" >> $LOG 2>&1
echo "[r4e] brief-reasoning routed prompt vs routed" >> $LOG
python3 $C $R/lme_base2_routed_acc.json $R/lme_base2_r4_brief_acc.json --md --metric strict,strict_v2,lenient_nonidk,official >> $LOG 2>&1; lat $R/lme_base2_routed_acc.json $R/lme_base2_r4_brief_acc.json
# (2) 35B-A3B as the routed thinker
lms unload google/gemma-4-e4b >> $LOG 2>&1; lms load qwen/qwen3.6-35b-a3b -y --context-length 8192 --parallel 4 >> $LOG 2>&1
python3 /tmp/tps.py "9B-dense (thinking)" >> $LOG 2>&1
sed 's/qwen3.8-9b-mlx/qwen\/qwen3.6-35b-a3b/' /tmp/tps.py > /tmp/tps35.py; python3 /tmp/tps35.py "35B-A3B (thinking)" >> $LOG 2>&1
python3 $A --contexts $CTX --out $R/lme_base2_r4_rm35_acc.json --prompt concise --cascade auto_v1n --route-compute auto_v1n --route-compute-think --route-model qwen/qwen3.6-35b-a3b --judge-model qwen3.8-9b-mlx --threads 2 --label "route-model 35B (placeholder judge)" >> $LOG 2>&1
lms unload qwen/qwen3.6-35b-a3b >> $LOG 2>&1; lms load google/gemma-4-e4b -y --context-length 8192 --parallel 4 >> $LOG 2>&1
python3 $A --contexts $CTX --regrade-from $R/lme_base2_r4_rm35_acc.json --out $R/lme_base2_r4_rm35_g_acc.json --extra-judges --official-judge --threads 4 >> $LOG 2>&1
echo "[r4e] 35B-A3B as routed thinker vs routed (Gemma judges)" >> $LOG
python3 $C $R/lme_base2_routed_acc.json $R/lme_base2_r4_rm35_g_acc.json --md --metric strict,strict_v2,lenient_nonidk,official >> $LOG 2>&1; lat $R/lme_base2_routed_acc.json $R/lme_base2_r4_rm35_acc.json
echo "[r4e] $(date '+%F %T') done" >> $LOG; touch $LOG.done
