"""Automated leakage audit for the Mycelic simulator.

Run: python3 -m unittest research.mycelic.test_leakage -v
     python3 -m research.mycelic.test_leakage --sites    (print every site)

"Hidden information" is anything the generator knows that a deployed system
would not observe: gold objects (``Pattern``, ``corpus.patterns``, ``Gold``),
per-record hidden fields (``kind``, ``veracity``, ``event``, ``group``,
``facet``, ``superseded``, ``salience``, and the true ``pred`` / ``anchor`` /
``t`` / ``polarity`` / ``aux`` read by anything other than a simulated
operator), the extraction operator's ``near_miss`` table, the
``hallucinated`` / ``spurious`` flags, and anything derived from them.  The
classified inventory is docs/mycelic_v5/ORACLE_AUDIT.md.

Three kinds of check:

1. Static (``TestStaticInventory``).  An AST scan of research/mycelic/*.py
   enumerates every access to a hidden field or object and compares it to
   ``leakage_allowlist.json``: site (file::function::token) -> expected count,
   category (SCORING | GENERATOR | OPERATOR-SIM | SHORTCUT) and a one-line
   justification.  A new site, or more accesses at a listed site, fails.
   The scan is a tripwire, not a proof: it sees attribute names, string
   subscripts on record arrays and a few names; it does not follow data
   through files or containers.  The metamorphic tests cover what it cannot.

2. Metamorphic (``TestMetamorphic``), on a 400-user world, tune seed 590.
   Each test changes something no decision may depend on and requires the
   architectures' decisions (the ranked register: anchor, links, confidence,
   chain, support, evidence pointers; plus compute units, call count and
   questions asked) to be identical:

   (a) Signature bijection.  Every ``sig`` the extraction operator returns is
       mapped through a random bijection of the int64 space.  Signatures are
       a simulated near-duplicate detector's output; decisions may use them
       only as identities (set union, cardinality).  Invariance must hold for
       every architecture.

   (b1) Never-read fields.  After world construction, the record columns
        ``kind``, ``veracity``, ``group``, ``facet``, ``superseded`` and
        ``salience`` are permuted, and ``corpus.patterns``,
        ``notable_records``, ``contradiction_pairs``, ``families``,
        ``dup_events``, ``entity_kind`` and ``world.gold`` are replaced by
        objects that raise on any use.  No operator reads these (the
        extraction operator reads pred / anchor / t / polarity / event / uid
        only), so this tests the decisions and the operators together and
        neither may depend on them.

   (b2) Operator-only fields.  The columns ``pred``, ``anchor``, ``t``,
        ``polarity``, ``event`` and ``aux`` are permuted, while the simulated
        operators (``ops.extract``) and the renderer (``Corpus.tokens`` /
        ``Corpus.text`` - the observable text) keep reading the pristine
        columns.  So operator outputs and the text are unchanged, and only a
        decision that reads a true field directly can change.  This is the
        test that separates a disclosed operator from a shortcut.  Known
        shortcuts fail it (S1, S2: marked expectedFailure) and their
        observable replacements (ops.OBSERVABLE) pass it.

   (c) Hallucination flag.  The ``hallucinated`` flag is cleared on every
       candidate as soon as the synthesis operator returns (the flag exists
       for the evaluator).  Decisions downstream of synthesis must not change.
       The current default fails it (S3, expectedFailure); the observable
       rule "a candidate with no evidence object is unsupported" passes.

3. Seed governance (``TestSealedSeeds``): ``protocol.check_world`` and
   ``runner.build_world`` refuse every sealed final world without
   MYCELIC_FINAL_EVAL=1, before anything is generated or logged.  Final seeds
   are refused at every scale (audit finding P1: 9,999 / 50,001 users rebuild
   the 10k / 50k worlds), and corpus.build_corpus checks as well, so callers
   that skip build_world are covered too.

Every expectedFailure cites its finding; ``test_known_shortcuts_are_detected``
checks that each one fails because the decisions differ, not by crashing.

This file is excluded from its own static scan (it reads hidden fields in
order to scramble them).
"""
from __future__ import annotations

