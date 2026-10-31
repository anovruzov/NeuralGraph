#!/bin/zsh
# Round 4: latency knobs + verify pass vs the ROUTED baseline, 5 sets. Each variant = routed protocol + one change.
set -u
ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"; cd "$ROOT"
export LLM_NO_THINK_PREFILL=1
R=.context/overnight/results; LOG=.context/overnight/logs/round4.log
A=research/overnight/scripts/answer_eval.py; C=research/overnight/scripts/compare.py
BASEFLAGS=(--prompt concise --cascade auto_v1n --route-compute auto_v1n --route-compute-think --extra-judges --threads 2)
typeset -A CTX; CTX=(lme_base2 lme_base2_q1raw3_6k_ctx.json lme_base lme_base_q1raw3_6k_ctx.json locomo_c0 locomo_c0_q1raw3_6k_ctx.json locomo_c1 locomo_c1_default_ctx.json locomo_c2 locomo_c2_default_ctx.json)
run() { # variant-name set extra-flags...
  local v=$1 s=$2; shift 2; local off=""; local met="strict,strict_v2,lenient_nonidk"; [[ $s == lme_* ]] && off="--official-judge" && met="$met,official"
  python3 $A --contexts $R/${CTX[$s]} --out $R/${s}_r4_${v}_acc.json $BASEFLAGS $off "$@" --label "$s $v" >> $LOG 2>&1
  echo "[r4] $s $v vs routed" >> $LOG
  python3 $C $R/${s}_routed_acc.json $R/${s}_r4_${v}_acc.json --md --metric $met >> $LOG 2>&1
  python3 - $R/${s}_routed_acc.json $R/${s}_r4_${v}_acc.json >> $LOG <<'PY'
import json,sys
for f in sys.argv[1:]:
    j=json.load(open(f)); o=j['summary']['overall']; rows=j['rows']; secs=sorted(r['answer_seconds'] for r in rows)
    print(f"   {f.split('/')[-1][:48]:<48} mean_s {o['mean_answer_s']:.2f} p50 {secs[len(secs)//2]:.2f} p95 {secs[int(len(secs)*0.95)]:.2f} calls {sum(r.get('calls',1) for r in rows)/len(rows):.2f} routed {sum(1 for r in rows if r.get('routed')=='compute')}")
PY
}
for s in lme_base2 lme_base locomo_c0 locomo_c1 locomo_c2; do
  run tight $s --route-regex tight --think-early-stop
  run estop $s --think-early-stop
  run cc80 $s --think-early-stop --concise-max-tokens 80
done
for s in lme_base2 lme_base locomo_c0; do
  run verify $s --think-early-stop --verify verify_v1
  run vidk $s --think-early-stop --verify verify_v1 --verify-apply-unsupported idk
done
echo "[r4] $(date '+%F %T') done" >> $LOG; touch $LOG.done
