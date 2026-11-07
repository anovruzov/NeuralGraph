# Scene audit: Mycelial-Launch-FINAL.mp4

Every result below was measured on the rendered file, `yc-demo/Mycelial-Launch-FINAL.mp4`.

## File
- 2:00.00 (120 s), 3,600 frames, 1920×1080, 30 fps, H.264 High.
- AAC-LC stereo, 48 kHz, 256 kb/s, fast-start.
- Size: 12,967,063 bytes (13.0 MB).
- Full decode: no errors.
- Loudness: −13.6 LUFS integrated, sample peak −1.0 dBFS. Loudnorm target was −14 LUFS / −1.5 dBTP; ffmpeg's ebur128 reported the peak as dBFS.

## Sections
| Time | Section | Category |
|---|---|---|
| 0:00–0:13 | Hook: the 2020 outage, public routing and ping data | NOC |
| 0:13–0:22 | Why NOC engineers struggle: evidence in separate tools | NOC |
| 0:22–0:52 | TraceNOC console: alert → "Inspect investigation" → 6 recorded agent exchanges → gate verdicts → operator report | NOC (product) |
| 0:52–1:00 | **Inserted:** Ali's prior work — Fujitsu Virtuora reconciliation agent, 48 hours → 10 minutes; AT&T-scale network as a future use case | Founder credibility / telecom |
| 1:00–1:10 | Investigation loop: from alert to verified diagnosis, 8 stages | NOC (product) |
| 1:10–1:23 | Operator value: without / with, then the labelled $676k scenario | NOC (value) |
| 1:23–1:35 | Measured result: 21.1% → 76.7%, withhold trade-off, swarm, coverage | Results |
| 1:35–1:46 | Adaptive execution: how the planner was made; harness in development | Harness |
| 1:46–1:55 | Platform: environments, Remember / Coordinate / Improve (NeuralGraph unchanged) | Platform |
| 1:55–2:00 | End card: tesseract, "See what others miss.", Mycelic | Close |

- **NOC-focused:** 75 s of 120 s = **62.5%** (target ≥55%) ✓. Counting the telecom prior-work slide too, it's 83 s = 69.2%.
- **Results-focused:** 12 s = **10.0%** (target ≤20%) ✓. If the harness scene is also counted as research, the total is 23 s = 19.2%.

## Removed
- The latency scene.
- The duplicate research-replay scene (it is kept in the source but never shown).
- Every Forge reference. Searching the visible HTML finds no "Forge".

## Operator workflow (as shown on screen)
1. An alert names the incident.
2. The operator clicks "Inspect investigation" (0:24.6).
3. Each of 6 objectives moves Queued → Investigating/Verifying → result, with the topology lighting up the entities involved.
4. The agent-exchange panel shows each question's recorded Scout → Verifier → Synthesizer messages and the evidence-gate check with record IDs.
5. The outcome is 4 supported findings and 2 explicit withholds (an abnormal baseline before 10:00, and the internal cause), each with its reason.
6. The report reads: "6 of 6 decisions correct · every finding cites record IDs".

Labelled throughout as a recorded replay of development questions.

