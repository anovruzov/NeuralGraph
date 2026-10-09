## 5. Live language-model check (component level only)

- **What ran:**
  - Qwen3-4B-Instruct (llama.cpp, CPU) behind the product's model router;
  - n = 40 answer-step calls on the holders that hold the gold evidence;
  - details in `live/LIVE_ANSWER_CHECK.md`.
- **Result:** the live model gave the gold answer in 13 of 17 gold-holder cases, against 17/17 for the deterministic provider.
  It never gave a wrong option.
- **Not an end-to-end number:** the CPU model was far too slow for a 120-task run.

## 6. Reproduce

```
# from a checkout of the frozen commit (see impl_patches/README.md), anchored sources
python research/mycelic_e2e/bench/run.py --size S --seed 1 --split dev --mode system --out RUN --anchor 2026-10-09T00:00:00+00:00
python -m research.mycelic_e2e.bench.baseline_central RUN --authority-only
python -m research.mycelic_e2e.bench.report RUN --finalize                       # score + architecture gate
python -m research.mycelic_e2e.bench.baseline_central RUN --out RUN-baseline-source --variant source
python -m research.mycelic_e2e.bench.report RUN-baseline-source --finalize
python research/mycelic_e2e/bench/run.py ... --ablation A1                         # A1..A6
python -m research.mycelic_e2e.tools.results_table RUN RUN-baseline-source ... --json OUT.json
```

The holdout was run once:
- with `--split holdout`;
- with `MYCELIC_E2E_HOLDOUT_BANK` pointing at the sealed bank (sha256 `6a5eabd3…`);
- from a snapshot whose `.rev` equals the clean repository HEAD.

The ledger guard (`bench/ledger.py`) refuses a second holdout run of the same commit, ablation, mode and variant.
