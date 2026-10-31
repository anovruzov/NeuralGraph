#!/bin/zsh
# Round 4b (wild): agentic follow-up retrieval for routed questions, vs routed. LME both ingests + LoCoMo c0.
set -u
ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"; cd "$ROOT"
export LLM_NO_THINK_PREFILL=1
R=.context/overnight/results; LOG=.context/overnight/logs/round4b.log
A=research/overnight/scripts/answer_eval.py; C=research/overnight/scripts/compare.py
until [ -f .context/overnight/logs/round4.log.done ]; do sleep 20; done
run() { local s=$1 ctx=$2 dbflag=$3 dbval=$4; local off=""; local met="strict,strict_v2,lenient_nonidk"; [[ $s == lme_* ]] && off="--official-judge" && met="$met,official"
  python3 $A --contexts $R/$ctx --out $R/${s}_r4_agentic_acc.json --prompt concise --cascade auto_v1n --route-compute auto_v1n --route-compute-think --think-early-stop --agentic-search $dbflag $dbval --extra-judges $off --threads 2 --label "$s agentic" >> $LOG 2>&1
  echo "[r4b] $s agentic vs routed" >> $LOG
  python3 $C $R/${s}_routed_acc.json $R/${s}_r4_agentic_acc.json --md --metric $met >> $LOG 2>&1
  python3 - $R/${s}_r4_agentic_acc.json >> $LOG <<'PY'
import json,sys
j=json.load(open(sys.argv[1])); rows=j['rows']; o=j['summary']['overall']
print(f"   searched {sum(1 for r in rows if r.get('agentic_query'))} rows; mean_s {o['mean_answer_s']:.2f}; calls {sum(r.get('calls',1) for r in rows)/len(rows):.2f}")
PY
}
run lme_base2 lme_base2_q1raw3_6k_ctx.json --db-dir .context/overnight/db/lme_base2
run lme_base lme_base_q1raw3_6k_ctx.json --db-dir .context/overnight/db/lme_base
run locomo_c0 locomo_c0_q1raw3_6k_ctx.json --db .context/overnight/db/locomo_c0.db
echo "[r4b] $(date '+%F %T') done" >> $LOG; touch $LOG.done
