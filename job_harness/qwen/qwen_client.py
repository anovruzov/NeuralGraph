"""Qwen client over any OpenAI-compatible /chat/completions endpoint.

Failure policy for every call:
    1. call -> parse strict JSON -> validate against schema
    2. on failure: deterministic JSON repair -> validate
    3. on failure: one repair round-trip asking the model to fix its own output
    4. on failure: return the schema's safe default (never raise into the pipeline)

Identical requests are cached in SQLite, keyed by model + function + prompt hash.
"""
from __future__ import annotations

import hashlib
import json
import random
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

import httpx
from pydantic import BaseModel, ValidationError

from ..config.logging_setup import get_logger
from ..config.settings import QwenConfig
from . import prompts as P
from .json_repair import repair_json
from .schemas import (
    AnswerValidation, DuplicateVerdict, FieldAnswer, FieldClassification,
    JobClassification, JobScore, Requirements, ScoreBreakdown, SubmissionEvaluation,
)

log = get_logger("qwen")


class QwenError(RuntimeError):
    pass


@dataclass
class CallResult:
    data: BaseModel
    cache_hit: bool = False
    degraded: bool = False          # safe default was returned
    attempts: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: int = 0
    error: Optional[str] = None


SAFE_DEFAULTS: dict[str, Callable[[], BaseModel]] = {
    "classify_job": lambda: JobClassification(
        is_relevant=False, reason="model output unusable; defaulting to not relevant"),
    "score_job": lambda: JobScore(
        score=0, breakdown=ScoreBreakdown(), decision="SKIP",
        required_qualifications_met=False, experience_plausible=False,
        reason="model output unusable; defaulting to SKIP"),
    "extract_requirements": lambda: Requirements(),
    "classify_form_field": lambda: FieldClassification(
        semantic_key="other", field_type="text", confidence=0.0,
        reason="model output unusable"),
    "answer_form_question": lambda: FieldAnswer(
        field_type="", answer="", confidence=0.0, source="unknown",
        safe_to_submit=False, reason="model output unusable; refusing to guess"),
    "detect_duplicate": lambda: DuplicateVerdict(
        is_duplicate=False, confidence=0.0, reason="model output unusable"),
    "evaluate_application": lambda: SubmissionEvaluation(
        submitted=False, confidence=0.0, signal="none",
        reason="model output unusable; treating as NOT submitted"),
    "validate_answer": lambda: AnswerValidation(
        valid=False, grounded=False, problems=["validator output unusable"],
        reason="model output unusable"),
}


