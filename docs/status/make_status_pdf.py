"""Build docs/STATUS_2026-10-08.pdf: current results, the NeuralGraph -> Tesseract
audit, and questions for the chief research engineer."""
import sys
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (KeepTogether, PageBreak, Paragraph, SimpleDocTemplate,
                                Spacer, Table, TableStyle)

OUT = sys.argv[1]
F = "/usr/share/fonts/truetype/dejavu/"
pdfmetrics.registerFont(TTFont("DV", F + "DejaVuSans.ttf"))
pdfmetrics.registerFont(TTFont("DVB", F + "DejaVuSans-Bold.ttf"))
pdfmetrics.registerFont(TTFont("DVM", F + "DejaVuSansMono.ttf"))
from reportlab.pdfbase.pdfmetrics import registerFontFamily
registerFontFamily("DV", normal="DV", bold="DVB", italic="DV", boldItalic="DVB")

ss = getSampleStyleSheet()
INK = colors.HexColor("#1f2933")
MUTED = colors.HexColor("#52606d")
ACC = colors.HexColor("#0b6e4f")
BAD = colors.HexColor("#a61b1b")
RULE = colors.HexColor("#cbd2d9")
BAND = colors.HexColor("#f0f4f8")
body = ParagraphStyle("b", parent=ss["Normal"], fontName="DV", fontSize=9.2, leading=12.6,
                      textColor=INK, alignment=TA_LEFT, spaceAfter=4)
small = ParagraphStyle("s", parent=body, fontSize=8, leading=10.6, textColor=MUTED)
cell = ParagraphStyle("c", parent=body, fontSize=7.8, leading=10, spaceAfter=0)
cellb = ParagraphStyle("cb", parent=cell, fontName="DVB")
h1 = ParagraphStyle("h1", parent=body, fontName="DVB", fontSize=17, leading=21, spaceAfter=6, textColor=INK)
h2 = ParagraphStyle("h2", parent=body, fontName="DVB", fontSize=12.5, leading=16, spaceBefore=10,
                    spaceAfter=5, textColor=ACC)
h3 = ParagraphStyle("h3", parent=body, fontName="DVB", fontSize=9.8, leading=13, spaceBefore=6, spaceAfter=2)
q = ParagraphStyle("q", parent=body, fontName="DVB", fontSize=9.6, leading=13, spaceBefore=7, spaceAfter=2)
code = ParagraphStyle("code", parent=body, fontName="DVM", fontSize=7.4, leading=9.6, textColor=INK,
                      backColor=BAND, borderPadding=4, spaceBefore=3, spaceAfter=6)
bullet = ParagraphStyle("bl", parent=body, leftIndent=10, bulletIndent=2, spaceAfter=2)

S = []
P = lambda t, st=body: S.append(Paragraph(t, st))


def bl(items):
    for t in items:
        S.append(Paragraph(t, bullet, bulletText="•"))