import ast
import json
import os
import re
import sys
import unittest
from collections import defaultdict
from typing import Dict, List, Optional, Tuple
from unittest import mock

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ALLOWLIST = os.path.join(HERE, "leakage_allowlist.json")
CATEGORIES = ("SCORING", "GENERATOR", "OPERATOR-SIM", "SHORTCUT")
SELF = os.path.basename(__file__)

# ---------------------------------------------------------------------------
# 1. Static inventory
# ---------------------------------------------------------------------------

# per-record hidden fields (the true pred/anchor/t/polarity/aux count as
# hidden when read outside an operator; an operator site is allowlisted)
HIDDEN_REC_FIELDS = {"kind", "veracity", "event", "group", "facet",
                     "superseded", "salience", "pred", "anchor", "t",
                     "polarity", "aux"}
# attributes that only gold / generator / simulator-flag objects carry
HIDDEN_ATTRS = {
    "patterns", "gold", "discoverable", "independent_sources", "top_supported",
    "notable_records", "contradiction_pairs", "dup_events", "entity_kind",
    "facet_records", "facet_users", "facet_times", "decoy_type",
    "contradicted", "real", "rare", "hallucinated", "spurious",
}
# attributes that are hidden only on a corpus / gold receiver (RunResult also
# has `families`; Hypothesis and KO also have `evidence`)
CORPUS_RECV = re.compile(r"(^|\.)(corpus|gold|c|cp|self\.c)$")
CORPUS_ONLY_ATTRS = {"families", "cfg", "evidence"}
HIDDEN_NAMES = {"make_gold", "Gold", "Pattern", "_near_miss_map", "_near_miss"}
NEAR_MISS = {"near_miss", "_near_miss", "_near_miss_arr"}
HIDDEN_KWARGS = {"hallucinated", "spurious"}
RECORD_RECV = re.compile(r"(\brecs?\b|_recs\b|\.recs\b|\brec_\w+)")
RECORD_SOURCES = re.compile(r"(\.recs\b|\brecs\[|REC_DTYPE|\brecs\b)")


