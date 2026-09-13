# Memory recall evaluation (memory-eval-v1)

60 memories, 48 queries, embedder `hashed-bow-v1`. Precision@1 per category; overall P@1 / R@5 / MRR; negative hit rate = fraction of unanswerable queries that still returned something.

| configuration | exact | paraphrase | misspelled | name | keyed | overall P@1 | R@5 | MRR | negative hit rate |
|---|---|---|---|---|---|---|---|---|---|
| `baseline` | 0.80 | 0.40 | 0.50 | 0.88 | 1.00 | 0.68 | 0.82 | 0.73 | 1.00 |
| `spelling` | 0.80 | 0.40 | 0.75 | 0.88 | 1.00 | 0.72 | 0.88 | 0.78 | 1.00 |
| `thesaurus` | 0.80 | 0.50 | 0.50 | 0.88 | 1.00 | 0.70 | 0.85 | 0.76 | 1.00 |
| `names` | 0.80 | 0.40 | 0.50 | 1.00 | 1.00 | 0.70 | 0.82 | 0.74 | 1.00 |
| `all` | 0.90 | 0.50 | 1.00 | 1.00 | 1.00 | 0.85 | 0.90 | 0.87 | 1.00 |

Confidence gate 0.25 (`all`): answers 39/40 answerable queries and 6/8 unanswerable ones.
Confidence gate 0.34 (`all`): answers 35/40 answerable queries and 0/8 unanswerable ones.

Misses at rank 1 under `all` (6):

- [exact] 'which database does staging use' → gold m03 at rank 3
- [paraphrase] 'which IDE does Ali like' → gold m60 at rank 2
- [paraphrase] 'when is the team meeting' → gold m11 at rank None
- [paraphrase] 'where are the credentials for rollout kept' → gold m06 at rank None
- [paraphrase] 'which llm answers questions' → gold m22 at rank None
- [paraphrase] 'what is the ci workflow for shipping' → gold m05 at rank None
