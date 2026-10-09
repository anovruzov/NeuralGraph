#!/usr/bin/env python3
"""Build the product's ModelRouter from MYCELIC_* / OPENAI_* environment variables only and run real tasks
against whatever OpenAI-compatible server those variables point at (here: llama-server on 127.0.0.1:8082).

    set -a; . research/mycelic_e2e/live/live_env.sh; set +a
    python3 research/mycelic_e2e/live/verify_live_provider.py [--evaluate]

The product reads its environment when `mycelic.config` is first imported, so the variables must be exported
before this process starts. Nothing is hard-coded here: the printed `router.describe()` shows what the router
actually resolved.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))      # repo root: mycelic + NeuralGraph packages

from mycelic.config import load_settings                       # noqa: E402  (reads os.environ at import time)
from mycelic.models.router import DefaultModelRouter          # noqa: E402


async def main() -> int:
    s = load_settings()
    router = DefaultModelRouter.from_settings(s)                # same constructor the runtime uses (build_router -> from_settings)
    print("settings.model_tiers :", json.dumps(s.model_tiers))
    print("settings.openai_base_url:", s.openai_base_url, "| api key set:", bool(s.openai_api_key),
          "| max_parallel:", s.model_max_parallel, "| timeout_s:", s.model_timeout_seconds, "| embed:", s.embed_provider)
    print("router.describe()['tiers']:", json.dumps(router.describe()["tiers"]))
    ok = True
    try:
        t0 = time.perf_counter()
        out = await router.run_task(
            "answer_from_evidence",
            {"question": "When was the Atlas billing migration cut over and who approved it?",
             "evidence": [
                 {"ref_id": "ev-101", "excerpt": "Atlas billing migration cut over to the new ledger on 2026-03-14; approved by Dana Okafor (finance lead).",
                  "observed_at": "2026-03-15T09:00:00Z"},
                 {"ref_id": "ev-102", "excerpt": "Office plants are watered on Tuesdays and Fridays by the facilities team.",
                  "observed_at": "2026-02-02T08:00:00Z"}]},
            tenant_id="live-verify")
        print(f"\n[answer_from_evidence] {time.perf_counter() - t0:.1f}s ->")
        print(json.dumps(out, indent=2, ensure_ascii=False))
        ok = ok and isinstance(out.get("answer"), str) and out.get("no_evidence") is False and "ev-101" in (out.get("used_ref_ids") or [])
        if "--evaluate" in sys.argv:
            t0 = time.perf_counter()
            ev = await router.run_task(
                "evaluate_responses",
                {"question": "When did the Atlas billing migration cut over?",
                 "responses": [
                     {"response_id": "r1", "holder_id": "h1", "content": "Atlas billing cut over on 2026-03-14.", "confidence": 0.8,
                      "refs": [{"ref_id": "ev-101", "root_id": "root-1", "root_known": True, "observed_at": "2026-03-15T09:00:00Z"}]},
                     {"response_id": "r2", "holder_id": "h2", "content": "Atlas billing cut over on 2026-03-21.", "confidence": 0.6,
                      "refs": [{"ref_id": "ev-207", "root_id": "root-2", "root_known": True, "observed_at": "2026-03-22T09:00:00Z"}]}],
                 "existing_claims": [], "today": "2026-04-01"},
                tenant_id="live-verify")
            print(f"\n[evaluate_responses] {time.perf_counter() - t0:.1f}s ->")
            print(json.dumps(ev, indent=2, ensure_ascii=False))
    finally:
        print("\nusage ledger totals:", json.dumps(router.ledger.totals(), ensure_ascii=False))
        for c in router.ledger.calls:
            print("  call:", json.dumps({k: getattr(c, k) for k in ("provider", "model", "tier", "purpose", "input_tokens", "output_tokens", "latency_ms", "ok", "error")}))
        await router.close()
    print("\nRESULT:", "OK" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
