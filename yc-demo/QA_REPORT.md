# QA report: YC landscape demo

**Final file:** `yc-demo/Mycelic_TraceNOC_YC_W27_FINAL.mp4`
Verified on the rendered MP4 itself, not the source HTML.

| Check | Result |
|---|---|
| Duration | 2:05.00 (125 s); under the 3-minute limit |
| Size | 11,937,184 bytes (11.9 MB); under 100 MB |
| Video | H.264 High, yuv420p, 1920×1080, 30 fps, 3,750 frames |
| Full decode | No errors |
| Audio | AAC-LC stereo, 48 kHz, 196 kb/s · −15.2 LUFS integrated · LRA 3.6 LU · peak −1.2 dBFS · no silence >2 s |
| Black frames | None |
| Static stretches >3 s | 8.97–12.8 s, 20.6–23.8 s, 34.2–37.8 s, 116.3–119.8 s. These are intended end-of-scene reading holds, confirmed by looking at the frames, not render stalls. |
| Real Mycelic logo | Visible in every top bar and beside the closing tagline (frames pulled from the MP4 at 8 s and 122.5 s) |
| Tesseract | 16 vertices and 32 edges, all edges of length 2 in 4D. XW + YW + YZ rotation with perspective 4D → 3D → 2D. Shown in the intro (subtle), at the results reveal (assembling from scattered points), in "Prompting is dead", and as the central closing visual. |
| Frames inspected from the MP4 | 1.5, 8, 27, 37, 41.5, 62, 70, 72, 88, 104, 116, 122.5, 124.5 s. No clipped or overlapping text found. |
| Claims | See `CLAIMS_AUDIT.md`. Every number traces to the Modal 9B paper (revised 1 Nov 2026) or a recorded artifact. |

## Outstanding limitations
- The independent multi-agent QA workflow was stopped before it reported, to prioritise the submission. Claims and frames were checked by me, not by independent reviewers.
- The Google "harnesses" reference was left out because no source was provided.
- The YC application text says "multiple agents investigate" and "Qwen3 8B". The paper finds no swarm advantage over a planned central agent, and it used Qwen3.5 9B.
- The music is original and synthesized in code, so there are no rights issues.

# QA report: vertical (LinkedIn / Instagram) cut

**Final file:** `yc-demo/Mycelic_TraceNOC_VERTICAL_FINAL.mp4`
Verified on the rendered MP4.

| Check | Result |
|---|---|
| Duration | 58.50 s (target 55–59 s) |
| Size | 5,158,329 bytes (5.2 MB) |
| Video | H.264 High, yuv420p, 1080×1920 portrait, 30 fps, 1,755 frames. The layout was recomposed for portrait, not cropped from the landscape cut. |
| Full decode | No errors |
| Audio | AAC-LC stereo, 48 kHz · −13.4 LUFS (social-feed level) · peak −1.4 dBFS · no silence >2 s |
| Static stretches >3 s | None |
| "Black" flags | 4 flags at 7.7, 28.2, 44.2 and 51.2 s (0.6–1.3 s each). Inspected: these are scene openings where the headline is already visible on a dark background. The detector counts frames that are ≥98% dark; they are not blank. |
| Real logo | In every top bar (7 scenes) and on the end card |
| Tesseract | Same XW + YW + YZ geometry as the landscape cut. Subtle in the intro, central behind "PROMPTING IS DEAD.", assembling at the results reveal, and central on the closing card. |
| Safe zones | Important text sits between y ≈ 150 (top bar) and y ≈ 1600, away from Reels/LinkedIn UI overlays |
| Story | Hook (incident + "small local AI agents investigate together — and verify") → scattered evidence → PROMPTING IS DEAD → operator console replaying recorded run ho-D-dev → research replay → 21.1% → 76.7% with the withhold regression → harness-not-weights with "27 proposed · 0 accepted" → labelled $676k scenario → SEE WHAT OTHERS MISS |
