"""Chat backends for live Mycelic agents.

``LlamaServerBackend`` talks to llama.cpp's ``llama-server`` through its
OpenAI-compatible ``/v1/chat/completions`` endpoint (stdlib only: urllib and a
thread pool; aiohttp is not a dependency).  It provides

* bounded concurrency (one in-flight request per server slot, ``-np``),
* timeouts and retries with exponential backoff,
* grammar- (GBNF) or JSON-schema-constrained output,
* Qwen3 thinking disabled through the chat template
  (``chat_template_kwargs = {"enable_thinking": false}``; needs ``--jinja``),
* token and latency metering from each response (``usage`` and llama.cpp's
  ``timings``: prompt tokens, prompt tokens served from the slot's KV cache,
  completion tokens, server-side prefill / decode milliseconds),
* a record/replay cache keyed by sha256(model fingerprint + request params +
  messages), stored as append-only JSONL.  A call whose key is in the cache is
  never sent again, which makes every run resumable after a crash and every
  rerun bit-for-bit reproducible.

``MockBackend`` is a deterministic, LLM-free stand-in used by the tests and by
``run.py --mock``.  Its output is a lexical lookup with injected format errors;
it is labelled as a mock everywhere it is reported and is never an LLM result.
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import re
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple


Messages = List[Dict[str, str]]


@dataclass
class CallResult:
    text: str
    prompt_tokens: int
    completion_tokens: int
    cached_tokens: int          # prompt tokens reused from the slot's KV cache
    latency_s: float            # client-side wall time of the original call
    prompt_ms: float            # server-side prefill time (llama.cpp timings)
    decode_ms: float            # server-side decode time
    finish_reason: str
    key: str
    replayed: bool = False
    backend: str = ""
    attempts: int = 1
    # where the call was MADE (machine, server build, slots, URL kind), stored
    # in the cache with the result, so a replay elsewhere still says where the
    # answer came from
    recorded: Optional[Dict[str, object]] = None


# ---------------------------------------------------------------------------
# Record / replay cache
# ---------------------------------------------------------------------------

def cache_key(model_id: str, params: Dict[str, object], messages: Messages) -> str:
    blob = json.dumps({"model": model_id, "params": params, "messages": messages},
                      sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


class ReplayCache:
    """Append-only JSONL: one line per completed call, {"key", "result"}.

    Loaded fully at start; each new result is appended and flushed at once,
    so a crash loses at most the calls in flight.  A truncated last line (a
    crash mid-write) is skipped on load."""

    def __init__(self, path: str):
        self.path = path
        self._d: Dict[str, Dict[str, object]] = {}
        self._lock = threading.Lock()
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        if os.path.exists(path):
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    self._d[row["key"]] = row["result"]

    def __len__(self) -> int:
        return len(self._d)

    def __contains__(self, key: str) -> bool:
        return key in self._d

    def get(self, key: str) -> Optional[Dict[str, object]]:
        return self._d.get(key)

    def put(self, key: str, result: Dict[str, object]) -> None:
        with self._lock:
            if key in self._d:
                return
            self._d[key] = result
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps({"key": key, "result": result},
                                    ensure_ascii=False) + "\n")
                fh.flush()


def model_fingerprint(path: str) -> str:
    """sha256 of a local GGUF file, cached in a ``<path>.sha256`` sidecar."""
    side = path + ".sha256"
    st = os.stat(path)
    if os.path.exists(side):
        with open(side) as fh:
            parts = fh.read().split()
        if len(parts) == 2 and parts[1] == str(st.st_size):
            return parts[0]
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            b = fh.read(1 << 22)
            if not b:
                break
            h.update(b)
    fp = h.hexdigest()
    try:
        with open(side, "w") as fh:
            fh.write(f"{fp} {st.st_size}\n")
    except OSError:
        pass
    return fp


# ---------------------------------------------------------------------------
# Base class: caching + bounded-concurrency map
# ---------------------------------------------------------------------------

@dataclass
class Job:
    messages: Messages
    max_tokens: int
    grammar: Optional[str] = None
    json_schema: Optional[Dict[str, object]] = None
    tag: str = ""


class _Backend:
    label = "abstract"
    is_llm = False

    def __init__(self, model_id: str, cache: Optional[ReplayCache] = None,
                 concurrency: int = 1, replay_only: bool = False,
                 temperature: float = 0.0, seed: int = 0):
        self.model_id = model_id
        self.cache = cache
        self.concurrency = max(1, int(concurrency))
        self.replay_only = replay_only
        self.temperature = float(temperature)
        self.seed = int(seed)
        self.n_live_calls = 0
        self.n_replayed = 0
        self._lock = threading.Lock()
        self.record_info: Optional[Dict[str, object]] = None   # set by the caller

    # request parameters that change the output (all go into the cache key)
    def params(self, job: Job) -> Dict[str, object]:
        p: Dict[str, object] = {"max_tokens": int(job.max_tokens),
                                "temperature": self.temperature,
                                "seed": self.seed}
        if job.grammar is not None:
            p["grammar"] = job.grammar
        if job.json_schema is not None:
            p["json_schema"] = job.json_schema
        return p

    def _call(self, job: Job, params: Dict[str, object]) -> Dict[str, object]:
        raise NotImplementedError

    def chat(self, job: Job) -> CallResult:
        params = self.params(job)
        key = cache_key(self.model_id, params, job.messages)
        if self.cache is not None:
            hit = self.cache.get(key)
            if hit is not None:
                with self._lock:
                    self.n_replayed += 1
                r = CallResult(**{k: v for k, v in hit.items()
                                  if k in CallResult.__dataclass_fields__})
                r.replayed = True
                r.key = key
                return r
        if self.replay_only:
            raise KeyError(f"replay-only backend has no cached result for {key[:12]}")
        res = self._call(job, params)
        res["key"] = key
        res["backend"] = self.label
        res["recorded"] = self.record_info
        if self.cache is not None:
            self.cache.put(key, res)
        with self._lock:
            self.n_live_calls += 1
        return CallResult(**res)

    def map(self, jobs: Sequence[Job],
            progress: Optional[Callable[[int, int, CallResult], None]] = None
            ) -> List[CallResult]:
        """Run every job with at most ``concurrency`` requests in flight;
        results in job order.  Cached jobs return immediately."""
        out: List[Optional[CallResult]] = [None] * len(jobs)
        done = 0
        if self.concurrency == 1:
            for i, j in enumerate(jobs):
                out[i] = self.chat(j)
                done += 1
                if progress:
                    progress(done, len(jobs), out[i])
            return out  # type: ignore[return-value]
        with ThreadPoolExecutor(max_workers=self.concurrency) as pool:
            futs = {pool.submit(self.chat, j): i for i, j in enumerate(jobs)}
            for f in as_completed(futs):
                i = futs[f]
                out[i] = f.result()
                done += 1
                if progress:
                    progress(done, len(jobs), out[i])
        return out  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# llama-server (OpenAI-compatible)
# ---------------------------------------------------------------------------

class LlamaServerBackend(_Backend):
    """llama.cpp ``llama-server`` over HTTP.

    ``thinking=False`` disables Qwen3's reasoning mode through the chat
    template (``chat_template_kwargs``); ``no_think_tag`` additionally appends
    Qwen3's ``/no_think`` soft switch to the last user turn (use only if the
    server was started without ``--jinja``)."""

    is_llm = True

    def __init__(self, url: str, model_id: str, cache: Optional[ReplayCache] = None,
                 concurrency: int = 4, timeout: float = 3600.0, retries: int = 4,
                 thinking: bool = False, no_think_tag: bool = False,
                 replay_only: bool = False, temperature: float = 0.0,
                 seed: int = 0, label: str = ""):
        super().__init__(model_id, cache, concurrency, replay_only, temperature, seed)
        self.url = url.rstrip("/")
        self.timeout = float(timeout)
        self.retries = int(retries)
        self.thinking = bool(thinking)
        self.no_think_tag = bool(no_think_tag)
        self.label = label or f"llama-server:{model_id[:12]}"

    def params(self, job: Job) -> Dict[str, object]:
        p = super().params(job)
        p["enable_thinking"] = self.thinking
        p["no_think_tag"] = self.no_think_tag
        return p

    @staticmethod
    def server_props(url: str, timeout: float = 10.0) -> Dict[str, object]:
        with urllib.request.urlopen(url.rstrip("/") + "/props", timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))

    @staticmethod
    def wait_ready(url: str, timeout: float = 600.0) -> bool:
        t0 = time.time()
        while time.time() - t0 < timeout:
            try:
                with urllib.request.urlopen(url.rstrip("/") + "/health", timeout=5) as r:
                    if r.status == 200:
                        return True
            except (urllib.error.URLError, OSError):
                pass
            time.sleep(2.0)
        return False

    def _payload(self, job: Job, params: Dict[str, object]) -> Dict[str, object]:
        msgs = [dict(m) for m in job.messages]
        if self.no_think_tag and msgs and msgs[-1]["role"] == "user":
            msgs[-1]["content"] = msgs[-1]["content"] + " /no_think"
        body: Dict[str, object] = {
            "messages": msgs,
            "max_tokens": int(job.max_tokens),
            "temperature": self.temperature,
            "seed": self.seed,
            "stream": False,
            "cache_prompt": True,
            "chat_template_kwargs": {"enable_thinking": self.thinking},
        }
        if self.temperature == 0.0:
            body["top_k"] = 1
        if job.grammar is not None:
            body["grammar"] = job.grammar
        if job.json_schema is not None:
            body["response_format"] = {"type": "json_schema",
                                       "json_schema": {"name": "out", "strict": True,
                                                       "schema": job.json_schema}}
        return body

    def _call(self, job: Job, params: Dict[str, object]) -> Dict[str, object]:
        data = json.dumps(self._payload(job, params)).encode("utf-8")
        last: Optional[BaseException] = None
        for attempt in range(1, self.retries + 2):
            req = urllib.request.Request(self.url + "/v1/chat/completions", data=data,
                                         headers={"Content-Type": "application/json"})
            t0 = time.time()
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    resp = json.loads(r.read().decode("utf-8"))
                lat = time.time() - t0
                ch = resp["choices"][0]
                msg = ch.get("message", {})
                text = msg.get("content") or ""
                u = resp.get("usage", {}) or {}
                tm = resp.get("timings", {}) or {}
                return {"text": text,
                        "prompt_tokens": int(u.get("prompt_tokens", 0)),
                        "completion_tokens": int(u.get("completion_tokens", 0)),
                        "cached_tokens": int(tm.get("cache_n", 0)),
                        "latency_s": round(lat, 4),
                        "prompt_ms": float(tm.get("prompt_ms", 0.0)),
                        "decode_ms": float(tm.get("predicted_ms", 0.0)),
                        "finish_reason": str(ch.get("finish_reason", "")),
                        "attempts": attempt}
            except (urllib.error.URLError, OSError, KeyError, ValueError,
                    json.JSONDecodeError) as e:   # noqa: PERF203
                last = e
                if isinstance(e, urllib.error.HTTPError) and e.code in (400, 404):
                    body = e.read().decode("utf-8", "replace")[:500]
                    raise RuntimeError(f"llama-server rejected the request: {e.code} {body}")
                time.sleep(min(60.0, 2.0 * 2 ** (attempt - 1)))
        raise RuntimeError(f"llama-server call failed after {self.retries + 1} attempts: {last}")


# ---------------------------------------------------------------------------
# Mock (tests only; NOT an LLM)
# ---------------------------------------------------------------------------

MOCK_LABEL = "MOCK (deterministic lexical stub, NOT an LLM)"


class MockBackend(_Backend):
    """Deterministic, LLM-free stand-in.

    It reads ONLY the request messages.  For each numbered note line
    ``<i>: <text>`` of the last user turn it looks up a predicate phrase in a
    supplied lexicon, takes the first catalog name as the entity and copies a
    trailing ``not``, then injects deterministic faults keyed on the note text
    (unknown predicate names, misspelt entities, duplicate and malformed
    lines) so that the parser's error paths are exercised.  It is a test
    double: its numbers are labelled MOCK and are never reported as an LLM
    result."""

    label = MOCK_LABEL
    is_llm = False

    def __init__(self, lexicon: Dict[Tuple[str, ...], str], catalog: Sequence[str],
                 cache: Optional[ReplayCache] = None, fault_rate: float = 0.08,
                 concurrency: int = 1, sleep_s: float = 0.0):
        super().__init__("mock-v1", cache, concurrency)
        self.lexicon = dict(lexicon)
        self.lens = sorted({len(k) for k in self.lexicon}, reverse=True)
        self.catalog = set(catalog)
        self.fault_rate = float(fault_rate)
        self.sleep_s = float(sleep_s)
        self.seen: List[Messages] = []          # every request, for privacy tests

    def params(self, job: Job) -> Dict[str, object]:
        p = super().params(job)
        p["fault_rate"] = self.fault_rate
        return p

    def _line(self, idx: int, body: str) -> List[str]:
        toks = body.split()
        neg = bool(toks) and toks[-1] == "not"
        pred = None
        for i in range(len(toks)):
            for L in self.lens:
                p = self.lexicon.get(tuple(toks[i:i + L]))
                if p is not None:
                    pred = p
                    break
            if pred:
                break
        ent = next((t for t in toks if t in self.catalog), None)
        if pred is None or ent is None:
            return []
        h = int(hashlib.sha256(body.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
        pol = "not" if neg else "ok"
        line = f"{idx} {pred} {ent} {pol}"
        if h < self.fault_rate * 0.25:
            line = f"{idx} not_a_predicate {ent} {pol}"
        elif h < self.fault_rate * 0.5:
            line = f"{idx} {pred} {ent[:-1] or 'x'}q {pol}"
        elif h < self.fault_rate * 0.75:
            return [line, line]
        elif h < self.fault_rate:
            return [line, "garbage line without structure"]
        return [line]

    def _call(self, job: Job, params: Dict[str, object]) -> Dict[str, object]:
        self.seen.append([dict(m) for m in job.messages])
        if self.sleep_s:
            time.sleep(self.sleep_s)
        user = job.messages[-1]["content"]
        out: List[str] = []
        for ln in user.splitlines():
            m = re.match(r"^(\d+): (.*)$", ln)
            if m:
                out.extend(self._line(int(m.group(1)), m.group(2)))
        text = "\n".join(out)
        ptok = sum(len(m["content"].split()) for m in job.messages)
        return {"text": text, "prompt_tokens": int(ptok * 1.3),
                "completion_tokens": int(len(text.split()) * 2.5),
                "cached_tokens": 0, "latency_s": 0.0, "prompt_ms": 0.0,
                "decode_ms": 0.0, "finish_reason": "stop", "attempts": 1}
