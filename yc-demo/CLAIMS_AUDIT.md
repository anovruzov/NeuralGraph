# Mycelic TraceNOC: claims audit (final)

Videos:
- `Mycelic_TraceNOC_YC_W27_FINAL.mp4` (1920×1080, 125 s)
- `Mycelic_TraceNOC_VERTICAL_FINAL.mp4` (1080×1920, 58.5 s)

Primary research source: **TraceNOC on the CenturyLink 2020 Outage, revised 1 Nov 2026** (`TraceNOC_CenturyLink_2020_Updated_Modal_9B(2).pdf`). The user identified `FINAL_TRACENOC_PAPER` as not authoritative, and nothing in the videos comes from it.

Secondary sources, all real artifacts:
- the recorded run `ho-D-dev` (decisions and role messages, 1 Nov 2026);
- the Comparator Forge log `research/operation/COMPARATOR_FORGE.md` and `logs/forge-r5.out`;
- the public RIPE RIS / RIPE Atlas reconstruction files;
- archived ThousandEyes and Cloudflare reports.

Labels: VERIFIED = in a source · QUALIFIED = true, shown with its condition · HYPOTHETICAL = vision or scenario, labelled as one · REMOVED = unsupported, so not shown

## Incident and hook (0–24 s; vertical 0–13.5 s)
| Claim | Status | Basis |
|---|---|---|
| 30 Aug 2020, CenturyLink / Level 3 (AS3356) | VERIFIED | Paper title and Fig. 1 |
| Nearly 5 hours | VERIFIED (attributed) | ThousandEyes analysis (archived copy) |
| 3.5% drop in global traffic | QUALIFIED | Cloudflare, 30 Aug 2020: a share of the traffic Cloudflare saw, not users. The caption says so. |
| 124.5M updates · 158 files · 2,873 records | VERIFIED | Paper, "Data provenance" |
| 3.2×/6.9× at 10:00 · 64% AS209 ping failure · 15:00 1.97×/1.83× · 14:30 report contradicted | VERIFIED | Paper, Fig. 1. The chart series were recomputed from the reconstruction files and match. |
| "CenturyLink is not a Mycelic customer" | Stated on screen | Avoids implying a customer relationship |
| "Mycelic's small local AI agents investigate it together — and verify before they answer" | QUALIFIED (product description) | The evidence-first swarm and evidence gate are real (Figs. 2–5, run `ho-D-dev`). The paper finds no accuracy advantage for the swarm over a planned single agent, and the video says so in the operator footer and the results scene. |

## Operator console (24–38 s; vertical 19–28 s), labelled RECORDED REPLAY
| Claim | Status | Basis |
|---|---|---|
| Six objectives: 4 SUPPORTED (10:00, AS3356, AS209, 15:00), 2 WITHHELD (pre-onset control, internal cause) | VERIFIED | `ho-D-dev/decisions.jsonl` |
| 6 of 6 decisions correct on development questions | VERIFIED | `ho-D-dev`. Consistent with paper §8.2: four supported-correct in both C and D, plus one extra correct withhold for D. |
| Scout → Verifier → Synthesizer exchange with record IDs rf7cacb9, rf5ccd08, r316d92e; checks time ok / lineage ok / contradiction none | VERIFIED (condensed wording) | `ho-D-dev/messages/w-ctl20200830-q-reach.initial.messages.json` |
| Withhold reasons | VERIFIED (paraphrased) | `ho-D-dev` decision reasons |
| "Development questions, not held out · timing compressed · no swarm advantage in the larger evaluation" | QUALIFIED | Paper §8.1–8.2. Stated on screen. |

## "Prompting is dead" loop (38–49 s)
| Claim | Status |
|---|---|
| The 8 stages | VERIFIED as described in Figs. 2, 3, 5 |
| Automatic loop assembly for new environments | HYPOTHETICAL, labelled "in development" |

## Research replay (49–67 s; vertical 28–36 s)
| Claim | Status | Basis |
|---|---|---|
| v1 answers (onset "rrc01" wrong, AS3356 provisional, AS209 supported, recovery withheld): 1 of 6 | VERIFIED | Table 1 and §7 |
| Planner revision was written by the researchers | VERIFIED | Fig. 5 |
| v2: 4 of 6 supported-correct | VERIFIED | §8.2 |
| Grid playback | QUALIFIED | Illustrates the documented rule ("scan hourly, refine where state changes") over the real window values. Timing compressed. |

