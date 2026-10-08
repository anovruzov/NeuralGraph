"""Tests for the live-agent package (MockBackend only; no model, < 1 min).

    python3 -m unittest research.mycelic.live.test_live -v

* privacy: an agent call carries exactly that agent's own notes; nothing a
  deployment could not observe reaches the backend (hidden fields scrambled
  after rendering leave every prompt byte-identical; gold objects poisoned);
* parsing and code-side canonicalisation (note index, predicate and entity
  catalogs, day from the header, text echo signature);
* integration with the unchanged pipeline (user_extract / chunked_long_context
  with a pre-computed extraction);
* resumability (a crash mid-run resumes from the replay cache) and replay
  determinism (a replay-only rerun through the real HTTP client returns the
  recorded results without contacting a server; a mock rerun of the CLI gives
  identical rows).
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

import numpy as np

from ..corpus import CAUSAL_CHAINS, N_CAUSAL_PRED, PRED_SURFACE, PREDICATES
from ..models import ANCHORS
from ..ops import extract
from ..runner import build_world
from ..systems import chunked_long_context, user_extract
from . import agents as A
from .backend import (CallResult, Job, LlamaServerBackend, MockBackend, ReplayCache,
                      cache_key)
from .notes import NoteStore, echo_signature, split_note
from .run import a2_candidates, mock_lexicon

SCALE, SEED = 400, 701          # LIVE seed (development data)


class _Poison:
    def __init__(self, name):
        object.__setattr__(self, "_n", name)

    def _hit(self, *a, **k):
        raise AssertionError(f"live code touched hidden object {self._n}")

    def __getattr__(self, a):
        if a.startswith("__"):
            raise AttributeError(a)
        self._hit()

    __iter__ = __len__ = __getitem__ = __contains__ = __bool__ = _hit


def _prompts(store, agents):
    ag = A.EdgeAgent(MockBackend(mock_lexicon(), store.catalog))
    return [json.dumps(ag.job(store.notes(a)).messages) for a in agents]


class TestPrivacy(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.w = build_world(SCALE, SEED)
        cls.store = NoteStore(cls.w.corpus)
        cls.ids = cls.store.agents()[:6] + cls.store.agents()[-3:]

    def test_agent_receives_only_its_own_notes(self):
        mock = MockBackend(mock_lexicon(), self.store.catalog)
        ag = A.EdgeAgent(mock)
        for a in self.ids:
            ag.extract(self.store.notes(a), a)
        self.assertEqual(len(mock.seen), len(self.ids))
        others = set(self.store.agents())
        for a, msgs in zip(self.ids, mock.seen):
            self.assertEqual([m["role"] for m in msgs], ["system", "user"])
            self.assertEqual(msgs[0]["content"], A.SYSTEM_PROMPT)   # constant, no data
            own = [f"{i}: {split_note(t)[2]}" for i, t in self.store.notes(a)]
            self.assertEqual(msgs[1]["content"], "Notes:\n" + "\n".join(own) + "\nOutput:")
            blob = json.dumps(msgs)
            for o in others:                    # no author id, not even its own
                self.assertNotIn(o, blob)
            # no record id, user node id or other agent's note text
            other_bodies = {split_note(t)[2] for b in self.ids if b != a
                            for _, t in self.store.notes(b)}
            mine = {split_note(t)[2] for _, t in self.store.notes(a)}
            for body in other_bodies - mine:
                self.assertNotIn(body, msgs[1]["content"].split("\n"))

    def test_hidden_fields_scrambled_after_rendering_leave_prompts_identical(self):
        w = build_world(SCALE, SEED)
        store = NoteStore(w.corpus)
        before = _prompts(store, self.ids)
        rng = np.random.default_rng(3)
        recs = w.corpus.recs
        for f in ("kind", "veracity", "group", "facet", "superseded", "salience",
                  "event", "pred", "anchor", "t", "polarity", "aux"):
            recs[f] = rng.permutation(recs[f])
        w.corpus.patterns = _Poison("patterns")
        w.gold = _Poison("gold")
        after = _prompts(store, self.ids)
        self.assertEqual(before, after)
        # canonicalisation reads only the store as well
        mock = MockBackend(mock_lexicon(), store.catalog)
        reps = A.EdgeAgent(mock).extract_many([(a, store.notes(a)) for a in self.ids])
        cl = A.edge_claims(store, reps)
        self.assertGreater(cl.diag["kept"], 0)

    def test_rendering_does_not_read_never_rendered_fields(self):
        w = build_world(SCALE, SEED)
        rng = np.random.default_rng(4)
        for f in ("kind", "veracity", "group", "facet", "superseded", "salience"):
            w.corpus.recs[f] = rng.permutation(w.corpus.recs[f])
        w.corpus.patterns = _Poison("patterns")
        store2 = NoteStore(w.corpus)
        self.assertEqual(_prompts(self.store, self.ids), _prompts(store2, self.ids))

    def test_store_holds_no_reference_to_the_corpus(self):
        for v in vars(self.store).values():
            self.assertNotIsInstance(v, type(self.w.corpus))
            if isinstance(v, np.ndarray):
                self.assertIsNone(v.dtype.names)

    def test_prompt_has_no_generator_surface_phrase(self):
        toks = A.SYSTEM_PROMPT
        for ch in ",.:\"()[]":
            toks = toks.replace(ch, " ")
        toks = toks.split()
        for p in PREDICATES[:N_CAUSAL_PRED]:
            for f in PRED_SURFACE[p]:
                ft = f.split()
                hit = any(toks[i:i + len(ft)] == ft for i in range(len(toks) - len(ft) + 1))
                self.assertFalse(hit, f"system prompt contains surface phrase {f!r}")


class TestParsing(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.w = build_world(SCALE, SEED)
        cls.store = NoteStore(cls.w.corpus)

    def test_parse_lines(self):
        txt = ("0 yield_drop nimbus-pay ok\n1  sla_breach  orion-ii   not \n"
               "garbage here\n\n2 meeting_note logged kestrel ok\n3 x\n"
               "4 doc_review nimbus\n")
        lines, bad = A.parse_lines(txt)
        self.assertEqual(lines, [(0, "yield_drop", ("nimbus-pay",), False),
                                 (1, "sla_breach", ("orion-ii",), True),
                                 (2, "meeting_note", ("logged", "kestrel"), False),
                                 (4, "doc_review", ("nimbus",), False)])
        self.assertEqual(bad, 2)

    def test_canonicalise(self):
        a = self.store.agents()[5]
        notes = self.store.notes(a)
        ent = self.store.catalog
        lines = [(0, "yield_drop", (ent[3], ent[9]), False),
                 (0, "sla_breach", (ent[4],), False),          # duplicate index
                 (1, "not_a_predicate", (ent[3],), False),     # unknown predicate
                 (2, "sla_breach", (ent[3] + "zz",), False),   # not in the catalog
                 (len(notes) + 5, "sla_breach", (ent[3],), False),  # out of range
                 (3, "close_delay", ("logged", ent[7]), True)]  # first catalog name
        call = CallResult("", 0, 0, 0, 0.0, 0.0, 0.0, "stop", "k")
        cl = A.edge_claims(self.store, [A.AgentReply(a, lines, 1, call, len(notes))])
        d = cl.diag
        self.assertEqual((d["kept"], d["dup_index"], d["bad_pred"], d["bad_entity"],
                          d["bad_index"], d["malformed"]), (2, 1, 1, 1, 1, 1))
        ex = cl.to_extract_result()
        self.assertEqual(ex.rid.tolist(), [self.store.rid_of(a, 0), self.store.rid_of(a, 3)])
        self.assertEqual(ex.pred.tolist(), [PREDICATES.index("yield_drop"),
                                            PREDICATES.index("close_delay")])
        self.assertEqual(ex.anchor.tolist(), [3, 7])
        self.assertEqual(ex.polarity.tolist(), [1, -1])
        self.assertEqual(ex.t.tolist(), [split_note(notes[0][1])[0], split_note(notes[3][1])[0]])
        self.assertEqual(set(ex.uid.tolist()), {self.store.uid_of(a)})
        self.assertFalse(ex.spurious.any())
        self.assertEqual(int(ex.sig[0]), echo_signature(notes[0][1], set(ent)))

    def test_echo_signature_recipe(self):
        cat = {"nimbus", "orion-ii", "kestrel"}
        base = "[d010] U1 :: line yield fell nimbus orion-ii per the runbook during"
        echo = "[d013] U9 :: line yield fell nimbus kestrel per the runbook week"
        other = "[d010] U1 :: line yield fell nimbus per the window during"
        neg = "[d010] U1 :: line yield fell nimbus orion-ii per the runbook during not"
        self.assertEqual(echo_signature(base, cat), echo_signature(echo, cat))
        self.assertNotEqual(echo_signature(base, cat), echo_signature(other, cat))
        self.assertNotEqual(echo_signature(base, cat), echo_signature(neg, cat))
        self.assertGreaterEqual(echo_signature(base, cat), 0)

    def test_grammar(self):
        g = A.grammar(7)
        for p in PREDICATES:
            self.assertIn(f'"{p}"', g)
        self.assertIn('"6"', g)
        self.assertNotIn('"7"', g)
        self.assertIn("{0,7}", g)
        self.assertIn('pol ::= "ok" | "not"', g)

    def test_mock_is_labelled_and_not_an_llm(self):
        m = MockBackend(mock_lexicon(), self.store.catalog)
        self.assertFalse(m.is_llm)
        self.assertIn("NOT an LLM", m.label)


class TestIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.w = build_world(SCALE, SEED)

    def test_user_extract_with_precomputed_claims_is_unchanged(self):
        c = self.w.corpus
        tier = ANCHORS["small-7b"]
        ex = extract(c, np.arange(len(c.recs)), tier, np.random.default_rng(5),
                     near_miss=self.w.near_miss)
        ua = user_extract(c, tier, np.random.default_rng(5), near_miss=self.w.near_miss)
        ub = user_extract(c, tier, np.random.default_rng(99), ex=ex)
        for f in ("rid", "uid", "pred", "anchor", "t", "polarity", "sig"):
            np.testing.assert_array_equal(getattr(ua.ex, f), getattr(ub.ex, f))
        np.testing.assert_allclose(ua.imp, ub.imp)
        np.testing.assert_allclose(ua.nov, ub.nov)

    def test_a2_with_precomputed_claims_uses_exactly_them(self):
        from ..models import allocation
        c = self.w.corpus
        cand = a2_candidates(self.w)
        sel = cand[::3]
        pre = extract(c, sel, ANCHORS["frontier-plus"], np.random.default_rng(1),
                      near_miss=self.w.near_miss)
        pre = pre.take(np.nonzero(~pre.spurious)[0])
        res = chunked_long_context(c, allocation("back-loaded"), SEED,
                                   near_miss=self.w.near_miss, pre_ex=pre)
        self.assertEqual({(k.pred, k.anchor) for k in res.kernel_kos},
                         {(int(p), int(a)) for p, a in zip(pre.pred, pre.anchor)})


class _CrashAfter(MockBackend):
    def __init__(self, k, *a, **kw):
        super().__init__(*a, **kw)
        self.k = k

    def _call(self, job, params):
        if self.n_live_calls >= self.k:
            raise RuntimeError("simulated crash")
        return super()._call(job, params)


class TestResumeReplay(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.w = build_world(SCALE, SEED)
        cls.store = NoteStore(cls.w.corpus)
        cls.items = [(a, cls.store.notes(a)) for a in cls.store.agents()[:12]]
        cls.tmp = tempfile.mkdtemp()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _claims(self, reps):
        ex = A.edge_claims(self.store, reps).to_extract_result()
        return [getattr(ex, f).tolist() for f in ("rid", "pred", "anchor", "polarity", "sig")]

    def test_crash_resumes_from_the_cache(self):
        path = os.path.join(self.tmp, "resume.jsonl")
        crash = _CrashAfter(5, mock_lexicon(), self.store.catalog, cache=ReplayCache(path))
        with self.assertRaises(RuntimeError):
            A.EdgeAgent(crash).extract_many(self.items)
        self.assertEqual(crash.n_live_calls, 5)
        again = MockBackend(mock_lexicon(), self.store.catalog, cache=ReplayCache(path))
        reps = A.EdgeAgent(again).extract_many(self.items)
        self.assertEqual((again.n_replayed, again.n_live_calls), (5, 7))
        fresh = MockBackend(mock_lexicon(), self.store.catalog)
        self.assertEqual(self._claims(reps),
                         self._claims(A.EdgeAgent(fresh).extract_many(self.items)))
        # a truncated last line (crash mid-write) is tolerated
        with open(path, "a") as fh:
            fh.write('{"key": "abc", "resu')
        self.assertEqual(len(ReplayCache(path)), 12)

    def test_http_client_record_then_replay_only(self):
        seen = []

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                seen.append(body)
                user = body["messages"][-1]["content"]
                n = sum(1 for ln in user.splitlines() if ln[:1].isdigit())
                text = "\n".join(f"{i} status_update nimbus" for i in range(n))
                out = json.dumps({"choices": [{"message": {"content": text},
                                               "finish_reason": "stop"}],
                                  "usage": {"prompt_tokens": 100, "completion_tokens": 7 * n},
                                  "timings": {"cache_n": 60, "prompt_ms": 5.0,
                                              "predicted_ms": 9.0}}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

            def log_message(self, *a):
                pass

        srv = HTTPServer(("127.0.0.1", 0), H)
        th = threading.Thread(target=srv.serve_forever, daemon=True)
        th.start()
        url = f"http://127.0.0.1:{srv.server_port}"
        path = os.path.join(self.tmp, "http.jsonl")
        try:
            be = LlamaServerBackend(url, "fp-test", cache=ReplayCache(path), concurrency=3)
            r1 = A.EdgeAgent(be).extract_many(self.items[:6])
        finally:
            srv.shutdown()
            srv.server_close()
        self.assertEqual(len(seen), 6)
        b0 = seen[0]
        self.assertEqual(b0["chat_template_kwargs"], {"enable_thinking": False})
        self.assertIn("grammar", b0)
        self.assertEqual(b0["temperature"], 0.0)
        self.assertEqual(r1[0].call.cached_tokens, 60)
        # server gone: a replay-only client reproduces every result from the cache
        rb = LlamaServerBackend(url, "fp-test", cache=ReplayCache(path), replay_only=True)
        r2 = A.EdgeAgent(rb).extract_many(self.items[:6])
        self.assertEqual([r.call.text for r in r1], [r.call.text for r in r2])
        self.assertTrue(all(r.call.replayed for r in r2))
        self.assertEqual(rb.n_live_calls, 0)
        # a different model fingerprint is a different key
        job = A.EdgeAgent(rb).job(self.items[0][1])
        self.assertNotEqual(cache_key("fp-test", rb.params(job), job.messages),
                            cache_key("fp-other", rb.params(job), job.messages))
        with self.assertRaises(KeyError):
            LlamaServerBackend(url, "fp-other", cache=ReplayCache(path),
                               replay_only=True).chat(job)

    def test_cli_mock_run_is_deterministic(self):
        from .run import main
        rows = []
        for k in range(2):
            out = os.path.join(self.tmp, f"cli{k}.jsonl")
            main(["--mock", "--scale", str(SCALE), "--seed", str(SEED), "--out", out,
                  "--systems", "H_mycelic_prev,B4_central_triage,A2_live",
                  "--log-every", "1000"])
            with open(out) as fh:
                rows.append([json.loads(l) for l in fh])
        strip = lambda rs: [{k: v for k, v in r.items()                      # noqa: E731
                             if k not in ("tag", "started", "finished", "pipeline_s", "phase_wall_s", "llm_phase_wall_s",
                                          "runtime_s", "est_wall_s_at_concurrency",
                                          "latency_s", "per_call_latency_s")}
                            for r in rs]
        self.assertEqual(strip(rows[0]), strip(rows[1]))
        kinds = [r["kind"] for r in rows[0]]
        self.assertEqual(kinds.count("system"), 6)
        self.assertTrue(rows[0][0]["mock"])
        with open(os.path.join(self.tmp, "cli0.md")) as fh:
            self.assertIn("NOT LLM results", fh.read())


class TestStagesAndPackaging(unittest.TestCase):
    def test_smoke_stage_preset_env_rows_and_package(self):
        import tarfile
        from . import envinfo
        from .package_results import main as package
        from .run import main
        tmp = tempfile.mkdtemp()
        try:
            rows = main(["--mock", "--stage", "smoke", "--out-dir", tmp, "--log-every", "1000"])
            meta = rows[0]
            self.assertEqual((meta["stage"], meta["scale"], meta["seed"], meta["agents_run"]),
                             ("smoke", 400, 701, 40))
            self.assertFalse([r for r in rows if r["kind"] == "system"])
            out = os.path.join(tmp, "stage_smoke_s701_mock.jsonl")
            with open(out) as fh:
                written = [json.loads(l) for l in fh]
            for r in written:                       # every row says where it came from
                env = r["run_env"]
                self.assertIn("cpu", env["machine"])
                self.assertEqual(env["edge"]["backend_kind"], "mock")
                self.assertEqual(env["prompt_version"], A.PROMPT_VERSION)
            res = package(["--out-dir", tmp, "--dest", os.path.join(tmp, "pk")])
            with tarfile.open(res["results"]) as tf:
                names = tf.getnames()
            self.assertIn("results/MANIFEST.json", names)
            self.assertIn("results/stage_smoke_s701_mock.jsonl", names)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        self.assertEqual(envinfo.url_kind("http://127.0.0.1:8081"), "loopback")
        self.assertEqual(envinfo.url_kind("http://192.168.1.5:8081"), "private-network")
        self.assertEqual(envinfo.url_kind(None), "none")

    def test_preflight_refuses_a_non_llama_server_endpoint(self):
        from . import envinfo

        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(404)
                self.end_headers()

            def log_message(self, *a):
                pass

        srv = HTTPServer(("127.0.0.1", 0), H)
        th = threading.Thread(target=srv.serve_forever, daemon=True)
        th.start()
        try:
            with self.assertRaises(SystemExit):
                envinfo.preflight(f"http://127.0.0.1:{srv.server_port}", "edge")
        finally:
            srv.shutdown()
            srv.server_close()

    def test_projection_reports_later_stages(self):
        from .run import make_projection
        rows = []
        proj = make_projection("edge agents", "smoke", 701, lambda st: 100.0, "agent", rows)
        proj(2.0, 50, 60)
        self.assertEqual(rows[0]["kind"], "projection")
        for st in ("400", "2000", "10000"):
            self.assertIn(f"stage {st}", rows[0]["text"])


if __name__ == "__main__":
    unittest.main()
