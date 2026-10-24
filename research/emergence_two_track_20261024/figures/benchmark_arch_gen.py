"""EMERGENCE end-to-end benchmark: system architecture diagram (SVG).
Stage names and parameters follow emergence-rule docs/collective/E2E-INGEST.md (sections 1, 3.5, 3.6, 4)
and the recorded e2e-ingest reports."""
from xml.sax.saxutils import escape as E

W, H = 1800, 1130
BG, PANEL, PANEL2, RULE = "#070b10", "#0c131b", "#101a24", "#1f2d39"
TXT, DIM, DIM2 = "#dcefe5", "#8aa89a", "#5d7a6c"
GREEN, CYAN, PURPLE, AMBER, RED = "#3ee08a", "#4fd3ff", "#c39bff", "#ffb547", "#ff5d67"
SANS, MONO = "DejaVu Sans, Liberation Sans, sans-serif", "DejaVu Sans Mono, Liberation Mono, monospace"

out = []
def add(s): out.append(s)

def text(x, y, s, size=13, fill=TXT, weight="normal", anchor="start", font=SANS, ls=0):
    add(f'<text x="{x}" y="{y}" font-family="{font}" font-size="{size}" fill="{fill}" font-weight="{weight}" '
        f'text-anchor="{anchor}" letter-spacing="{ls}">{E(s)}</text>')

def rect(x, y, w, h, fill=PANEL, stroke=RULE, sw=1, dash=None, rx=3):
    d = f' stroke-dasharray="{dash}"' if dash else ""
    add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" fill="{fill}" stroke="{stroke}" stroke-width="{sw}"{d}/>')

def node(x, y, w, h, title, sub=None, color=CYAN, code=None, tag=None):
    """One pipeline step: title line, optional code line, optional sub line."""
    rect(x, y, w, h, fill=PANEL2, stroke=color, sw=1.2)
    add(f'<rect x="{x}" y="{y}" width="4" height="{h}" fill="{color}"/>')
    ty = y + 19
    text(x + 13, ty, title, 13.5, TXT, "bold")
    if code:
        ty += 17; text(x + 13, ty, code, 11, color, font=MONO)
    if sub:
        for line in (sub if isinstance(sub, list) else [sub]):
            ty += 16; text(x + 13, ty, line, 11.5, DIM)
    if tag:
        tx = x + w - 8
        for t in reversed(tag):
            tw = 9 + 7.4 * len(t)
            tx -= tw
            rect(tx, y + 6, tw, 18, fill="#2a1d07", stroke=AMBER, sw=1, rx=2)
            text(tx + tw / 2, y + 19, t, 11, AMBER, "bold", "middle", MONO)
            tx -= 5