## On-screen claims vs source
| Claim | Source |
|---|---|
| 19/90 → 69/90, +55.6 pts · 55 → 86 of 180, p < 0.001 · withholds 36/90 → 17/90 · swarm 52/78, 75 vs 71, p = 0.54 · 680/720 runs | Modal 9B paper (revised 1 Nov 2026), Tables 2–3 |
| Console decisions, messages, record IDs, withhold reasons | Recorded run `ho-D-dev`: decisions.jsonl and messages/*.json |
| 3.2× / 6.9×, 64% ping failure, 15:00 recovery, 14:30 report contradicted | Paper Fig. 1; RIPE RIS/Atlas reconstruction |
| $676,000 | Labelled scenario: 100 × 2 h × 52 × $65 |
| 48 hours → 10 minutes | Ali's prior work at Fujitsu (founder-supplied). Labelled on screen as not a Mycelic Labs result; the AT&T-scale network is a future application, not a deployment. |

The full line-by-line table is in `CLAIMS_AUDIT.md`. That file still describes the earlier cut, so its Forge and latency rows no longer apply.

## Audio/visual sync (measured)
- **Shared cue timeline:** `cues.json` holds 105 cues, and the visuals and the score use the same event times.
- **Global alignment:** the MP4's audio was cross-correlated against the score source in 4 windows (20, 45, 80, 105 s). The lag was **0 samples** in each, so every cue sound plays at its scheduled time.
- **Onset detection at each cue:** median 30 ms, max 60 ms. This reflects the ±60 ms detection window in a reverberant mix, not drift.
- **Visual change at cues:**
  - Clicks, verdicts and the report start changing on the cue frame (0–33 ms).
  - Agent messages ease in, so the first measurable change is 100–167 ms after the cue.
  - Cut crossfades start 100 ms before the cut, with the hit landing mid-crossfade by design.

## Frames
- **Contact sheet:** 40 frames across the whole MP4, plus detailed stills of the console (two states), operator value, scenario, results and end card. No overlapping or clipped text was found in the frames inspected.
- **Black frames:** none.
- **Silence over 2 s:** none.
- **Static stretches over 3 s:**
  - 8.97–12.80 s: the end of the hook. The chart is complete and only the faint tesseract moves.
  - 103.30–106.33 s: the end of the platform scene.

  Both are intended reading holds, not render stalls.
- **Not verified:** not every one of the 3,360 frames was inspected individually.

## Slide insertion check (measured)
- **0:00–0:42 is the original encode, reused.** Frames at 10, 30 and 45 s are pixel-identical to the previous final (mean absolute difference 0.00/255).
- **Shifted scenes match the old cut.** Comparing new 73/98/116 s with old 65/90/108 s gives 0.09–0.13/255, which is encoder noise.
- **Audio is sample-aligned.** The MP4's audio vs the score source shows a 0-sample lag at 20, 54, 88 and 112 s. The slide's impact cue is at 55.0 s, the frame where "48 hours → 10 minutes" appears.
- **The new "black" flag is a fade, not a blank.** It covers 52.2–53.0 s, the fade from the console into the slide. The slide's first line is visible from 52.5 s.
- **Static stretches over 3 s:** 8.97–12.80 s (end of the hook) and 111.30–114.80 s (end of the platform scene). Both are intended holds.

---

# iPhone cut: Mycelial-Launch-iPhone-FINAL.mp4 (measured)

- **File:** 2:01.00, 3,630 frames, 1080×1920 portrait, 30 fps, H.264 High; AAC 256 kb/s, 48 kHz.
- **Size:** 11,500,906 bytes (11.5 MB). Full decode: no errors.
- **Loudness:** −13.6 LUFS, sample peak −1.3 dBFS, no silence over 2 s.
- **Audio sync:** the MP4's audio vs the score source shows a 0-sample lag at 10, 52, 88 and 112 s. All 116 cues are taken from the same event times as the visuals.

| Time | Section | Category |
|---|---|---|
| 0:00–0:07.5 | Hook (unchanged) | NOC |
| 0:07.5–0:50.5 | TraceNOC console, 5 views: open incident → ingest/index → agents (recorded ho-D-dev exchanges) → competing explanations (answer cards, 14:30 report contradicted) → verification gate → operator report | NOC (product) |
| 0:50.5–0:58.5 | Ali's prior work at Fujitsu (Virtuora, 48 hours → 10 minutes); AT&T scale as a future use case | Founder / telecom |
| 0:58.5–1:11 | Adaptive execution loop: recorded v1 (ctl-A-std) vs v2 (ho-C-dev) recovery-question retrievals | Harness (on TraceNOC traces) |
| 1:11–1:25 | Architecture fabric: local models, NeuralGraph (implemented · open source), coordination (prototype), verification on arrival | Architecture |
| 1:25–1:37 | Measured result: 19/90 → 69/90, +55.6 pts, 55 → 86 of 180, withholds 36 → 17 | Results |
| 1:37–1:48 | Four policies (Table 2): answerable vs withhold; C vs D p = 0.54; ≈2–3× tokens; gate | Results |
| 1:48–1:54 | $676,000 labelled scenario | Operator value |
| 1:54–2:01 | Tesseract · "See what others miss." · Mycelic | Close |

- **NOC / product** (hook + console + scenario): 56.5 s = 46.7%. Adding the telecom prior-work slide makes 64.5 s = 53.3%. Adding the harness scene, which replays TraceNOC traces, makes 77 s = 63.6%.
- **Results** (two scenes): 23 s = **19.0%** (≤20%) ✓
- **Forge:** absent. A search of the visible HTML finds 0 matches.
- **Black-detector flags:** 97.2–97.8 s and 108.2–109.5 s, both scene openings where only the headline is on screen yet.
- **Static stretches over 3 s:** 10.1, 34.6, 45.5, 102.3 and 110.5 s, each 3.1–4.0 s. These are reading holds at the end of a view or scene; no renderer stalls were found.
- **Not verified:** every frame individually.
