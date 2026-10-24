#!/bin/bash
# clean-checkout baselines, dev seeds 0-5, process mode, drills skipped
S=/tmp/claude-0/-home-user-NeuralGraph/2707d1a8-0286-570b-bef6-de01f8113a8b/scratchpad
cd /home/user/emergence-base && export MYCELIC_NATS_SERVER_BIN=$S/nats/nats-server PATH=$S/nats:$PATH PYTHONPATH=/home/user/emergence-base
for seed in "$@"; do
  t=$(date +%s)
  python3.11 $S/tools/run_seed.py run --size 200 --seed $seed --mode process --skip-drills --out $S/em/clean-s$seed --run-id clean-s$seed > $S/em/clean-s$seed.log 2>&1
  echo "seed $seed exit $? secs $(( $(date +%s)-t ))"
done