def arrow(pts, color=GREEN, label=None, lx=None, ly=None, dash=None, la="middle", lsize=11.5, lcol=None, width=1.8):
    d = "M " + " L ".join(f"{a},{b}" for a, b in pts)
    ds = f' stroke-dasharray="{dash}"' if dash else ""
    mid = {GREEN: "ag", CYAN: "ac", PURPLE: "ap", AMBER: "aa", RED: "ar", DIM: "ad"}[color]
    add(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="{width}"{ds} marker-end="url(#{mid})"/>')
    if label:
        text(lx, ly, label, lsize, lcol or color, anchor=la)

def zone(x, y, w, h, title, color, kicker):
    rect(x, y, w, h, fill=PANEL, stroke=color, sw=1, rx=4)
    text(x + 14, y + 22, kicker, 10.5, color, "bold", ls=1.6)
    text(x + 14, y + 42, title, 16, TXT, "bold")

add(f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">')
add("<defs>")
for mid, c in (("ag", GREEN), ("ac", CYAN), ("ap", PURPLE), ("aa", AMBER), ("ar", RED), ("ad", DIM)):
    add(f'<marker id="{mid}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
        f'<path d="M0,0 L10,5 L0,10 z" fill="{c}"/></marker>')
add('<pattern id="grid" width="24" height="24" patternUnits="userSpaceOnUse"><path d="M24 0H0V24" fill="none" stroke="#0d151d" stroke-width="1"/></pattern>')
add("</defs>")
add(f'<rect width="{W}" height="{H}" fill="{BG}"/><rect width="{W}" height="{H}" fill="url(#grid)"/>')

# ── title
text(30, 44, "EMERGENCE END-TO-END BENCHMARK", 24, GREEN, "bold", ls=2)
text(30, 68, "System architecture · process mode · one run = one synthetic world seed · research/e2e_ingest", 14, DIM)

# ── gold line (scorer only)
arrow([(150, 112), (150, 92), (1662, 92), (1662, 112)], DIM, dash="6 5",
      label="Gold (make_gold) goes to the scorer only; it never crosses to sites, HQ or the fabric",
      lx=900, ly=86, lcol=DIM)

# ── 1 WORLD
zone(24, 112, 252, 470, "World generator", PURPLE, "1 · HARNESS")
node(38, 172, 224, 66, "build_org(seed)", code="org.py", sub="309 users · 35 teams · 7 sites", color=PURPLE)
node(38, 248, 224, 66, "build_corpus", code="corpus.py", sub="12,964 records · 180 days", color=PURPLE)
node(38, 324, 224, 84, "make_gold", code="corpus.py", sub=["40 real patterns:", "22 common · 18 rare"], color=PURPLE)
node(38, 418, 224, 66, "Decoys", sub=["50 planted · 5 types"], color=PURPLE)
text(38, 512, "dev seeds 0–5 · world size 200", 11.5, DIM)
text(38, 530, "held-out 94000–94009 sealed", 11.5, RED)
text(38, 556, "pack: emergence (config hash pinned)", 11, DIM2, font=MONO)

# ── 2 SITES
zone(310, 112, 420, 470, "Sites × 7 (r00 … r06)", CYAN, "2 · PRIVATE EDGE · ONE PROCESS EACH")
for k in (2, 1):  # stacked cards imply seven
    rect(326 + k * 6, 166 + k * 6, 388, 354, fill="#0a1118", stroke="#16303d", sw=1)
rect(326, 166, 388, 354, fill="#0b1219", stroke=CYAN, sw=1.2)
text(340, 186, "SITE r0k · site_graph.py · child process", 11.5, CYAN, "bold", font=MONO)
node(340, 196, 360, 46, "EdgeSite.ingest", sub="own records only · counts checked · 0 loss", color=CYAN)
node(340, 254, 172, 62, "Lexical extraction", sub="pack record interface", color=CYAN)
node(528, 254, 172, 62, "NeuralGraph", code="SQLite · hash embed", sub="1 node per record", color=CYAN)
node(340, 328, 172, 62, "Weekly cells", sub="k-suppressed", color=CYAN, tag=["nd"])
node(528, 328, 172, 76, "Tesseract", code="AsOfStorage · L=50", sub=["retrieval (site-local)"], color=CYAN)
node(528, 416, 172, 62, "Lexical judge", sub="→ verdict (Qwen: later)", color=CYAN)
arrow([(426, 242), (426, 254)], CYAN); arrow([(614, 242), (614, 254)], CYAN)
arrow([(426, 316), (426, 328)], CYAN); arrow([(614, 316), (614, 328)], CYAN)
arrow([(614, 404), (614, 416)], CYAN)
rect(340, 488, 360, 24, fill="#071a14", stroke=GREEN, sw=1, dash="5 4")
text(520, 504, "BOUNDARY · only cells, verdicts, descriptor leave", 11, GREEN, "bold", "middle")
text(326, 552, "Raw records and graph nodes never cross the boundary.", 11.5, DIM)
text(326, 570, "Logical site ids are never wildcard authorization.", 11.5, DIM)

# world -> sites
arrow([(276, 219), (338, 219)], GREEN)
add('<text x="297" y="330" font-family="DejaVu Sans, sans-serif" font-size="11" fill="#3ee08a" text-anchor="middle" transform="rotate(-90 297 330)">records → their site</text>')

# ── 3 HQ
zone(768, 112, 432, 470, "HQ · question routing", PURPLE, "3 · COORDINATOR (sees cells + verdicts only)")
node(784, 166, 400, 48, "CollectiveStore", sub="receive log of every site's weekly cells", color=PURPLE)
node(784, 224, 400, 56, "Detectors · detect(as_of)", code="detectors.py · top-n · tie salt", color=PURPLE, tag=["rs", "h1"])
node(784, 290, 400, 56, "Candidates → questions", code="build_question · pushdown", color=PURPLE, tag=["n2", "n1"])
node(784, 356, 400, 70, "TesseractCoordinator.execute", code="Router.select · arm R rank fusion",
     sub="budget m = 3 sites per question", color=PURPLE)
node(784, 436, 400, 52, "Intake → gate.evaluate", sub="status: supported / hypothesis", color=PURPLE)
node(784, 498, 400, 52, "HQ store", code="pd_questions · pd_routes · pd_verdicts · pd_conclusions", color=PURPLE)
for y0, y1 in ((214, 224), (280, 290), (346, 356), (426, 436), (488, 498)):
    arrow([(984, y0), (984, y1)], PURPLE)
text(784, 570, "route_selected precedes retrieval_selected for every question", 11, DIM2, font=MONO)

# sites <-> HQ
arrow([(512, 352), (742, 352), (742, 190), (784, 190)], GREEN)
text(748, 286, "cells", 11, GREEN, anchor="start")
arrow([(784, 384), (700, 384)], AMBER, label="question", lx=742, ly=377, lsize=11)
arrow([(700, 450), (754, 450), (754, 462), (784, 462)], AMBER, label="verdict", lx=742, ly=444, lsize=11)
text(706, 418, "WIRE", 10.5, AMBER, "bold", font=MONO)

# ── 4 FABRIC
zone(1238, 112, 300, 470, "Mycelic fabric", AMBER, "4 · REAL SERVICE + BROKER")
node(1252, 166, 272, 52, "hq_bridge", code="POST /events · SDK", sub=None, color=AMBER)
node(1252, 228, 272, 48, "MycelicService", code="ingest_events → outbox", color=AMBER)
node(1252, 286, 272, 48, "HMAC-signed publish", code="keyed integrity", color=AMBER)
node(1252, 344, 272, 48, "NATS JetStream", code="nats-server 2.10.29", color=AMBER)
node(1252, 402, 272, 48, "Consumer verifies → apply", code="rules.json · min_support 2", color=AMBER)
node(1252, 460, 272, 62, "Conclusions + lineage", code="source_event_ids", sub="verdict & conclusion events only", color=AMBER)
for y0, y1 in ((218, 228), (276, 286), (334, 344), (392, 402), (450, 460)):
    arrow([(1388, y0), (1388, y1)], AMBER)
# HQ store -> bridge
arrow([(1184, 524), (1216, 524), (1216, 192), (1252, 192)], GREEN)
add('<text x="1229" y="380" font-family="DejaVu Sans, sans-serif" font-size="11" fill="#3ee08a" text-anchor="middle" transform="rotate(-90 1229 380)">read by watermark</text>')

# ── 5 SCORER
zone(1576, 112, 200, 470, "Scorer", GREEN, "5 · report.py")
text(1590, 178, "Inputs", 12, DIM, "bold")
for i, s in enumerate(["fabric API + lineage", "HQ receive log", "site egress + verdict logs", "Gold"]):
    text(1594, 198 + i * 18, "· " + s, 11.5, TXT)
node(1590, 280, 172, 98, "Discovery score", sub=["found / gold_discoverable", "recall common / rare", "decoys accepted by type"], color=GREEN)
node(1590, 390, 172, 70, "Checks", code="c0 c7 c8 c9", sub="fabric · guards · load", color=GREEN)
node(1590, 472, 172, 62, "Report JSON", code="e2e-ingest-report", sub="+ sha256", color=GREEN)
arrow([(1676, 378), (1676, 390)], GREEN); arrow([(1676, 460), (1676, 472)], GREEN)
arrow([(1524, 491), (1556, 491), (1556, 194), (1588, 194)], GREEN)

# ── 6 EVALUATION band
zone(24, 622, 1752, 330, "Evaluation protocol", GREEN, "6 · ABLATIONS, TRACKS, GATES")
bx = [(40, 300, "ablate.py arms", ["pack overrides per arm", "same code, same worlds"], CYAN),
      (360, 290, "Dev seeds 0–5", ["6 worlds per config", "pooled found / gold"], CYAN),
      (670, 380, "Two tracks, reported separately", ["A · frozen R0 (clean)", "B · EMERGENCE-Next (h1, nd, n1, n2, rs)"], PURPLE),
      (1070, 330, "Qwen gate", ["best Track B ≥ 70% on all 6 dev seeds", "then held-out + live judge"], AMBER),
      (1420, 340, "Swarm console", ["events.jsonl → relay → console", "every metric cites its report"], GREEN)]
for x, w, t, sub, c in bx:
    node(x, 684, w - 20, 84, t, sub=sub, color=c)
for (x, w, *_), (nx, *_ ) in zip(bx, bx[1:]):
    arrow([(x + w - 20, 726), (nx, 726)], GREEN)
arrow([(1676, 534), (1676, 600), (300, 600), (300, 684)], GREEN)
text(940, 594, "one report per (config, seed)", 11, GREEN, anchor="middle")

# results strip
text(40, 806, "MEASURED · dev seeds, synthetic worlds, deterministic lexical judge", 11.5, GREEN, "bold", ls=0.6)
cols = [("clean (A)", "38.8%", "93/240", DIM), ("h1n (B)", "58.3%", "140/240", PURPLE),
        ("v2-h1n_nd_n1 (B)", "62.9%", "151/240 · 6 seeds", PURPLE), ("v4-rs_nd_n1_n2 (B)", "69.0%", "138/200 · seeds 0–4", AMBER)]
for i, (n, p, f, c) in enumerate(cols):
    x = 40 + i * 300
    rect(x, 818, 280, 62, fill=PANEL2, stroke=RULE)
    text(x + 12, 838, n, 12, DIM, font=MONO)
    text(x + 12, 868, p, 24, c, "bold")
    text(x + 110, 866, f, 11.5, DIM)
x = 1250
rect(x, 818, 510, 62, fill="#1c0a0d", stroke=RED)
text(x + 12, 838, "BOTTLENECK: rare-pattern recall", 12, RED, "bold")
text(x + 12, 858, "common ≈ 0.98 · rare 0.16 (v2) → 0.25 (v4)", 12, TXT)
text(x + 12, 874, "70% needs rare ≈ 0.36 · 80% needs ≈ 0.58 · failing stage under diagnosis", 11, DIM)
text(40, 906, "NOT RUN · held-out confirmation (seeds 94000–94009)      NOT MEASURED · live Qwen judge, FDR, model tokens/cost", 11.5, RED)
text(40, 926, "Tesseract question→verdict latency (v2, recorded): p50 ≈ 2.1 s · p95 ≈ 3.3 s · ~86 questions and ~260 site pairs per run", 11.5, DIM)

# ── legend
ly = 980
text(30, ly, "LEGEND", 11, DIM, "bold", ls=1.4)
items = [(GREEN, "data flow"), (AMBER, "WIRE question / verdict"), (DIM, "Gold (scorer only)")]
lx = 110
for c, s in items:
    dash = ' stroke-dasharray="6 5"' if c == DIM else ""
    add(f'<line x1="{lx}" y1="{ly-4}" x2="{lx+34}" y2="{ly-4}" stroke="{c}" stroke-width="2"{dash}/>')
    text(lx + 42, ly, s, 11.5, TXT); lx += 60 + 7.2 * len(s) + 30
rect(lx, ly - 15, 26, 16, fill="#2a1d07", stroke=AMBER, rx=2); text(lx + 13, ly - 3, "h1", 10.5, AMBER, "bold", "middle", MONO)
text(lx + 34, ly, "Track B lever: h1 detector warm-up · rs rule snapshots · n1 weak-site corroboration · n2 roots from k sites · nd near-dup roots", 11.5, TXT)
text(30, ly + 26, "Source: emergence-rule docs/collective/E2E-INGEST.md §1, §3.5, §3.6, §4 · numbers from scratchpad/em/*/e2e-ingest-report.json · 2026-10-24",
     11, DIM2)

add("</svg>")
open(__import__("os").path.join(__import__("os").path.dirname(__file__), "benchmark_arch.svg"), "w").write("\n".join(out))
print("ok")
