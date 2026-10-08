"""
Generates figures/architecture.svg — the Mycelic Invention Loop.

Deterministic: a fixed seed, so the figure is byte-reproducible and can be
regenerated from source rather than hand-edited. Everything is vector; the node
field is real (thousands of nodes placed by the layout, not a decorative
sprinkle), because the claim the figure makes is about density and distance.

Encoding rules (chosen so the figure survives greyscale printing):
  - activation is encoded by RADIUS and OPACITY first, hue second
  - the cross-domain path is encoded by STROKE WEIGHT and a halo, not hue alone
  - every marked element is also labelled in text
"""
import math, random

SEED = 20260918
random.seed(SEED)

W, H = 1480, 1030
GOLD = "#9A6A12"      # activated / Mycelic
BLUE = "#1064BE"      # cross-domain traversal
INK = "#15171B"
INK2 = "#4F555E"
INK3 = "#7A808A"
RULE = "#D8D3C8"
PAPER = "#FFFFFF"

# Font stacks. Lead with the faces a design machine will have, fall back to the
# metric-compatible ones present on a bare renderer so the exported PNG and the
# vector agree. Mono is reserved for identifiers and expressions.
SANS = '"IBM Plex Sans", "Liberation Sans", "Helvetica Neue", Helvetica, Arial, sans-serif'
MONO = '"IBM Plex Mono", "DejaVu Sans Mono", ui-monospace, SFMono-Regular, Menlo, monospace'

# Type scale (pt at the figure's native 1480 px width).
T_TITLE, T_PANEL, T_LABEL, T_NODE, T_SMALL, T_TINY = 18, 13.5, 12.5, 12, 11, 10.5

out = []
def add(s): out.append(s)

def esc(t):
    return (t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))

def text(x, y, s, size=T_NODE, fill=INK2, anchor="start", weight=400, style="normal", family="sans", tracking=None):
    fam = (MONO if family == "mono" else SANS)
    tr = f' letter-spacing="{tracking}"' if tracking else ""
    add(f'<text x="{x:.1f}" y="{y:.1f}" font-family=\'{fam}\' font-size="{size}" '
        f'fill="{fill}" text-anchor="{anchor}" font-weight="{weight}" '
        f'font-style="{style}"{tr}>{esc(s)}</text>')

# ---------------------------------------------------------------- panel (a)
AX, AY, AW, AH = 40, 74, 1400, 540

# (name, cx, cy, rx, ry, label_dy) — label_dy places the caption clear of the hull
DOMAINS = [
    ("materials",        210, 250, 108, 74, -1),
    ("fluid dynamics",   545, 210, 112, 62, -1),
    ("microbiology",     255, 495, 104, 66,  1),
    ("control theory",   925, 205, 110, 62, -1),
    ("photonics",       1245, 400,  98, 74, -1),
    ("manufacturing",    835, 508, 116, 58,  1),
]

nodes = []     # (x, y, domain_index)
for di, (name, cx, cy, sx, sy, _ldy) in enumerate(DOMAINS):
    n = 330 if di % 2 == 0 else 300
    for _ in range(n):
        # gaussian core with a heavier tail so domains bleed into each other
        r = random.gauss(0, 1)
        a = random.uniform(0, math.tau)
        rad = abs(random.gauss(1.0, 0.55))
        x = cx + math.cos(a) * rad * sx
        y = cy + math.sin(a) * rad * sy
        if AX + 8 < x < AX + AW - 8 and AY + 30 < y < AY + AH - 14:
            nodes.append((x, y, di))

# a thin interstitial haze so the field reads as one continuous substrate
for _ in range(420):
    x = random.uniform(AX + 10, AX + AW - 10)
    y = random.uniform(AY + 34, AY + AH - 16)
    nodes.append((x, y, -1))

print(f"panel (a): {len(nodes)} concept nodes")

# The unsolved problem sits between domains: no single cluster contains it.
PROB = (700, 335)
CAND = (700, 530)

def d(p, q):
    return math.hypot(p[0] - q[0], p[1] - q[1])

# Activation: falls off with distance from the problem seed, with noise so the
# front is ragged rather than a clean disc.
def activation(x, y):
    base = max(0.0, 1.0 - d((x, y), PROB) / 430.0)
    return max(0.0, min(1.0, base ** 1.7 * random.uniform(0.55, 1.45)))

add(f'<rect x="{AX}" y="{AY}" width="{AW}" height="{AH}" fill="{PAPER}" stroke="{RULE}"/>')