class _Scanner(ast.NodeVisitor):
    def __init__(self, fname: str):
        self.f = fname
        self.stack: List[str] = []
        self.rec_names: List[set] = [set()]
        self.sites: List[Tuple[str, int, str]] = []    # (qualname, line, token)
        self._skip: set = set()

    # -- scope ------------------------------------------------------------
    def _qual(self) -> str:
        return ".".join(self.stack) or "<module>"

    def _scope(self, node, name):
        self.stack.append(name)
        names = set()
        for sub in ast.walk(node):
            if isinstance(sub, (ast.Assign, ast.AnnAssign)) and sub.value is not None:
                src = ast.unparse(sub.value)
                if RECORD_SOURCES.search(src):
                    tg = sub.targets if isinstance(sub, ast.Assign) else [sub.target]
                    for t in tg:
                        if isinstance(t, ast.Name):
                            names.add(t.id)
        self.rec_names.append(names)
        self.generic_visit(node)
        self.rec_names.pop()
        self.stack.pop()

    def visit_FunctionDef(self, node):
        self._scope(node, node.name)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node):
        self._scope(node, node.name)

    def _add(self, node, token):
        self.sites.append((self._qual(), node.lineno, token))

    # -- plumbing that is not a read --------------------------------------
    def visit_keyword(self, node):
        if node.arg in NEAR_MISS:
            # passing the operator's table on to an operator parameter
            self._skip.add(id(node.value))
        self.generic_visit(node)

    def visit_Assign(self, node):
        if len(node.targets) == 1 and isinstance(node.targets[0], ast.Attribute) \
                and node.targets[0].attr in NEAR_MISS:
            self._skip.add(id(node.value))
        self.generic_visit(node)

    def visit_Call(self, node):
        for kw in node.keywords:
            if kw.arg in HIDDEN_KWARGS:
                self._add(node, f"kw:{kw.arg}")
        self.generic_visit(node)

    # -- reads ------------------------------------------------------------
    def visit_Name(self, node):
        if id(node) in self._skip:
            return
        if node.id in HIDDEN_NAMES or node.id.startswith("KIND_"):
            self._add(node, f"name:{node.id}")
        elif node.id in NEAR_MISS and isinstance(node.ctx, ast.Load):
            self._add(node, f"name:{node.id}")

    def visit_Attribute(self, node):
        if id(node) in self._skip:
            self.generic_visit(node)
            return
        a = node.attr
        if a in HIDDEN_ATTRS:
            self._add(node, f"attr:{a}")
        elif a in CORPUS_ONLY_ATTRS and CORPUS_RECV.search(ast.unparse(node.value)):
            self._add(node, f"attr:{a}")
        elif a in NEAR_MISS and isinstance(node.ctx, ast.Load):
            self._add(node, f"attr:{a}")
        self.generic_visit(node)

    def visit_Subscript(self, node):
        sl = node.slice
        if isinstance(sl, ast.Constant) and isinstance(sl.value, str) \
                and sl.value in HIDDEN_REC_FIELDS:
            recv = node.value
            src = ast.unparse(recv)
            is_rec = bool(RECORD_RECV.search(src)) or (
                isinstance(recv, ast.Name) and recv.id in self.rec_names[-1])
            if is_rec:
                kind = "write" if isinstance(node.ctx, ast.Store) else "field"
                self._add(node, f"{kind}:{sl.value}")
        self.generic_visit(node)


def scan(paths: Optional[List[str]] = None) -> Dict[str, List[int]]:
    """site key 'file::qualname::token' -> line numbers of every access."""
    if paths is None:
        paths = sorted(os.path.join(HERE, f) for f in os.listdir(HERE)
                       if f.endswith(".py") and f != SELF)
        # the live-LLM package is scanned too; its sites are keyed live/<file>
        live = os.path.join(HERE, "live")
        if os.path.isdir(live):
            paths += sorted(os.path.join(live, f) for f in os.listdir(live)
                            if f.endswith(".py"))
    out: Dict[str, List[int]] = defaultdict(list)
    for p in paths:
        with open(p) as fh:
            tree = ast.parse(fh.read(), filename=p)
        s = _Scanner(os.path.relpath(p, HERE) if os.path.dirname(os.path.abspath(p))
                     == os.path.join(HERE, "live") else os.path.basename(p))
        s.visit(tree)
        for q, line, tok in s.sites:
            out[f"{s.f}::{q}::{tok}"].append(line)
    return dict(out)


def load_allowlist() -> Dict[str, Dict[str, object]]:
    with open(ALLOWLIST) as fh:
        return json.load(fh)["sites"]


class TestStaticInventory(unittest.TestCase):
    def test_every_hidden_access_is_allowlisted(self):
        found = scan()
        allow = load_allowlist()
        new = [f"{k} (lines {v})" for k, v in sorted(found.items()) if k not in allow]
        grown = [f"{k}: {len(v)} > {allow[k]['count']} (lines {v})"
                 for k, v in sorted(found.items())
                 if k in allow and len(v) > int(allow[k]["count"])]
        self.assertFalse(new or grown,
                         "unlisted hidden-information access(es); classify each in "
                         "research/mycelic/leakage_allowlist.json and "
                         "docs/mycelic_v5/ORACLE_AUDIT.md:\n  "
                         + "\n  ".join(new + grown))

    def test_allowlist_entries_are_classified(self):
        allow = load_allowlist()
        for k, v in allow.items():
            self.assertIn(v.get("category"), CATEGORIES, k)
            self.assertTrue(str(v.get("why", "")).strip(), f"{k}: no justification")
            if v["category"] == "SHORTCUT":
                self.assertRegex(str(v["why"]), r"\bS\d+\b",
                                 f"{k}: a SHORTCUT must cite its audit finding")

    def test_scanner_sees_a_planted_leak(self):
        """The scanner itself: a decision that reads a hidden field is caught."""
        import tempfile
        src = ("def decide(corpus, hyps):\n"
               "    rec = corpus.recs[[1, 2]]\n"
               "    k = rec['kind']\n"
               "    g = corpus.patterns\n"
               "    return [h for h in hyps if not h.hallucinated], k, g\n")
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as fh:
            fh.write(src)
        try:
            found = scan([fh.name])
        finally:
            os.unlink(fh.name)
        toks = {k.split("::")[-1] for k in found}
        self.assertTrue({"field:kind", "attr:patterns", "attr:hallucinated"} <= toks, toks)


