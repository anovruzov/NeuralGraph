# v2 abstention-gate: root-cause analysis

Paired rows: **60** of 60. Read-only analysis; no prompt, grader, split or cached prediction was modified.

## Verdict up front

- Raw judged delta: **+0.0833** (24→29 correct)
- Noise-corrected delta: **+0.0833**
- McNemar exact p (raw): **0.2668**, corrected: **0.1797**
- Paired bootstrap 95% CI: **[-0.0333, 0.2]** — NOT significant, spans zero

## §1 Paired-run integrity

Config differs **only** in the prompt. Identical across versions: answerer model (`qwen2.5:7b-instruct`), judge (`gpt-4o`), temperature (0.0), top-k (`recorded`), evidence set, split membership, scoring implementation, error handling.

- Evidence **set** changed on **0/60** questions (required: 0) ✅
- Evidence **order** changed on **60/60** questions ⚠️
- Cache key: `kind:question_id:config_hash:evidence_hash`; `config_hash` covers answerer, judge, temperature, top-k, prompt_hash. No cross-version reuse is possible (v1 `cd9098df…`, v2 `817fb674…`). No decoding seed exists to pin — Ollama exposes none.

## §2 Classification of every paired row

| class | n |
|---|---|
| REAL_GENERATION_CHANGE | 29 |
| SAME_OUTPUT_SAME_VERDICT | 27 |
| SAME_OUTPUT_JUDGE_FLIP | 3 |
| SEMANTICALLY_EQUIVALENT_OUTPUT_JUDGE_FLIP | 1 |

Judge-flip rows (identical output, different verdict): **[178, 898, 1018, 1398]**

## §3 A/A variance

Judge stability, measured directly on a byte-identical candidate (q178):

- 5 fresh identical calls: 5/5 INCORRECT
- 20 further fresh identical calls: 20/20 INCORRECT
- cached v1 verdict: CORRECT; cached v2 verdict: INCORRECT
- **1 CORRECT in 22 observations → judge flip rate ≈ 4.5%**

A 23-candidate × 5-rep sweep found 0/23 unstable, so flips are rare rather than widespread. At ~4.5% a 60-question set carries ~2.7 expected spurious flips — comparable to any plausible single-fix effect, so the raw delta alone cannot adjudicate a round.

## §5 Why a temporal fix moved non-temporal questions

**The patch bundled two mechanisms, only one of which was intended.**

1. *Intended*: timestamp/abstention instructions — but applied to **every** category, not gated to temporal.
2. *Unintended and global*: chronological re-ordering of evidence on **60/60** questions. v1 presented excerpts in **retrieval-rank order** (most relevant first); v2 sorts them by time, moving the rank-1 excerpt to median position **10**.

Prompt length rose only +2.9%, so this is position bias, not token budget.

## §7 Did the target mechanism move?

| | |
|---|---|
| abstention_to_wrong_answer | 0 |
| correct_abstentions_broken | 0 |
| false_abstentions_recovered | 0 |
| n | 15 |
| new_abstentions_introduced | 0 |
| unsupported_delta | 1 |
| v1_abstentions | 0 |
| v2_abstentions | 0 |

**The validation set contains zero temporal abstentions under v1.** The patch had no instance of its target failure to act on.

## Root causes, per row

| root cause | n |
|---|---|
| no_change | 27 |
| changed_but_verdict_unmoved | 11 |
| under_listing_lost_a_gold_item | 9 |
| genuine_improvement | 5 |
| judge_nondeterminism | 4 |
| unsupported_item_introduced | 2 |
| genuine_regression_answer_changed | 1 |
| gate_introduced_abstention | 1 |

## §8 Aggregate metrics

| metric | v1 | v2 | delta |
|---|---|---|---|
| item_f1 | 0.5544 | 0.5407 | -0.0137 |
| item_precision | 0.6306 | 0.6261 | -0.0044 |
| item_recall | 0.5317 | 0.5025 | -0.0292 |
| abstentions | 1 | 4 | +3 |
| unsupported items | 22 | 20 | -2 |

### By category

| category | n | v1 | v2 | delta |
|---|---|---|---|---|
| multi_hop | 15 | 6 | 10 | +0.2667 |
| open_domain | 15 | 3 | 5 | +0.1333 |
| single_hop | 15 | 5 | 3 | -0.1333 |
| temporal | 15 | 10 | 11 | +0.0667 |