class QwenClient:
    def __init__(self, config: QwenConfig, db: Any = None, run_id: Optional[str] = None,
                 transport: Optional[httpx.BaseTransport] = None) -> None:
        self.config = config
        self.db = db
        self.run_id = run_id
        headers = {"Content-Type": "application/json"}
        if config.api_key:
            headers["Authorization"] = f"Bearer {config.api_key}"
        self._client = httpx.Client(
            base_url=config.base_url.rstrip("/"),
            headers=headers,
            timeout=config.timeout_seconds,
            transport=transport,
        )
        self.stats = {"requests": 0, "cache_hits": 0, "prompt_tokens": 0,
                      "completion_tokens": 0, "cost_usd": 0.0, "failures": 0,
                      "degraded": 0}

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "QwenClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # ------------------------------------------------------------- plumbing

    def _cache_key(self, function: str, system: str, user: str) -> str:
        basis = "\x00".join([self.config.model, function, system, user,
                             str(self.config.temperature)])
        return hashlib.sha256(basis.encode("utf-8")).hexdigest()

    def _cost(self, prompt_tokens: int, completion_tokens: int) -> float:
        return (prompt_tokens / 1e6) * self.config.price_input_per_mtok + \
               (completion_tokens / 1e6) * self.config.price_output_per_mtok

    def _post(self, messages: list[dict[str, str]]) -> tuple[str, int, int]:
        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
        }
        if self.config.json_mode:
            payload["response_format"] = {"type": "json_object"}

        last_exc: Optional[Exception] = None
        for attempt in range(self.config.max_retries + 1):
            try:
                resp = self._client.post("/chat/completions", json=payload)
                if resp.status_code in (400, 422) and self.config.json_mode:
                    # Endpoint rejects response_format; drop it and retry once.
                    payload.pop("response_format", None)
                    self.config.json_mode = False
                    log.warning("endpoint rejected json_mode; disabling it")
                    resp = self._client.post("/chat/completions", json=payload)
                if resp.status_code == 429 or resp.status_code >= 500:
                    raise QwenError(f"HTTP {resp.status_code}: {resp.text[:300]}")
                resp.raise_for_status()
                body = resp.json()
                choice = (body.get("choices") or [{}])[0]
                content = (choice.get("message") or {}).get("content") or ""
                usage = body.get("usage") or {}
                return (content,
                        int(usage.get("prompt_tokens") or 0),
                        int(usage.get("completion_tokens") or 0))
            except Exception as exc:  # network, HTTP, or malformed envelope
                last_exc = exc
                if attempt < self.config.max_retries:
                    backoff = (2 ** attempt) + random.uniform(0, 0.5)
                    log.warning("qwen request failed, retrying",
                                extra={"attempt": attempt + 1, "backoff_s": round(backoff, 2),
                                       "error": str(exc)[:200]})
                    time.sleep(backoff)
        raise QwenError(f"qwen request failed after retries: {last_exc}")

    def _call(self, function: str, user_prompt: str, schema: type[BaseModel],
              use_cache: bool = True) -> CallResult:
        system = P.system_prompt(function)
        key = self._cache_key(function, system, user_prompt)

        if use_cache and self.config.cache_enabled and self.db is not None:
            cached = self.db.cache_get(key)
            if cached is not None:
                try:
                    data = schema.model_validate(cached)
                except ValidationError:
                    data = None
                if data is not None:
                    self.stats["cache_hits"] += 1
                    self._log_call(function, key, cache_hit=True, ok=True)
                    return CallResult(data=data, cache_hit=True, attempts=0)

        started = time.time()
        messages = [{"role": "system", "content": system},
                    {"role": "user", "content": user_prompt}]
        attempts, ptok, ctok, error = 0, 0, 0, None
        raw = ""
        try:
            attempts += 1
            raw, ptok, ctok = self._post(messages)
            result = self._parse(raw, schema)
            if result is None:
                # One repair round-trip: hand the model its own broken output back.
                attempts += 1
                repair_msgs = messages + [
                    {"role": "assistant", "content": raw[:4000]},
                    {"role": "user", "content":
                        "That was not valid JSON matching the requested shape. "
                        "Reply with ONLY the corrected JSON object, no prose."},
                ]
                raw2, p2, c2 = self._post(repair_msgs)
                ptok, ctok = ptok + p2, ctok + c2
                result = self._parse(raw2, schema)
                raw = raw2
        except Exception as exc:
            error = str(exc)[:500]
            result = None
            log.error("qwen call failed", extra={"function": function, "error": error})

        latency_ms = int((time.time() - started) * 1000)
        self.stats["requests"] += 1
        self.stats["prompt_tokens"] += ptok
        self.stats["completion_tokens"] += ctok
        cost = self._cost(ptok, ctok)
        self.stats["cost_usd"] += cost

        degraded = result is None
        if degraded:
            self.stats["degraded"] += 1
            if error:
                self.stats["failures"] += 1
            result = SAFE_DEFAULTS[function]()
            log.warning("qwen output unusable; using safe default",
                        extra={"function": function, "raw_excerpt": (raw or "")[:200]})
        elif use_cache and self.config.cache_enabled and self.db is not None:
            self.db.cache_put(key, function, self.config.model, result.model_dump(mode="json"))

        self._log_call(function, key, cache_hit=False, ok=not degraded, ptok=ptok, ctok=ctok,
                       cost=cost, latency_ms=latency_ms, attempts=attempts, error=error)
        return CallResult(data=result, cache_hit=False, degraded=degraded, attempts=attempts,
                          prompt_tokens=ptok, completion_tokens=ctok,
                          latency_ms=latency_ms, error=error)

    @staticmethod
    def _parse(raw: str, schema: type[BaseModel]) -> Optional[BaseModel]:
        for candidate in (raw, None):
            if candidate is None:
                data = repair_json(raw)
            else:
                try:
                    data = json.loads(candidate)
                except (ValueError, TypeError):
                    continue
            if not isinstance(data, dict):
                continue
            try:
                return schema.model_validate(data)
            except ValidationError as exc:
                log.debug("schema validation failed", extra={"error": str(exc)[:300]})
                continue
        data = repair_json(raw)
        if isinstance(data, dict):
            try:
                return schema.model_validate(data)
            except ValidationError:
                return None
        return None

    def _log_call(self, function: str, key: str, cache_hit: bool, ok: bool,
                  ptok: int = 0, ctok: int = 0, cost: float = 0.0,
                  latency_ms: int = 0, attempts: int = 0,
                  error: Optional[str] = None) -> None:
        if self.db is None:
            return
        try:
            self.db.record_qwen_call(
                run_id=self.run_id, function=function, model=self.config.model,
                cache_key=key, cache_hit=int(cache_hit), prompt_tokens=ptok,
                completion_tokens=ctok, total_tokens=ptok + ctok,
                estimated_cost_usd=cost, latency_ms=latency_ms, attempts=attempts,
                ok=int(ok), error=error,
            )
            if self.run_id:
                self.db.bump_run(self.run_id, "qwen_requests", 0 if cache_hit else 1)
                self.db.bump_run(self.run_id, "qwen_tokens", ptok + ctok)
                if cost:
                    self.db.add_run_cost(self.run_id, cost)
        except Exception as exc:  # telemetry must never break the pipeline
            log.debug("failed to record qwen call", extra={"error": str(exc)[:200]})

    # ------------------------------------------------------------ functions

    def classify_job(self, job: dict[str, Any], target_roles: list[str]) -> JobClassification:
        return self._call("classify_job", P.classify_job(job, target_roles),
                          JobClassification).data  # type: ignore[return-value]

    def score_job(self, job: dict[str, Any], profile_summary: str, resume_text: str,
                  weights: dict[str, int],
                  requirements: dict[str, Any] | None = None) -> JobScore:
        return self._call(
            "score_job",
            P.score_job(job, profile_summary, resume_text, weights, requirements),
            JobScore).data  # type: ignore[return-value]

    def extract_requirements(self, description: str) -> Requirements:
        return self._call("extract_requirements", P.extract_requirements(description),
                          Requirements).data  # type: ignore[return-value]

    def classify_form_field(self, field: dict[str, Any]) -> FieldClassification:
        return self._call("classify_form_field", P.classify_form_field(field),
                          FieldClassification).data  # type: ignore[return-value]

    def answer_form_question(self, field: dict[str, Any], profile_json: str,
                             resume_text: str, job_context: dict[str, Any],
                             policy: dict[str, Any]) -> FieldAnswer:
        return self._call(
            "answer_form_question",
            P.answer_form_question(field, profile_json, resume_text, job_context, policy),
            FieldAnswer).data  # type: ignore[return-value]

    def detect_duplicate(self, candidate: dict[str, Any],
                         existing: list[dict[str, Any]]) -> DuplicateVerdict:
        return self._call("detect_duplicate", P.detect_duplicate(candidate, existing),
                          DuplicateVerdict).data  # type: ignore[return-value]

    def evaluate_application(self, page_state: dict[str, Any]) -> SubmissionEvaluation:
        # Page state is unique per submission; caching it would be wrong.
        return self._call("evaluate_application", P.evaluate_application(page_state),
                          SubmissionEvaluation, use_cache=False).data  # type: ignore[return-value]

    def validate_answer(self, field: dict[str, Any], answer: Any, profile_json: str,
                        resume_text: str) -> AnswerValidation:
        return self._call("validate_answer",
                          P.validate_answer(field, answer, profile_json, resume_text),
                          AnswerValidation).data  # type: ignore[return-value]

    def health_check(self) -> tuple[bool, str]:
        try:
            content, _, _ = self._post([
                {"role": "system", "content": "Reply with JSON only."},
                {"role": "user", "content": 'Return {"ok": true}'},
            ])
            data = repair_json(content) or {}
            return bool(data.get("ok")) or bool(content), content[:200]
        except Exception as exc:
            return False, str(exc)[:300]