# ---------------------------------------------------------------------------
# 2. Metamorphic tests
# ---------------------------------------------------------------------------

SCALE, SEED = 400, 590          # tune seed; never a sealed final seed


def _world():
    from .runner import build_world
    return build_world(SCALE, SEED)


def _decisions(res) -> Tuple:
    """Everything a run decided, minus the simulator's own flags."""
    reg = tuple((int(h.anchor), tuple(int(p) for p in h.preds), float(h.conf),
                 int(h.chain), int(h.n_indep), int(h.contra),
                 int(h.n_branch_regions), tuple(int(e) for e in h.evidence))
                for h in res.hypotheses)
    md = res.meter.as_dict()
    qs = tuple((int(q.anchor), tuple(int(p) for p in q.target_preds))
               for q in res.questions)
    return reg, round(float(md["cu"]), 6), int(md["calls"]), qs, len(res.kernel_kos)


def _run(world, arch: str, observable: Tuple[str, ...] = (),
         cfg_over: Optional[Dict] = None):
    from . import ops
    from .models import allocation
    from .runner import run_arch
    prev = ops.set_observable(**{k: (k in observable) for k in ops.OBSERVABLE})
    try:
        return run_arch(arch, world, allocation("back-loaded"), SEED,
                        cfg_over=dict(cfg_over) if cfg_over else None)
    finally:
        ops.set_observable(**prev)


class _Poison:
    """Stands in for a hidden object; any use by a decision raises."""

    def __init__(self, name: str):
        object.__setattr__(self, "_name", name)

    def _hit(self, *a, **k):
        raise AssertionError(f"a decision read hidden object {self._name}")

    def __getattr__(self, a):
        if a.startswith("__"):
            raise AttributeError(a)
        self._hit()

    __iter__ = __len__ = __getitem__ = __contains__ = __bool__ = _hit


# the runs each case compares; (label, arch, observable switches, cfg override)
RX = {"local_reextract": True}
CASES = {
    "H_full": ("H_mycelic_full", (), None),
    "H_prev": ("H_mycelic_prev", (), None),
    "A2": ("A2_chunked_ctx", (), None),
    "B4": ("B4_central_triage", (), None),
    "A2_obs": ("A2_chunked_ctx", ("lexical_index",), None),
    "H_full_rx": ("H_mycelic_full", (), RX),
    "H_full_rx_obs": ("H_mycelic_full", ("reextract_lookup",), RX),
    "H_full_obs": ("H_mycelic_full", ("unsupported_by_evidence",), None),
    "H_prev_obs": ("H_mycelic_prev", ("unsupported_by_evidence",), None),
}
_BASE: Dict[str, Tuple] = {}


def _baseline(label: str) -> Tuple:
    if label not in _BASE:
        arch, obs, over = CASES[label]
        _BASE[label] = _decisions(_run(_world(), arch, obs, over))
    return _BASE[label]