## Discordant pairs

### q17 · temporal · REAL_GENERATION_CHANGE · changed_but_verdict_unmoved
- **Q**: When did Melanie sign up for a pottery class?
- **gold** (1 items): `2 July 2023`
- **v1**: `['2 July 2023']` → CORRECT
- **v2**: `['2023-07-02']` → CORRECT
- support: ['supported_by_retrieved_evidence_tokenwise']
- evidence present (lenient/strict): True/True

### q24 · single_hop · REAL_GENERATION_CHANGE · under_listing_lost_a_gold_item
- **Q**: What books has Melanie read?
- **gold** (2 items): `"Nothing is Impossible", "Charlotte's Web"`
- **v1**: `["Charlotte's Web"]` → INCORRECT
- **v2**: `[]` → INCORRECT
- support: []
- evidence present (lenient/strict): False/False

### q114 · multi_hop · REAL_GENERATION_CHANGE · changed_but_verdict_unmoved
- **Q**: What did Caroline see at the council meeting for adoption?
- **gold** (1 items): `many people wanting to create loving homes for children in need`
- **v1**: `['so many people wanted to create loving homes for children in need']` → CORRECT
- **v2**: `['people wanted to create loving homes for children in need']` → CORRECT
- support: ['supported_by_retrieved_evidence']
- evidence present (lenient/strict): False/False

### q178 · single_hop · SAME_OUTPUT_JUDGE_FLIP · judge_nondeterminism
- **Q**: What does Jon's dance studio offer?
- **gold** (5 items): `one-on-one metoring and training to dancers,  workshops and classes to local schools and centers`
- **v1**: `['dance classes and workshops', 'one-on-one mentoring and training']` → CORRECT
- **v2**: `['dance classes and workshops', 'one-on-one mentoring and training']` → INCORRECT
- support: ['supported_by_retrieved_evidence', 'supported_by_retrieved_evidence']
- evidence present (lenient/strict): False/False

### q270 · single_hop · REAL_GENERATION_CHANGE · under_listing_lost_a_gold_item
- **Q**: What music events has John attended?
- **gold** (2 items): `Live music event, violin concert`
- **v1**: `['a live music event', 'a violin concert']` → CORRECT
- **v2**: `['a live music event']` → INCORRECT
- support: ['supported_by_retrieved_evidence']
- evidence present (lenient/strict): True/False

### q294 · single_hop · REAL_GENERATION_CHANGE · under_listing_lost_a_gold_item
- **Q**: What are Maria's dogs' names?
- **gold** (2 items): `Coco, Shadow`
- **v1**: `['Coco', 'Kyle']` → INCORRECT
- **v2**: `['Coco']` → INCORRECT
- support: ['supported_by_retrieved_evidence']
- evidence present (lenient/strict): False/False

### q370 · multi_hop · REAL_GENERATION_CHANGE · under_listing_lost_a_gold_item
- **Q**: What does John believe is important for children regarding veterans?
- **gold** (2 items): `Teaching them to respect and appreciate those who served`
- **v1**: `['investing in our future generations is key', 'giving them the right tools for success']` → INCORRECT
- **v2**: `[]` → INCORRECT
- support: []
- evidence present (lenient/strict): False/False

### q394 · temporal · REAL_GENERATION_CHANGE · changed_but_verdict_unmoved
- **Q**: When did Nate get his first two turtles?
- **gold** (1 items): `2019`
- **v1**: `['10 November 2022']` → INCORRECT
- **v2**: `['2:01 pm on 23 January, 2022']` → INCORRECT
- support: ['supported_by_retrieved_evidence']
- evidence present (lenient/strict): False/False

### q409 · single_hop · REAL_GENERATION_CHANGE · genuine_improvement
- **Q**: Which of Joanna's screenplay were rejected from production companies?
- **gold** (4 items): `first screenplay on drama and romance, third screenplay on loss identity and connection`
- **v1**: `['a major company', 'some']` → INCORRECT
- **v2**: `['the screenplay Joanna submitted in May 2022', 'the screenplay Joanna submitted in October 2022']` → CORRECT
- support: ['supported_by_retrieved_evidence_tokenwise', 'supported_by_retrieved_evidence_tokenwise']
- evidence present (lenient/strict): False/False