## Measured result (67–79 s; vertical 36–44 s)
| Claim | Status | Basis |
|---|---|---|
| 19/90 (21.1%) → 69/90 (76.7%), +55.6 pts, same 9B model | VERIFIED | Table 2, "Planner effect". Population: 90 answerable questions out of 180 scheduled per arm, Qwen 9B on Modal, CenturyLink 2020 evidence. |
| Correct decisions 55 → 86 of 180, p < 0.001 | VERIFIED | Table 3 |
| Withholds 36/90 → 17/90 (regression) | VERIFIED | Table 2 |
| Swarm 52/78 (66.7%); 75 vs 71, p = 0.54 | VERIFIED | Abstract and Table 3 |
| 680/720 runs; 4 shards stopped; missing runs not imputed | VERIFIED | §10 |
| The bars grow, but the numbers appear only at their final values | Design rule | No intermediate percentages are shown |

## Harness, not weights (79–91 s; vertical 44–51 s)
| Claim | Status | Basis |
|---|---|---|
| The gain came from the investigation strategy; same weights, no training run | VERIFIED | Paper: same 9B model in A and C; the planner is deterministic harness code |
| "Why pay millions for RL?" | Rhetorical framing | No cost figure is claimed |
| Planner: human-designed, demonstrated | VERIFIED | Fig. 5 and §8 |
| Comparator Forge: 5 rounds, 27 proposals, 0 accepted; every rejection had a stated reason; nothing unvalidated entered the registry | VERIFIED | `COMPARATOR_FORGE.md` (r1–r4: 4+5+6+6) and `logs/forge-r5.out` (6). Synthetic test worlds, labelled as such. |
| Validator implemented; rule generation in development | VERIFIED | Same document |
| "The self-evolving harness did not create the planner" | Stated on screen | |
| Google reference | REMOVED | The user mentioned it, but there was no source to verify. Add it only with a citation. |

## Latency (91–98 s; not in the vertical cut)
| Claim | Status |
|---|---|
| Local test: larger latency overhead for the swarm | VERIFIED (§9) |
| ≈2–3× tokens for the swarm | VERIFIED (§9) |
| 79 s → 27 s | REMOVED. It is not in the paper. Repo files show 79.5 s = original swarm (earlier build) and 27.4 s = central + planner (later build), so not a like-for-like pair. |
| Modal latency | REMOVED. §9 says none was supplied. |

## Business (98–111 s; vertical 51–55.5 s)
| Claim | Status |
|---|---|
| 100 × 2 × 52 = 10,400 h; × $65 = $676,000 | HYPOTHETICAL, arithmetic checked. Labelled "Scenario — not measured savings". |
| What an operator pays for: faster investigations, fewer unsupported diagnoses, existing infrastructure | Value proposition, not a measured result. "No operator study has measured this comparison." |

## Platform and thesis (111–120 s)
| Claim | Status |
|---|---|
| NeuralGraph: implemented, open source, not part of the evaluated system | Matches the YC application and repo |
| Coordination: prototype · adaptive harness: in development · Mycelic Fabric pilot version targeted Dec 2026 | Matches the YC application, stated as plans |
| Automotive: concept configuration, no data, no results | HYPOTHETICAL, labelled |
| Research preview since 2 Nov 2026; no paying customers or pilots | Matches the YC application |

## Brand
- **Logo:** the real Mycelic mark, cropped from `~/Downloads/mycelic black logo linkedin.png` into `assets/mycelic-mark.png` (white, alpha from luminance; no redraw). It appears in every top bar and beside the closing tagline.
- **Tesseract:** the signature motif, kept separate from the logo.
  - Geometry: 16 vertices and 32 edges, every edge of length 2 in 4D, checked numerically.
  - Motion: rotation in the XW and YW planes plus YZ, with perspective projection 4D → 3D → 2D. Driven deterministically by frame time.
  - Placement: subtle in the intro; assembling from scattered points at the results reveal; central on the closing card.

## Audio
Original score and sound cues, synthesized in code (`score.py`, numpy). No third-party music or samples, so there are no rights issues.

## Tensions with the YC application text
1. The application says "multiple agents investigate." The paper finds no swarm advantage over planned central (p = 0.54) at 2–3× the tokens.
2. The application lists Qwen3 8B; the paper used Qwen3.5 9B.