def _bijection(x: np.ndarray) -> np.ndarray:
    """Affine bijection of the 64-bit integers (odd multiplier, mod 2**64)."""
    u = np.asarray(x, dtype=np.int64).view(np.uint64)
    with np.errstate(over="ignore"):
        y = u * np.uint64(0x9E3779B97F4A7C15) + np.uint64(0x5DEECE66D)
    return y.view(np.int64)


NEVER_READ = ("kind", "veracity", "group", "facet", "superseded", "salience")
OPERATOR_ONLY = ("pred", "anchor", "t", "polarity", "event", "aux")


def _permute(recs: np.ndarray, fields, seed: int = 12345) -> None:
    rng = np.random.default_rng(seed)
    for f in fields:
        recs[f] = recs[f][rng.permutation(len(recs))]


def _sig_bijection(label: str) -> Tuple:
    """(a) every signature the extraction operator returns is remapped."""
    from . import ops, systems
    orig = ops.extract

    def ext(*a, **k):
        r = orig(*a, **k)
        r.sig = _bijection(r.sig)
        return r

    arch, obs, over = CASES[label]
    with mock.patch.object(ops, "extract", ext), \
            mock.patch.object(systems, "extract", ext):
        return _decisions(_run(_world(), arch, obs, over))


def _never_read(label: str) -> Tuple:
    """(b1) hidden fields no operator reads are permuted; gold objects raise."""
    w = _world()
    c = w.corpus
    _permute(c.recs, NEVER_READ)
    for f in ("patterns", "notable_records", "contradiction_pairs",
              "families", "dup_events", "entity_kind"):
        setattr(c, f, _Poison(f"corpus.{f}"))
    w.gold = _Poison("world.gold")
    arch, obs, over = CASES[label]
    return _decisions(_run(w, arch, obs, over))


def _operator_only(label: str) -> Tuple:
    """(b2) operator-read fields permuted; operators and renderer see truth."""
    from . import ops, systems
    from .corpus import Corpus
    w = _world()
    c = w.corpus
    pristine = c.recs.copy()
    scrambled = c.recs.copy()
    _permute(scrambled, OPERATOR_ONLY)
    c.recs = scrambled

    def truthful(fn):
        def call(corpus, *a, **k):
            saved = corpus.recs
            corpus.recs = pristine
            try:
                return fn(corpus, *a, **k)
            finally:
                corpus.recs = saved
        return call

    ext = truthful(ops.extract)
    arch, obs, over = CASES[label]
    with mock.patch.object(ops, "extract", ext), \
            mock.patch.object(systems, "extract", ext), \
            mock.patch.object(Corpus, "tokens", truthful(Corpus.tokens)), \
            mock.patch.object(Corpus, "text", truthful(Corpus.text)):
        return _decisions(_run(w, arch, obs, over))


def _flag_cleared(label: str) -> Tuple:
    """(c) the hallucination flag is cleared when the synthesis operator returns."""
    from . import ops, systems
    orig = ops.synthesize

    def synth(*a, **k):
        out = orig(*a, **k)
        for h in list(out) + list(k.get("full_out") or []):
            h.hallucinated = False
        return out

    arch, obs, over = CASES[label]
    with mock.patch.object(ops, "synthesize", synth), \
            mock.patch.object(systems, "synthesize", synth):
        return _decisions(_run(_world(), arch, obs, over))


_TRANSFORMS = {"a": _sig_bijection, "b1": _never_read, "b2": _operator_only,
               "c": _flag_cleared}
_RES: Dict[Tuple[str, str], Tuple] = {}


def _transformed(kind: str, label: str) -> Tuple:
    if (kind, label) not in _RES:
        _RES[(kind, label)] = _TRANSFORMS[kind](label)
    return _RES[(kind, label)]