### q426 · temporal · REAL_GENERATION_CHANGE · changed_but_verdict_unmoved
- **Q**: When did Joanna's laptop crash?
- **gold** (2 items): `The week before 14September, 2022`
- **v1**: `['5 September 2022']` → CORRECT
- **v2**: `['week of 5 September 2022']` → CORRECT
- support: ['supported_by_retrieved_evidence']
- evidence present (lenient/strict): False/False

### q428 · single_hop · REAL_GENERATION_CHANGE · under_listing_lost_a_gold_item
- **Q**: What movies have both Joanna and Nate seen?
- **gold** (2 items): `"Little Women", "Lord of the Rings"`
- **v1**: `['The Lord of the Rings Trilogy']` → INCORRECT
- **v2**: `[]` → INCORRECT
- support: []
- evidence present (lenient/strict): False/False

### q440 · temporal · REAL_GENERATION_CHANGE · genuine_regression_answer_changed
- **Q**: When was Joanna's second movie script shown on the big screens?
- **gold** (2 items): `The Sunday before 25October, 2022.`
- **v1**: `['23 October 2022']` → CORRECT
- **v2**: `['4 June 2022', '23 October 2022']` → INCORRECT
- support: ['supported_by_retrieved_evidence', 'supported_by_retrieved_evidence']
- evidence present (lenient/strict): False/False

### q554 · multi_hop · REAL_GENERATION_CHANGE · changed_but_verdict_unmoved
- **Q**: What helps Joanna stay focused and brings her joy?
- **gold** (1 items): `stuffed animal dog named Tilly`
- **v1**: `['Tilly', 'writing']` → INCORRECT
- **v2**: `['Tilly', 'stuffed animals']` → INCORRECT
- support: ['supported_by_retrieved_evidence', 'supported_by_retrieved_evidence']
- evidence present (lenient/strict): True/False

### q753 · multi_hop · REAL_GENERATION_CHANGE · under_listing_lost_a_gold_item
- **Q**: Why does John like Aragorn from Lord of the Rings?
- **gold** (3 items): `brave, selfless, down-to-earth attitude`
- **v1**: `['he grows so much throughout the story', "he's brave, selfless, down-to-earth", 'he never gives up and always stands up for justice']` → INCORRECT
- **v2**: `['He never gives up and always stands up for justice.', 'He is brave, selfless, and down-to-earth.']` → CORRECT
- support: ['supported_by_retrieved_evidence', 'supported_by_retrieved_evidence_tokenwise']
- evidence present (lenient/strict): True/True

### q791 · single_hop · REAL_GENERATION_CHANGE · changed_but_verdict_unmoved
- **Q**: What is something that Audrey often dresses up her dogs with?
- **gold** (1 items): `Hats`
- **v1**: `['collars', 'tags', 'toys']` → INCORRECT
- **v2**: `['collars and tags']` → INCORRECT
- support: ['supported_by_retrieved_evidence']
- evidence present (lenient/strict): True/True

### q815 · open_domain · REAL_GENERATION_CHANGE · unsupported_item_introduced
- **Q**: What is something that Andrew could do to make birdwatching hobby to fit in his city schedule?
- **gold** (1 items): `Install a bird feeder outside where he can see the birds without going outdoors.`
- **v1**: `['read some books about our ecological systems', 'find a new open space to hike nearby']` → INCORRECT
- **v2**: `['find a new open space to hike nearby']` → INCORRECT
- support: ['unsupported_by_retrieved_evidence']
- evidence present (lenient/strict): False/False

### q893 · open_domain · REAL_GENERATION_CHANGE · gate_introduced_abstention
- **Q**: In which state is the shelter from which James adopted the puppy?
- **gold** (1 items): `Connecticut.`
- **v1**: `['Stamford']` → INCORRECT
- **v2**: `[]` → INCORRECT
- support: []
- evidence present (lenient/strict): False/False

