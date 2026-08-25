# Q7 — 1,986 vs 1,540: which protocol is canonical?

**Answer: definitively resolved. The 446 excluded questions are the adversarial
category, and the 1,540 subset is exactly "all answerable questions".**

| | |
|---|---|
| raw corpus (`evaluation/locomo/locomo10.json`) | **1,986** questions |
| recorded subset (`demo/maximal.json`) | **1,540** questions |
| excluded | **445** (of 446 in category 5) |

Raw category distribution:

| category | n | in subset |
|---|---|---|
| 1 | 282 | yes (single_hop) |
| 2 | 321 | yes (temporal) |
| 3 | 96 | yes (open_domain) |
| 4 | 841 | yes (multi_hop) |
| **5** | **446** | **excluded** |

**Every excluded question is category 5**, and the exclusion is not arbitrary:

* all **446** category-5 entries carry an `adversarial_answer` field;
* their `answer` field is the string `"None"`;
* they still carry `evidence` turn ids.

Samples: *"What did Caroline realize after her charity race?"*, *"What are
Melanie's plans for the summer with respect to adoption?"* — questions posed
about content the conversation does not actually establish.

These are **adversarial / unanswerable-by-construction** questions. The correct
behaviour is abstention, and scoring them changes the meaning of every metric:
accuracy becomes partly an abstention test, and a model that never abstains is
capped at 1,540/1,986 = 77.5% before answering a single question.

## Recommendation

**Adopt the 1,540 four-category subset as this repository's canonical protocol,
and label it explicitly** as *LoCoMo answerable subset (categories 1-4, n=1,540)*
— never as "LoCoMo".

Reasons: it is what every existing artifact in this repository measures; mixing
in an abstention test would confound answer quality with abstention calibration;
and the current answerer abstains on 1 of 60 questions, so it would score near
zero on category 5 and the composite would be uninterpretable.

**Hard rule: a 1,540 score and a 1,986 score must never be compared as the same
benchmark.** Any published number must state its denominator and category set.

Reporting category 5 separately as an abstention benchmark is a legitimate future
extension. It is not in scope here and no such run exists.

Sourced from the dataset file itself, not from any external claim.