class TestMetamorphic(unittest.TestCase):
    """Each test: decisions after the transform == decisions without it."""
    maxDiff = 400

    def check(self, kind: str, label: str) -> None:
        self.assertEqual(_transformed(kind, label), _baseline(label),
                         f"{label}: decisions changed under transform ({kind})")

    # (a) signature values are identities only
    def test_a_sig_bijection_H_full(self):
        self.check("a", "H_full")

    def test_a_sig_bijection_H_prev(self):
        self.check("a", "H_prev")

    def test_a_sig_bijection_A2(self):
        self.check("a", "A2")

    def test_a_sig_bijection_B4(self):
        self.check("a", "B4")

    # (b1) never-read hidden fields and gold objects
    def test_b1_never_read_H_full(self):
        self.check("b1", "H_full")

    def test_b1_never_read_H_prev(self):
        self.check("b1", "H_prev")

    def test_b1_never_read_A2(self):
        self.check("b1", "A2")

    def test_b1_never_read_B4(self):
        self.check("b1", "B4")

    def test_b1_never_read_H_full_reextract(self):
        self.check("b1", "H_full_rx")

    # (b2) operator-only fields: only an operator or the renderer may read them
    def test_b2_operator_only_H_full(self):
        self.check("b2", "H_full")

    def test_b2_operator_only_H_prev(self):
        self.check("b2", "H_prev")

    def test_b2_operator_only_B4(self):
        self.check("b2", "B4")

    @unittest.expectedFailure
    def test_b2_operator_only_A2_current(self):
        """Audit finding S1 (legacy path, off by default since v5): the A / A2
        lexical index reads the true `pred`."""
        self.check("b2", "A2")

    def test_b2_operator_only_A2_observable(self):
        self.check("b2", "A2_obs")

    @unittest.expectedFailure
    def test_b2_operator_only_H_full_reextract_current(self):
        """Audit finding S2: local re-extraction finds notes by the true
        `anchor` (local_reextract is off in every frozen configuration)."""
        self.check("b2", "H_full_rx")

    def test_b2_operator_only_H_full_reextract_observable(self):
        self.check("b2", "H_full_rx_obs")

    # (c) the simulator's hallucination flag
    @unittest.expectedFailure
    def test_c_hallucination_flag_H_full_current(self):
        """Audit finding S3: question / descent routing skips candidates by
        the simulator's `hallucinated` flag."""
        self.check("c", "H_full")

    @unittest.expectedFailure
    def test_c_hallucination_flag_H_prev_current(self):
        """Audit finding S3, as above."""
        self.check("c", "H_prev")

    def test_c_hallucination_flag_H_full_observable(self):
        self.check("c", "H_full_obs")

    def test_c_hallucination_flag_H_prev_observable(self):
        self.check("c", "H_prev_obs")

    def test_c_hallucination_flag_A2(self):
        self.check("c", "A2")

    def test_c_hallucination_flag_B4(self):
        self.check("c", "B4")

    # the expected failures above fail for the audited reason, not by crashing
    def test_known_shortcuts_are_detected(self):
        for kind, label, fid in (("b2", "A2", "S1"), ("b2", "H_full_rx", "S2"),
                                 ("c", "H_full", "S3"), ("c", "H_prev", "S3")):
            self.assertNotEqual(_transformed(kind, label), _baseline(label),
                                f"{fid} is no longer detected for {label}: if it was "
                                f"fixed, drop the expectedFailure and update the audit")

    def test_defaults_are_observable(self):
        """v5 default: every architecture runs the observable replacements
        (S1-S3); the legacy shortcut paths exist only behind
        MYCELIC_OBSERVABLE=none, to reproduce pre-v5 artifacts."""
        from . import ops
        if os.environ.get("MYCELIC_OBSERVABLE", "").strip() == "none":
            self.skipTest("legacy paths requested explicitly")
        self.assertTrue(all(ops.OBSERVABLE.values()), ops.OBSERVABLE)

    def test_observable_replacements_match_current_defaults(self):
        """S1 and S3 replacements are exact on this generator: identical
        decisions with the switch on and off.  (S2 is not exact: a token match
        also finds notes that name the entity as an aux mention.)"""
        self.assertEqual(_baseline("A2_obs"), _baseline("A2"))
        self.assertEqual(_baseline("H_full_obs"), _baseline("H_full"))
        self.assertEqual(_baseline("H_prev_obs"), _baseline("H_prev"))