### q898 · open_domain · SAME_OUTPUT_JUDGE_FLIP · judge_nondeterminism
- **Q**: Did James have a girlfriend during April 2022?
- **gold** (1 items): `Presumably not`
- **v1**: `[]` → INCORRECT
- **v2**: `[]` → CORRECT
- support: []
- evidence present (lenient/strict): False/False

### q924 · single_hop · REAL_GENERATION_CHANGE · changed_but_verdict_unmoved
- **Q**: What happened to John's job situation in 2022?
- **gold** (3 items): `quit his IT Job, secured his dream job, aspires to become an eSports competition organizer`
- **v1**: `['left his IT job after 3 years', "got the offer and was ecstatic. Can't wait to start his journey!", 'finally got my dream job!']` → INCORRECT
- **v2**: `['got his dream job', 'left his IT job after 3 years', 'started a new startup']` → INCORRECT
- support: ['supported_by_retrieved_evidence_tokenwise', 'supported_by_retrieved_evidence_tokenwise', 'supported_by_retrieved_evidence']
- evidence present (lenient/strict): False/False

### q928 · temporal · REAL_GENERATION_CHANGE · genuine_improvement
- **Q**: When did James take his 3 dogs to the beach?
- **gold** (2 items): `August 9, 2022`
- **v1**: `['10 August 2022']` → INCORRECT
- **v2**: `['9 August 2022']` → CORRECT
- support: ['supported_by_retrieved_evidence']
- evidence present (lenient/strict): True/False

### q952 · temporal · REAL_GENERATION_CHANGE · under_listing_lost_a_gold_item
- **Q**: When did John work with a game developer on a project?
- **gold** (2 items): `November 5-6, 2022`
- **v1**: `['8:57 pm on 7 November, 2022', '9:20 am on 3 October, 2022']` → INCORRECT
- **v2**: `['8:57 pm on 7 November, 2022']` → INCORRECT
- support: ['supported_by_retrieved_evidence']
- evidence present (lenient/strict): True/False

### q1018 · multi_hop · SEMANTICALLY_EQUIVALENT_OUTPUT_JUDGE_FLIP · judge_nondeterminism
- **Q**: What career milestone did John achieve recently in September 2022?
- **gold** (1 items): `making his first mobile game`
- **v1**: `['making my first mobile game']` → INCORRECT
- **v2**: `['making my first mobile game!']` → CORRECT
- support: ['supported_by_retrieved_evidence']
- evidence present (lenient/strict): True/False

### q1067 · temporal · REAL_GENERATION_CHANGE · genuine_improvement
- **Q**: When was the last photo of Deborah and Karlie taken?
- **gold** (1 items): `in summer 2022`
- **v1**: `['last summer']` → INCORRECT
- **v2**: `['last summer when we hiked']` → CORRECT
- support: ['supported_by_retrieved_evidence']
- evidence present (lenient/strict): False/False

### q1081 · temporal · REAL_GENERATION_CHANGE · changed_but_verdict_unmoved
- **Q**: When did Jolene finish her robotics project?
- **gold** (1 items): `May 2023`
- **v1**: `['1 February, 2023']` → INCORRECT
- **v2**: `['9 February, 2023']` → INCORRECT
- support: ['supported_by_retrieved_evidence']
- evidence present (lenient/strict): True/True

### q1111 · open_domain · REAL_GENERATION_CHANGE · changed_but_verdict_unmoved
- **Q**: What card game is Deborah talking about?
- **gold** (1 items): `Exploding Kittens`
- **v1**: `['a card game about cats']` → INCORRECT
- **v2**: `['This is a card game about cats, where you take cards one by one from a deck, and then you can attack your opponent with them.']` → INCORRECT
- support: ['supported_by_retrieved_evidence']
- evidence present (lenient/strict): False/False

### q1265 · open_domain · REAL_GENERATION_CHANGE · genuine_improvement
- **Q**: What role does nature and the outdoors play in Evan and Sam's mental well-being?
- **gold** (4 items): `Nature and outdoor activities seem to be significant stress relievers and sources of joy for both Evan and Sam. These activities likely contribute positively to their mental well-being.`
- **v1**: `['provides a peaceful environment', 'offers a way to bond with nature', 'serves as a source of inspiration for art']` → INCORRECT
- **v2**: `['a calming and fun way to bond with nature', 'a great way to relieve stress and be creative', 'a way to clear their heads', 'a way to appreciate something each day', 'a way to find peace']` → CORRECT
- support: ['supported_by_retrieved_evidence_tokenwise', 'supported_by_retrieved_evidence', 'unsupported_by_retrieved_evidence', 'supported_by_retrieved_evidence_tokenwise', 'supported_by_retrieved_evidence_tokenwise']
- evidence present (lenient/strict): False/False