def table(rows, widths, head=True, zebra=True, bold_first_col=False):
    data = []
    for i, r in enumerate(rows):
        data.append([Paragraph(str(c), cellb if (head and i == 0) or (bold_first_col and j == 0) else cell)
                     for j, c in enumerate(r)])
    t = Table(data, colWidths=[w * mm for w in widths], repeatRows=1 if head else 0)
    st = [("VALIGN", (0, 0), (-1, -1), "TOP"),
          ("LINEBELOW", (0, 0), (-1, 0), 0.8, INK),
          ("LINEBELOW", (0, -1), (-1, -1), 0.5, RULE),
          ("TOPPADDING", (0, 0), (-1, -1), 2.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
          ("LEFTPADDING", (0, 0), (-1, -1), 3), ("RIGHTPADDING", (0, 0), (-1, -1), 3)]
    if zebra:
        for i in range(1, len(rows)):
            if i % 2 == 0:
                st.append(("BACKGROUND", (0, i), (-1, i), BAND))
    t.setStyle(TableStyle(st))
    S.append(t)
    S.append(Spacer(1, 5))


# ------------------------------------------------------------------ title
P("Mycelic + NeuralGraph: where the accuracy actually stands", h1)
P("Status report, 8 October 2026 · branch <font name='DVM'>claude/vnext-accuracy-70-percent-gsjg8m</font> "
  "(latest results commit <font name='DVM'>9d4711f</font>) · every number below is from a committed artifact "
  "unless marked otherwise", small)
S.append(Spacer(1, 6))

P("Bottom line", h2)
bl([
    "<b>Simulator, 10,000 users:</b> Mycelic v4 finds <b>71%</b> of the hidden cross-org problems on seeds no one had "
    "read (0.713, 95% CI 0.678–0.750), up from 62.5%. The strongest centralised baseline (A2) finds <b>74%</b> on "
    "the same worlds, and <b>80%</b> vs Mycelic's 58% at 50,000 users. Centralised still wins on raw accuracy.",
    "<b>Real LLM agents (first run):</b> 429 employee agents, each a real Qwen3-1.7B call on its own notes, one "
    "400-user world. Mycelic v4 finds <b>65%</b> (60% backed by real evidence); central triage on the same claims 45%. "
    "<b>The centralised A2 reader on the same model finds 87.5%</b>, with about a third of the model tokens "
    "(313k vs 919k). Mycelic accepts fewer decoys (0.35 vs 0.525) and moves no raw text. One world, 40 patterns: "
    "indicative, not a claim, but centralised wins this one plainly.",
    "<b>The biggest live failure is not discovery, it is negation.</b> The small model read 2 of 43 negated notes. "
    "Retractions vanish, so every stale-chain decoy got through (D5 acceptance 1.00 vs 0.20 in the simulator).",
    "<b>NeuralGraph memory (LoCoMo benchmark):</b> 72.2% with a lenient same-family judge (744 of 1,540 questions), "
    "42.5% with a strict independent judge. Half the benchmark is unrun.",
    "<b>Your ingestion layer does not feed Tesseract.</b> The production memory server (<font name='DVM'>chat_memory</font>), "
    "the research service and the Tesseract retriever are three separate paths, and the only path that reaches "
    "Tesseract stamps every memory with the ingestion time instead of when it happened (section 4).",
])

# ------------------------------------------------------------------ accuracy
P("1. Every accuracy number we have", h2)
P("<b>found</b> = share of hidden cross-department patterns that end up in the 600-entry risk register. "
  "<b>Backed</b> = found with at least half of the pattern's links supported by its own evidence records "
  "(excludes coincidental matches). Decoys = planted look-alike patterns accepted.", small)
table([
    ["System", "Setting", "found", "backed", "decoys", "stale chains", "Source"],
    ["Mycelic v3 (frozen vNext)", "sim 10k, seeds 15–29", "0.625", "–", "0.375", "0.767", "quick_v4_heldout"],
    ["<b>Mycelic v4</b>", "sim 10k, seeds 15–29 (pre-registered)", "<b>0.713</b> [0.678, 0.750]", "–", "0.268", "0.273", "quick_v4_heldout"],
    ["Mycelic v4", "sim 10k, seeds 0–29 pooled", "0.703 [0.678, 0.728]", "–", "0.261", "0.243", "quick_v4_heldout"],
    ["A2 central long-context", "sim 10k, seeds 0–29", "<b>0.743</b>", "–", "–", "–", "quick_v4_heldout"],
    ["Mycelic v4", "sim 50k, seeds 0–2", "0.583", "–", "0.253", "0.27", "quick_v4_heldout"],
    ["A2 central long-context", "sim 50k, seeds 0–2", "<b>0.797</b>", "–", "–", "–", "quick_v4_heldout"],
    ["Mycelic v4", "<b>live</b> Qwen3-1.7B, 400 users, seed 702", "<b>0.650</b>", "0.600", "0.350", "<font color='#a61b1b'><b>1.00</b></font>", "stage_400_s702"],
    ["Mycelic v3", "live, same world", "0.575", "0.500", "0.325", "0.70", "stage_400_s702"],
    ["Central triage (B4)", "live, same world", "0.450", "0.450", "0.350", "0.90", "stage_400_s702"],
    ["<b>A2, same 1.7B model</b>", "live, same world", "<b>0.875</b>", "0.875", "0.525", "0.80", "stage_400_s702_A2_matched"],
], [33, 40, 26, 15, 14, 18, 28])
P("Live vs simulated operators on that same world: Mycelic v4 0.55 → 0.65, v3 0.60 → 0.575, central triage "
  "0.575 → 0.45, A2 0.925 → 0.875. Simulator decoy acceptance (0.10–0.23 for the hierarchy) badly understates live "
  "decoy acceptance (0.33–0.35). Live model cost on this world: Mycelic 429 calls, 754k prompt + 165k output tokens, "
  "~5.0 h of this CPU; A2 143 calls, 255k + 58k tokens, ~1.5 h. A2 reads only the notes its keyword prefilter "
  "flags as causal, which is exact on this generator's template text and would not be on real prose.", small)

P("NeuralGraph memory on LoCoMo (conversational memory QA)", h3)
table([
    ["Judge", "Questions", "single", "multi", "temporal", "open", "overall"],
    ["Gemma, lenient (same family as answerer)", "744 (conv 1–5)", "73.9", "72.8", "73.7", "56.5", "<b>72.2</b>"],
    ["Qwen, lenient (independent)", "584 (conv 1–4)", "62.2", "66.6", "71.5", "43.8", "65.6"],
    ["Qwen, strict (independent)", "584 (conv 1–4)", "33.3", "41.8", "60.8", "6.2", "<b>42.5</b>"],
    ["substring match", "584 (conv 1–4)", "21.6", "27.0", "36.2", "3.1", "26.7"],
], [52, 24, 18, 18, 17, 20, 16])
P("The same answer files score 13.5% to 64.9% depending only on the grader. The only gain that survives every "
  "judge is per-agent memory routing (+9 points single-hop). Conversations 6–10 (796 questions) have never been run.", small)

P("Live extraction quality (16,233 notes, Qwen3-1.7B vs the simulator's assumed 7B model)", h3)
table([
    ["", "simulator (7B-class operator)", "real Qwen3-1.7B", ""],
    ["Notes that yield a claim", "0.70", "0.96", "better"],
    ["Invented claims per note", "0.18", "0.04", "better"],
    ["Right entity", "0.84", "0.99", "better"],
    ["Gold-pattern notes read exactly right", "0.53", "0.65", "better"],
    ["Wrong event type on causal notes", "0.09", "0.30", "<font color='#a61b1b'>worse</font>"],
    ["Negated notes marked negated (smoke, 43 notes)", "≈1.00", "0.05", "<font color='#a61b1b'>much worse</font>"],
    ["Echo (duplicate-report) detection from text", "event id, 92% clean", "1.00 pair recall", "better"],
], [70, 32, 32, 30])

# ------------------------------------------------------------------ what is real
P("2. What is real and what is still simulated", h2)
table([
    ["Component", "Simulator", "Live run today"],
    ["Employee agents reading their notes", "coin flips on the hidden truth", "<b>real model call</b> per agent"],
    ["Duplicate/echo detection", "hidden event id (8% noise)", "<b>computed from note text</b>"],
    ["Routing, sketch, triage, merging", "code (by design)", "same code"],
    ["Answering kernel questions", "filter on extracted claims", "same (no fresh model read yet)"],
    ["Kernel chain synthesis + verification", "code with simulated checks", "<b>still simulated</b>"],
    ["Ranking the 600-entry register", "learned ranker", "same ranker (fitted on simulator)"],
    ["Gold answers", "evaluator only", "evaluator only (tested)"],
], [55, 55, 55])
P("So today's live numbers test whether discovery survives <b>real evidence extraction</b>, not whether a model "
  "can make the discovery leap itself.", small)

# ------------------------------------------------------------------ mycelic routing
P("3. Mycelic routing is not Dijkstra (yet)", h2)
P("You think of the routing as Dijkstra's algorithm. The code does something simpler. A kernel question descends "
  "the org tree; at each level it goes to at most 3 children, chosen greedily by "
  "<font name='DVM'>2 × (causal mentions of the entity) + (all mentions)</font> in each child's index "
  "(<font name='DVM'>research/mycelic/systems.py</font>, <font name='DVM'>Hierarchy.descend</font>). There is "
  "no path cost, no priority queue and no global optimum. That matters: 19% of rare evidence records are never "
  "reached by a descent, and the descent fan-out experiments (3 → 5 → 7) were inside noise because a greedy rule "
  "spends its budget where mentions are loud, not where the missing link is likely. Question 3 below.")

# ------------------------------------------------------------------ neuralgraph audit
P("4. Does NeuralGraph ingestion produce memories Tesseract can absorb?", h2)
P("<b>No.</b> There are three disconnected memory paths, and the one that reaches Tesseract has a timestamp fault.")
table([
    ["#", "Finding", "Where", "Effect"],
    ["F1", "Production ingestion writes its own SQLite store (Memory: event_time, observed_at, superseded_by). "
           "Nothing reads it into Tesseract or into Mycelic.", "NeuralGraph/chat_memory/*", "Memories users create never reach Tesseract."],
    ["F2", "Research service ingests with correct message dates but never calls Tesseract's retriever "
           "(imports only a keyword helper).", "research/retrieval/service.py:61, :558", "Correct timestamps, wrong retriever."],
    ["F3", "The only Tesseract path (benchmark runner) stamps created_at = datetime.now(); the real date "
           "lives only in metadata['datetime'].", "research/benchmarks/runner.py:651, :467", "Every memory looks like it happened at ingestion."],
    ["F4", "Tesseract resolves 'last year' from created_at.year first, metadata second.", "tesseract.py:1250–1255",
     "Relative years anchor to 2026, not 2022–24 (259 of 5,882 LoCoMo messages use relative time)."],
    ["F5", "No reference time is passed to retrieve(); recency term is a constant 0.5.", "runner.py:769; tesseract.py:1233",
     "Recency never ranks anything."],
    ["F6", "Episode segmentation uses gaps between created_at values.", "research/retrieval/hierarchy.py:367–376",
     "With ingestion timestamps all gaps are ~0: episodes are meaningless on that path."],
    ["F7", "Consolidation decay uses wall-clock age since update.", "research/retrieval/consolidation.py:255",
     "A bulk-imported history all looks equally fresh."],
    ["F8", "Naive and timezone-aware datetimes are mixed (runner naive; NeuralNode default aware UTC).", "runner.py:651; data_types.py",
     "Latent crash the first time they are subtracted."],
    ["F9", "Tesseract only searches message nodes; consolidated episode/topic/persona memories are never queried.",
     "tesseract.py (no layer handling)", "Consolidation work is invisible to retrieval."],
], [8, 72, 44, 46])
P("Temporal synchronisation on the Mycelic side: the simulator assumes every site stamps events on one shared "
  "day clock; v4's biggest gain (same-day witness clusters) depends on it. Real sites have time zones, reporting "
  "delay and batch uploads. Not yet tested.", small)

# ------------------------------------------------------------------ missing
P("5. What is missing (the short list)", h2)
bl([
    "<b>One temporal contract for the whole stack.</b> chat_memory already has the right model (event time with "
    "precision, observed time, superseded-by). Tesseract and Mycelic each invent their own and lose it.",
    "<b>Retraction as a first-class signal.</b> chat_memory has <font name='DVM'>superseded_by</font> and audited "
    "forgetting; Mycelic needs exactly that and currently relies on a small model noticing the word 'not'.",
    "<b>The bridge between personal memory and the enterprise hierarchy.</b> A Mycelic employee agent should "
    "<i>be</i> that employee's NeuralGraph memory. Today they are different codebases with different data models.",
    "<b>A reason to distribute for accuracy.</b> In the benchmark every note is self-contained, so a central "
    "reader with all the text has strictly more information. Distribution can only tie or lose on accuracy unless "
    "local agents know something the centre cannot.",
    "<b>A model at the kernel.</b> Discovery decisions are still code; the live run cannot yet tell us whether "
    "an LLM kernel would find more or hallucinate more.",
])

S.append(PageBreak())
# ------------------------------------------------------------------ questions
P("6. Questions for the chief research engineer", h2)
P("Ordered by expected impact per hour of work. Each says why it matters, the evidence, and the cheapest test "
  "(all free on local models unless noted).", small)

QS = [
    ("Q1. Should every Mycelic employee agent simply be a NeuralGraph chat_memory instance?",
     "chat_memory already extracts structured memories with event time, confidence and <i>superseded_by</i>. "
     "Mapping Memory → Mycelic claim (subject → entity, kind → event type, event_time → day, superseded → "
     "retraction) is an adapter, not a redesign. It would give Mycelic the retraction signal the live run is "
     "missing, and give NeuralGraph its enterprise layer.",
     "Test: adapter + rerun the 400-agent world with claims from chat_memory's extraction prompt; watch stale-chain decoys (1.00 today)."),
    ("Q2. Can we adopt one bitemporal memory contract everywhere (valid time vs recorded time)?",
     "Findings F2–F8 are all the same bug: 'when it happened', 'when we heard it' and 'when we stored it' are "
     "conflated. A retraction then becomes 'valid until', which is exactly what stale-chain detection needs.",
     "Test: set created_at from the message date in the benchmark runner, prefer metadata time in Tesseract, "
     "rerun LoCoMo temporal questions (321) under the strict judge."),
    ("Q3. Should routing become real best-first search: Dijkstra/A* with a learned 'evidence likely here' heuristic?",
     "Today it is a greedy 3-child descent by mention counts (section 3); 19% of rare facets are never reached "
     "and widening the fan-out did nothing. A priority-queue search whose edge cost is communication and whose "
     "heuristic is the sketch/Bloom estimate of the <i>missing</i> link spends the same budget where the answer is.",
     "Test: same question budget, A* vs greedy descent, measure descent reach and found on tune seeds."),
    ("Q4. Should edge agents get a tiny verification tool: 'is this note denying or retracting X?'",
     "The live model read 2 of 43 negations; every stale-chain decoy was accepted. A rule-based negation check, "
     "or one yes/no follow-up call per flagged note, is the cheapest high-impact fix we have found. It is also a "
     "distributed advantage: each agent re-checks its own 30 notes; a central reader cannot afford to re-read millions.",
     "Test: add the tool, rerun the same 429 agents from cache for everything else; compare D5 and found."),
    ("Q5. Instead of ranking 3,000 candidates into 600 slots, can the kernel falsify them?",
     "Real cross-org problems propagate region to region; background noise does not. Ask the holders of a "
     "candidate's predicted next link in another region for yes/no + counts (no raw text). A wrong prediction "
     "kills the candidate. This attacks the register cut, the single biggest loss in the simulator, and coincidental matches.",
     "Test: one falsification round before the cut on tune seeds; track found, backed found and decoys."),
    ("Q6. Where is Mycelic supposed to beat centralised on accuracy, not just privacy?",
     "In this benchmark a central reader with all text has strictly more information, so A2 wins (0.74 vs 0.71 "
     "at 10k, 0.80 vs 0.58 at 50k). Real enterprises have context only the local agent has: abbreviations, "
     "history, who 'the vendor' is. If that is the thesis, the generator must contain context-dependent notes, or "
     "we will never measure the advantage.",
     "Test: add notes resolvable only with the author's earlier notes; compare Mycelic vs A2 at equal compute."),
    ("Q7. Is final recall even the right metric, or is it time-to-detection?",
     "Continual discovery is the point of the architecture: incremental local agents can flag an emerging "
     "pattern while a central batch reader is still re-reading. The benchmark only scores the end state.",
     "Test: stream the 180 simulated days, score the day each pattern first enters the register."),
    ("Q8. How do we keep the clocks honest across sites?",
     "v4's main gain is same-day witness clusters. With real time zones, reporting delays and batch uploads, "
     "those clusters smear. Estimating per-site offsets from shared events (NTP over evidence) is cheap.",
     "Test: add per-site clock offset and delay to the generator; rerun v4 vs A2."),
    ("Q9. What should the kernel model do when we add one: verify, or generate hypotheses?",
     "Synthesis is still code with simulated checks. A model that verifies candidates is safer; one that "
     "proposes chains could find what the 7 templates miss, but may hallucinate. We need a decision before building it.",
     "Test: LLM judge on the top 600 only (cheap), then on all candidates."),
    ("Q10. Which NeuralGraph number do we stand behind?",
     "72.2% (lenient, same family) and 42.5% (strict, independent) describe the same answers. Commit to the strict "
     "independent judge and finish conversations 6–10 before any claim leaves the team.",
     "Cost: about 2 hours of local model time; scripts exist (n5_rescore, capstone runner)."),
    ("Q11. Should Tesseract's fusion be rank-based instead of near-uniform weights?",
     "The temporal store dominates the top 10 for every question type, and 18 gold messages are dropped by "
     "filters before ranking. Rank fusion across stores was the one untested combination with signal.",
     "Test: reciprocal-rank fusion of the four stores on the 1,219-question retrieval set."),
]
for title, why, test in QS:
    S.append(KeepTogether([Paragraph(title, q), Paragraph(why, body), Paragraph("<i>" + test + "</i>", small)]))

# ------------------------------------------------------------------ next
P("7. What I would do next (free, local)", h2)
bl([
    "Finish the A2 live comparison (running) and add 2 more live worlds so the live table is not one world.",
    "Fix F3/F4/F5 (three lines) and rerun LoCoMo temporal questions under the strict judge.",
    "Add the negation tool (Q4) and rerun the 429 agents (cached calls replay; only the new check costs time).",
    "Run 2,000 and 10,000 agents on the MacBook Air (Metal) with research/mycelic/live/mac_setup.sh; this "
    "container needs ~47 h and ~234 h.",
    "Resume the v5 protocol (fair comparators, sealed final seeds 3000–3029) once helper agents are available again (Oct 12).",
])
P("Reproduce", h3)
S.append(Paragraph("python3 -m research.mycelic.live.run --stage 400        # live agents (needs a llama-server)<br/>"
                   "python3 -m research.mycelic.arm_paired ...               # simulator paired runs<br/>"
                   "python3 -m unittest research.mycelic.test_mycelic research.mycelic.test_leakage research.mycelic.live.test_live<br/>"
                   "artifacts: research/mycelic/artifacts/quick_v4_heldout.jsonl, research/mycelic/artifacts/live/", code))


def footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("DV", 7)
    canvas.setFillColor(MUTED)
    canvas.drawString(18 * mm, 10 * mm, "Mycelic + NeuralGraph status · 8 Oct 2026")
    canvas.drawRightString(A4[0] - 18 * mm, 10 * mm, f"page {doc.page}")
    canvas.restoreState()


doc = SimpleDocTemplate(OUT, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm,
                        bottomMargin=16 * mm, title="Mycelic + NeuralGraph: current results",
                        author="NeuralGraph research")
doc.build(S, onFirstPage=footer, onLaterPages=footer)
print("wrote", OUT)