# ---------------------------------------------------------------------------
# 3. Sealed final seeds
# ---------------------------------------------------------------------------

class TestSealedSeeds(unittest.TestCase):
    def _env(self):
        env = dict(os.environ)
        env.pop("MYCELIC_FINAL_EVAL", None)
        return mock.patch.dict(os.environ, env, clear=True)

    def test_check_world_blocks_every_final_world(self):
        from . import protocol
        log = protocol._LOG
        size = os.path.getsize(log) if os.path.exists(log) else -1
        with self._env():
            for scale, seeds in protocol.FINAL_SEEDS.items():
                for seed in sorted(seeds):
                    with self.assertRaises(PermissionError, msg=f"{scale}/{seed}"):
                        protocol.check_world(scale, seed)
            with mock.patch.dict(os.environ, {"MYCELIC_FINAL_EVAL": "0"}):
                with self.assertRaises(PermissionError):
                    protocol.check_world(10_000, 3000)
        self.assertEqual(os.path.getsize(log) if os.path.exists(log) else -1, size,
                         "a refused build wrote to the final-access log")

    def test_final_set_is_what_the_protocol_declares(self):
        from . import protocol
        self.assertEqual(protocol.FINAL_SEEDS[10_000], frozenset(range(3000, 3030)))
        self.assertEqual(protocol.FINAL_SEEDS[50_000], frozenset(range(3000, 3003)))
        for s in protocol.TUNE_SEEDS + protocol.DEVVAL_SEEDS_10K + protocol.TRANSFER_SEEDS:
            self.assertFalse(protocol.is_final(10_000, s), s)

    def test_check_world_allows_development_worlds(self):
        from . import protocol
        with self._env():
            for scale, seed in ((10_000, 590), (10_000, 2999), (10_000, 3030),
                                (50_000, 3030), (50_000, 509)):
                protocol.check_world(scale, seed)

    def test_check_world_blocks_alias_scales(self):
        """Audit finding P1: build_org uses the target only through rounded
        region sizes, so target 9,999 builds the same org - and so the same
        corpus - as 10,000 (and 50,001 as 50,000), and check_world does not
        refuse those aliases of a sealed world."""
        from . import protocol
        with self._env():
            for scale, seed in ((9_999, 3000), (50_001, 3000)):
                with self.assertRaises(PermissionError, msg=f"{scale}/{seed}"):
                    protocol.check_world(scale, seed)

    def test_build_world_refuses_before_generating(self):
        from . import runner

        def boom(*a, **k):
            raise AssertionError("a sealed world reached the generator")

        with self._env(), mock.patch.object(runner, "build_org", boom), \
                mock.patch.object(runner, "build_corpus", boom):
            for scale, seed in ((10_000, 3000), (10_000, 3029), (50_000, 3002)):
                with self.assertRaises(PermissionError):
                    runner.build_world(scale, seed)


# ---------------------------------------------------------------------------

def _print_sites() -> None:
    allow = load_allowlist()
    found = scan()
    print("| site | lines | category | justification |")
    print("|---|---|---|---|")
    for k in sorted(found, key=lambda s: (allow.get(s, {}).get("category", "~"), s)):
        a = allow.get(k, {})
        f, q, t = k.split("::")
        print(f"| `{f}:{','.join(map(str, found[k]))}` {q} `{t}` | {len(found[k])} | "
              f"{a.get('category', 'UNLISTED')} | {a.get('why', '')} |")
    stale = sorted(set(allow) - set(found))
    if stale:
        print("\nallowlist entries no longer present:", *stale, sep="\n  ")


if __name__ == "__main__":
    if "--sites" in sys.argv:
        _print_sites()
    else:
        unittest.main(verbosity=2)
