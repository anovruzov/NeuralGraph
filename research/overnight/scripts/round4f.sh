#!/bin/zsh
# Round 4f: 35B-A3B as routed thinker — replicate on lme_base; and a 1200-token thinking cap on base2 (latency recovery).
set -u
ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"; cd "$ROOT"
export LLM_NO_THINK_PREFILL=1
R=.context/overnight/results; LOG=.context/overnight/logs/round4f.log
A=research/overnight/scripts/answer_eval.py; C=research/overnight/scripts/compare.py
lat() { python3 - "$@" >> $LOG <<'PY'
import json,sys
for f in sys.argv[1:]:
    j=json.load(open(f)); o=j['summary']['overall']; rows=j['rows']; secs=sorted(r['answer_seconds'] for r in rows)
    rt=[r['answer_seconds'] for r in rows if r.get('routed')=='compute']; tr=sum(1 for r in rows if r.get('think_truncated'))
    print(f"   {f.split('/')[-1][:46]:<46} mean_s {o['mean_answer_s']:.2f} p50 {secs[len(secs)//2]:.2f} p95 {secs[int(len(secs)*0.95)]:.2f} routed_mean {sum(rt)/max(1,len(rt)):.2f} truncated {tr}")
PY
}
lms unload google/gemma-4-e4b >> $LOG 2>&1; lms load qwen/qwen3.6-35b-a3b -y --context-length 8192 --parallel 4 >> $LOG 2>&1
python3 $A --contexts $R/lme_base_q1raw3_6k_ctx.json --out $R/lme_base_r4_rm35_acc.json --prompt concise --cascade auto_v1n --route-compute auto_v1n --route-compute-think --route-model qwen/qwen3.6-35b-a3b --judge-model qwen3.8-9b-mlx --threads 2 --label "rm35 base" >> $LOG 2>&1
python3 $A --contexts $R/lme_base2_q1raw3_6k_ctx.json --out $R/lme_base2_r4_rm35cap_acc.json --prompt concise --cascade auto_v1n --route-compute auto_v1n --route-compute-think --route-compute-max-tokens 1200 --route-model qwen/qwen3.6-35b-a3b --judge-model qwen3.8-9b-mlx --threads 2 --label "rm35 cap1200" >> $LOG 2>&1
lms unload qwen/qwen3.6-35b-a3b >> $LOG 2>&1; lms load google/gemma-4-e4b -y --context-length 8192 --parallel 4 >> $LOG 2>&1
python3 $A --contexts $R/lme_base_q1raw3_6k_ctx.json --regrade-from $R/lme_base_r4_rm35_acc.json --out $R/lme_base_r4_rm35_g_acc.json --extra-judges --official-judge --threads 4 >> $LOG 2>&1
python3 $A --contexts $R/lme_base2_q1raw3_6k_ctx.json --regrade-from $R/lme_base2_r4_rm35cap_acc.json --out $R/lme_base2_r4_rm35cap_g_acc.json --extra-judges --official-judge --threads 4 >> $LOG 2>&1
echo "[r4f] 35B thinker on lme_base vs routed" >> $LOG
python3 $C $R/lme_base_routed_acc.json $R/lme_base_r4_rm35_g_acc.json --md --metric strict,strict_v2,lenient_nonidk,official >> $LOG 2>&1; lat $R/lme_base_routed_acc.json $R/lme_base_r4_rm35_acc.json
echo "[r4f] 35B thinker cap 1200 on base2 vs routed" >> $LOG
python3 $C $R/lme_base2_routed_acc.json $R/lme_base2_r4_rm35cap_g_acc.json --md --metric strict,strict_v2,lenient_nonidk,official >> $LOG 2>&1; lat $R/lme_base2_routed_acc.json $R/lme_base2_r4_rm35cap_acc.json
echo "[r4f] $(date '+%F %T') done" >> $LOG; touch $LOG.done