### q1309 · single_hop · REAL_GENERATION_CHANGE · under_listing_lost_a_gold_item
- **Q**: What is a stress reliever for Sam?
- **gold** (4 items): `Unhealthy snacks, sweets, yoga, places with beautiful views`
- **v1**: `['running in the mornings', 'car rides', 'yoga', 'painting']` → INCORRECT
- **v2**: `['painting', 'running', 'car rides']` → INCORRECT
- support: ['supported_by_retrieved_evidence', 'supported_by_retrieved_evidence', 'supported_by_retrieved_evidence']
- evidence present (lenient/strict): False/False

### q1313 · multi_hop · REAL_GENERATION_CHANGE · changed_but_verdict_unmoved
- **Q**: What advice did Evan give Sam about finding a passion?
- **gold** (1 items): `keep trying new things until something sparks excitement`
- **v1**: `['set some goals', 'find an exercise that you enjoy', 'get a buddy for extra fun and accountability']` → INCORRECT
- **v2**: `['painting']` → INCORRECT
- support: ['supported_by_retrieved_evidence']
- evidence present (lenient/strict): False/False

### q1315 · multi_hop · REAL_GENERATION_CHANGE · under_listing_lost_a_gold_item
- **Q**: What did Evan find relaxing about his road trip to Jasper?
- **gold** (4 items): `fresh air, peacefulness, cozy cabin surrounded by mountains and forests`
- **v1**: `['the fresh air', 'the peacefulness', 'the scenery', 'a cozy cabin surrounded by mountains and forests']` → INCORRECT
- **v2**: `['fresh air', 'peacefulness', 'cozy cabin surrounded by mountains and forests']` → CORRECT
- support: ['supported_by_retrieved_evidence', 'supported_by_retrieved_evidence', 'supported_by_retrieved_evidence']
- evidence present (lenient/strict): True/False

### q1398 · single_hop · SAME_OUTPUT_JUDGE_FLIP · judge_nondeterminism
- **Q**: Why does Dave regularly visit parks?
- **gold** (2 items): `because it relaxes and calms him`
- **v1**: `['to relax']` → CORRECT
- **v2**: `['to relax']` → INCORRECT
- support: ['supported_by_retrieved_evidence']
- evidence present (lenient/strict): False/False

### q1404 · open_domain · REAL_GENERATION_CHANGE · changed_but_verdict_unmoved
- **Q**: Does Calvin wish to become more popular?
- **gold** (2 items): `Yes; he want's to grow his fanbase`
- **v1**: `['expand my brand worldwide and grow my fanbase']` → CORRECT
- **v2**: `['yes']` → CORRECT
- support: ['supported_by_retrieved_evidence']
- evidence present (lenient/strict): True/False

### q1466 · multi_hop · REAL_GENERATION_CHANGE · genuine_improvement
- **Q**: What gives Dave a sense of achievement and purpose?
- **gold** (1 items): `Fixing up things`
- **v1**: `['fixing up things', "transforming something that's not working into something that runs smoothly", 'seeing the hard work pay off', 'seeing something you create become a reality', 'helping people keep their cars in good condition', 'opening this shop']` → INCORRECT
- **v2**: `['fixing stuff up']` → CORRECT
- support: ['supported_by_retrieved_evidence']
- evidence present (lenient/strict): True/True

### q1513 · multi_hop · REAL_GENERATION_CHANGE · unsupported_item_introduced
- **Q**: How does Calvin balance his job and personal life?
- **gold** (1 items): `Takes it one day at a time`
- **v1**: `['working in the studio', 'having a room for relaxation with a TV']` → INCORRECT
- **v2**: `['Calvin only works in the studio and has a separate room for relaxation with a TV.']` → INCORRECT
- support: ['unsupported_by_retrieved_evidence']
- evidence present (lenient/strict): False/False