# substrate edges: short local links, drawn first and very faint
grid = {}
for i, (x, y, di) in enumerate(nodes):
    grid.setdefault((int(x // 40), int(y // 40)), []).append(i)
edges = []
for (gx, gy), bucket in grid.items():
    near = []
    for ox in (-1, 0, 1):
        for oy in (-1, 0, 1):
            near += grid.get((gx + ox, gy + oy), [])
    for i in bucket:
        xi, yi, _ = nodes[i]
        cnt = 0
        for j in near:
            if j <= i: continue
            xj, yj, _ = nodes[j]
            if math.hypot(xi - xj, yi - yj) < 26:
                edges.append((i, j)); cnt += 1
                if cnt >= 2: break
add(f'<g stroke="{INK3}" stroke-width="0.4" opacity="0.18">')
for i, j in edges:
    add(f'<line x1="{nodes[i][0]:.1f}" y1="{nodes[i][1]:.1f}" x2="{nodes[j][0]:.1f}" y2="{nodes[j][1]:.1f}"/>')
add('</g>')
print(f"panel (a): {len(edges)} substrate edges")

# nodes, painted back-to-front by activation so hot nodes sit on top
painted = sorted(((activation(x, y), x, y, di) for x, y, di in nodes), key=lambda t: t[0])
add('<g>')
for act, x, y, di in painted:
    if act < 0.12:
        add(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="1.5" fill="{INK3}" opacity="0.30"/>')
    else:
        r = 1.5 + act * 3.4
        op = 0.28 + act * 0.62
        add(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r:.2f}" fill="{GOLD}" opacity="{op:.2f}"/>')
add('</g>')

# domain boundaries + labels
add(f'<g fill="none" stroke="{INK3}" stroke-width="1" stroke-dasharray="2 5" opacity="0.5">')
for name, cx, cy, sx, sy, ldy in DOMAINS:
    add(f'<ellipse cx="{cx}" cy="{cy}" rx="{sx*1.18:.0f}" ry="{sy*1.18:.0f}"/>')
add('</g>')
for name, cx, cy, sx, sy, ldy in DOMAINS:
    ly = cy + (sy * 1.18 + 17) if ldy > 0 else cy - (sy * 1.18 + 9)
    add(f'<rect x="{cx-len(name)*3.9-7:.0f}" y="{ly-11:.0f}" width="{len(name)*7.8+14:.0f}" height="15" fill="{PAPER}" opacity="0.88"/>')
    text(cx, ly, name, size=T_LABEL, fill=INK2, anchor="middle", weight=500, tracking="0.02em")

# ---- the cross-domain paths: the actual argument of the figure.
# Each traverses a long distance through an intermediate domain, so its two ends
# are nowhere near each other in the embedding but are three hops apart here.
PATHS = [
    [(140, 268), (296, 222), (478, 214), (600, 282), PROB],
    [(1300, 412), (1108, 360), (940, 254), (800, 296), PROB],
    [(238, 520), (404, 496), (556, 424), (642, 372), PROB],
]
add(f'<g fill="none" stroke="{PAPER}" stroke-width="6.5" stroke-linecap="round" stroke-linejoin="round" opacity="0.92">')
for p in PATHS:
    add('<polyline points="' + " ".join(f"{x:.0f},{y:.0f}" for x, y in p) + '"/>')
add('</g>')
add(f'<g fill="none" stroke="{BLUE}" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round">')
for p in PATHS:
    add('<polyline points="' + " ".join(f"{x:.0f},{y:.0f}" for x, y in p) + '"/>')
add('</g>')
for p in PATHS:
    for k, (x, y) in enumerate(p[:-1]):
        add(f'<circle cx="{x}" cy="{y}" r="{5.2 if k == 0 else 3.8}" fill="{PAPER}" stroke="{BLUE}" stroke-width="2"/>')

# the problem node and the emitted candidate
add(f'<circle cx="{PROB[0]}" cy="{PROB[1]}" r="17" fill="{PAPER}" stroke="{INK}" stroke-width="2.6"/>')
add(f'<circle cx="{PROB[0]}" cy="{PROB[1]}" r="9" fill="{INK}"/>')
text(PROB[0], PROB[1] - 30, "unsolved problem", size=T_LABEL, fill=INK, anchor="middle", weight=600)

add(f'<path d="M{PROB[0]} {PROB[1]+20} L{CAND[0]} {CAND[1]-22}" stroke="{GOLD}" stroke-width="2.4" '
    f'marker-end="url(#arrowGold)" fill="none"/>')
add(f'<rect x="{CAND[0]-118}" y="{CAND[1]-22}" width="236" height="40" rx="3" fill="{PAPER}" '
    f'stroke="{GOLD}" stroke-width="2.2"/>')
text(CAND[0], CAND[1] + 5, "invention candidate + lineage", size=T_LABEL, fill=INK, anchor="middle", weight=600)

text(AX + 16, AY + 26, "(a)", size=T_PANEL + 1.5, fill=INK, weight=700)
text(AX + 44, AY + 24, "associative activation across domains — a candidate is a path, not a neighbour",
     size=T_PANEL, fill=INK2, weight=400)

# ---------------------------------------------------------------- panel (b)
BX, BY, BW, BH = 40, 644, 800, 344
add(f'<rect x="{BX}" y="{BY}" width="{BW}" height="{BH}" fill="{PAPER}" stroke="{RULE}"/>')
text(BX + 16, BY + 26, "(b)", size=T_PANEL + 1.5, fill=INK, weight=700)
text(BX + 44, BY + 24, "lineage of one candidate — every assertion resolves to a source locator",
     size=T_PANEL, fill=INK2, weight=400)

LANES = [
    (BY + 66,  "sources",   INK3),
    (BY + 140, "concepts",  INK2),
    (BY + 212, "operator",  GOLD),
    (BY + 286, "verdicts",  BLUE),
]
for ly, lname, lcol in LANES:
    add(f'<line x1="{BX+150}" y1="{ly}" x2="{BX+BW-24}" y2="{ly}" stroke="{RULE}" stroke-width="1" stroke-dasharray="1 4"/>')
    text(BX + 138, ly + 4, lname, size=T_SMALL, fill=lcol, anchor="end", weight=500, tracking="0.06em")

SRC = [("US-A 1", 218), ("US-B 2", 330), ("paper C", 442), ("null-result D", 574), ("standard E", 706)]
for label, x in SRC:
    add(f'<rect x="{BX+x-52}" y="{LANES[0][0]-15}" width="104" height="30" rx="2" fill="{PAPER}" stroke="{INK3}" stroke-width="1.3"/>')
    text(BX + x, LANES[0][0] + 4, label, size=T_SMALL, fill=INK2, anchor="middle", family="mono")

CON = [("mechanism m₁", 300), ("material x", 470), ("failure-mode f", 640)]
for label, x in CON:
    add(f'<rect x="{BX+x-64}" y="{LANES[1][0]-15}" width="128" height="30" rx="2" fill="{PAPER}" stroke="{INK}" stroke-width="1.4"/>')
    text(BX + x, LANES[1][0] + 4, label, size=T_SMALL, fill=INK, anchor="middle", family="mono")

LINKS = [(218, 268), (330, 268), (442, 442), (574, 616), (706, 442), (442, 616)]
add(f'<g stroke="{INK3}" stroke-width="1.1" fill="none" opacity="0.85">')
for sx, tx in LINKS:
    add(f'<path d="M{BX+sx} {LANES[0][0]+16} C{BX+sx} {LANES[0][0]+48} {BX+tx} {LANES[1][0]-48} {BX+tx} {LANES[1][0]-16}"/>')
add('</g>')

OPX = BX + 442
add(f'<rect x="{OPX-150}" y="{LANES[2][0]-17}" width="300" height="34" rx="2" fill="{PAPER}" stroke="{GOLD}" stroke-width="2"/>')
text(OPX, LANES[2][0] + 5, "transfer(m₁ : materials → manufacturing)", size=11.5, fill=INK, anchor="middle")
add(f'<g stroke="{GOLD}" stroke-width="1.3" fill="none">')
for _, x in CON:
    add(f'<path d="M{BX+x} {LANES[1][0]+16} L{OPX} {LANES[2][0]-18}"/>')
add('</g>')

CH = [("novelty", "challenged"), ("feasibility", "survived"), ("obviousness", "survived"), ("evidence fidelity", "survived")]
for k, (role, verdict) in enumerate(CH):
    x = BX + 132 + k * 180
    fill = "#FBF3E2" if verdict == "challenged" else PAPER
    add(f'<rect x="{x-82}" y="{LANES[3][0]-17}" width="164" height="34" rx="2" fill="{fill}" stroke="{BLUE}" stroke-width="1.5"/>')
    text(x, LANES[3][0] - 1, role, size=T_SMALL, fill=INK, anchor="middle", weight=600)
    text(x, LANES[3][0] + 12, verdict, size=T_TINY, fill=INK2, anchor="middle", style="italic")
    add(f'<path d="M{OPX} {LANES[2][0]+18} L{x} {LANES[3][0]-18}" stroke="{BLUE}" stroke-width="1.1" fill="none" opacity="0.8"/>')

text(BX + 24, BY + BH - 16,
     "independent challengers: separate contexts, no shared verdicts; a single sustained challenge blocks promotion",
     size=T_SMALL, fill=INK3, style="italic")

# ---------------------------------------------------------------- panel (c)
CX0, CY0, CW, CH2 = 862, 644, 578, 344
add(f'<rect x="{CX0}" y="{CY0}" width="{CW}" height="{CH2}" fill="{PAPER}" stroke="{RULE}"/>')
text(CX0 + 16, CY0 + 26, "(c)", size=T_PANEL + 1.5, fill=INK, weight=700)
text(CX0 + 44, CY0 + 24, "local discovery, gated promotion", size=T_PANEL, fill=INK2, weight=400)

TIERS = ["User", "Team", "Department", "Region / Subsidiary", "Global"]
for k, tname in enumerate(TIERS):
    y = CY0 + CH2 - 56 - k * 52
    w = 122 + k * 30
    x = CX0 + 34
    hot = k <= 2
    add(f'<rect x="{x}" y="{y-17}" width="{w}" height="34" rx="2" fill="{PAPER}" '
        f'stroke="{GOLD if hot else INK3}" stroke-width="{2 if hot else 1.3}"/>')
    text(x + 13, y + 5, tname, size=T_LABEL, fill=INK if hot else INK2, weight=500)
    if k < len(TIERS) - 1:
        add(f'<path d="M{x+w+14} {y-6} L{x+w+14} {y-38}" stroke="{GOLD if hot else INK3}" '
            f'stroke-width="1.8" fill="none" marker-end="url(#arrowGold)"/>')

add(f'<line x1="{CX0+300}" y1="{CY0+52}" x2="{CX0+300}" y2="{CY0+CH2-34}" stroke="{RULE}" stroke-width="1"/>')
text(CX0 + 320, CY0 + 92, "promoted only when", size=T_LABEL, fill=INK, weight=600)
for k, line in enumerate([
        "• the candidate clears its screen",
        "• no single unit below holds",
        "   more than half its evidence",
        "• lineage replays from the",
        "   pinned graph snapshot"]):
    text(CX0 + 300, CY0 + 138 + k * 17, line, size=10.5, fill=INK2, family="sans")
text(CX0 + 24, CY0 + CH2 - 16,
     "raw corpora and private problem statements never leave a unit",
     size=T_SMALL, fill=INK3, style="italic")

# ---------------------------------------------------------------- chrome
head = (f'<text x="42" y="38" font-family=\'{SANS}\' font-size="{T_TITLE}" '
        f'font-weight="600" fill="{INK}" letter-spacing="-0.005em">The Mycelic Invention Loop: '
        'distributed associative memory over a typed concept graph</text>')

legend = []
lx, ly = 44, 1012
def leg(x, sym, label, gap=16):
    legend.append(sym)
    legend.append(f'<text x="{x+gap}" y="{ly+4}" font-family=\'{SANS}\' '
                  f'font-size="{T_SMALL}" fill="{INK2}">{esc(label)}</text>')
leg(lx, f'<circle cx="{lx+5}" cy="{ly}" r="2" fill="{INK3}" opacity="0.5"/>', "inert concept")
leg(lx+150, f'<circle cx="{lx+155}" cy="{ly}" r="4.4" fill="{GOLD}" opacity="0.85"/>', "activated (radius ∝ activation)")
leg(lx+470, f'<line x1="{lx+470}" y1="{ly}" x2="{lx+496}" y2="{ly}" stroke="{BLUE}" stroke-width="2.4"/>', "cross-domain traversal", gap=34)
leg(lx+760, f'<ellipse cx="{lx+771}" cy="{ly}" rx="11" ry="6" fill="none" stroke="{INK3}" stroke-dasharray="2 4"/>', "domain", gap=26)

defs = (f'<defs><marker id="arrowGold" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" '
        f'markerHeight="6" orient="auto-start-reverse">'
        f'<path d="M0 0 L10 5 L0 10 z" fill="{GOLD}"/></marker></defs>')

svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" '
       f'font-family=\'{SANS}\'>'
       f'<rect width="{W}" height="{H}" fill="{PAPER}"/>' + defs + head
       + "".join(out) + "".join(legend) + '</svg>')

with open("architecture.svg", "w") as f:
    f.write(svg)
print(f"wrote architecture.svg  ({len(svg)/1024:.0f} KB)")
