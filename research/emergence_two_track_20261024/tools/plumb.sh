#!/bin/bash
# PLUMBING ONLY: fake lexical judge over HTTP through --routing; expect the baseline's found set and >0 receipts
S=/tmp/claude-0/-home-user-NeuralGraph/2707d1a8-0286-570b-bef6-de01f8113a8b/scratchpad; SEED=${1:-0}
cd /home/user/emergence && export PYTHONPATH=/home/user/emergence MYCELIC_NATS_SERVER_BIN=$S/nats/nats-server PATH=$S/nats:$PATH
rm -rf $S/em/routing-fake $S/em/plumb-s$SEED
python3.11 $S/tools/fake_lexical_judge_server.py --seed $SEED --routing-out $S/em/routing-fake > $S/em/fake.log 2>&1 & FP=$!
until grep -q listening $S/em/fake.log; do sleep 1; done
t=$(date +%s)
python3.11 -m research.e2e_ingest run --size 200 --seed $SEED --mode process --skip-drills --routing $S/em/routing-fake --out $S/em/plumb-s$SEED --run-id PLUMBING-fake-lexical-s$SEED > $S/em/plumb-s$SEED.log 2>&1
echo "exit $? secs $(( $(date +%s)-t ))"; kill -TERM $FP; sleep 1; tail -1 $S/em/fake.log
