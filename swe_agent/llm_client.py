# swe_agent/llm_client.py
import hashlib, json, time
from datetime import datetime, timezone
from pathlib import Path
from openai import OpenAI
from .config import (OPENAI_API_KEY, OPENAI_API_BASE_URL, GPT_MODEL,
                     MAX_LLM_CALLS_PER_BUG, MAX_TOKENS_PER_BUG)


class BudgetExceededError(Exception):
    pass


class LLMClient:
    """
    ONE instance per (baseline, bug). All baselines must use this — no direct OpenAI imports.
    """
    def __init__(self, baseline: str, bug_id: str):
        self.baseline   = baseline
        self.bug_id     = bug_id
        self._calls     = 0
        self._tokens    = {"prompt": 0, "completion": 0, "total": 0}
        self._latency   = 0.0
        self._client    = OpenAI(
            api_key=OPENAI_API_KEY,
            base_url=OPENAI_API_BASE_URL,
        )

    # ── Public API ──────────────────────────────────────────────────────────

    def chat(self, messages: list[dict], purpose: str, attempt: int,
             out_dir: Path, max_tokens: int = 1000) -> str:
        self._check_budget()
        prompt_text = json.dumps(messages)
        prompt_hash = hashlib.sha256(prompt_text.encode()).hexdigest()

        ts_start = datetime.now(timezone.utc)
        t0 = time.monotonic()

        response = self._client.chat.completions.create(
            model=GPT_MODEL,
            messages=messages,
            max_tokens=max_tokens,
        )

        latency = time.monotonic() - t0
        ts_end  = datetime.now(timezone.utc)

        usage = self._parse_usage(response)
        self._update_counters(usage, latency)

        response_content = response.choices[0].message.content

        self._write_call_log(out_dir, {
            "ts_start":    ts_start.isoformat(),
            "ts_end":      ts_end.isoformat(),
            "baseline":    self.baseline,
            "bug":         self.bug_id,
            "attempt":     attempt,
            "purpose":     purpose,
            "model":       GPT_MODEL,
            "api_base":    OPENAI_API_BASE_URL,
            "usage":       usage,
            "latency_sec": round(latency, 3),
            "prompt_sha256": prompt_hash,
            "response":    response_content,
        })

        return response_content

    # ── Aggregates (written into result.json) ───────────────────────────────

    def summary(self) -> dict:
        return {
            "calls":             self._calls,
            "prompt_tokens":     self._tokens["prompt"],
            "completion_tokens": self._tokens["completion"],
            "total_tokens":      self._tokens["total"],
            "latency_sec_total": round(self._latency, 3),
        }

    # ── Internals ───────────────────────────────────────────────────────────

    def _check_budget(self):
        if self._calls >= MAX_LLM_CALLS_PER_BUG:
            raise BudgetExceededError(
                f"LLM call budget exceeded: {self._calls}/{MAX_LLM_CALLS_PER_BUG}")
        if self._tokens["total"] >= MAX_TOKENS_PER_BUG:
            raise BudgetExceededError(
                f"Token budget exceeded: {self._tokens['total']}/{MAX_TOKENS_PER_BUG}")

    def _parse_usage(self, response) -> dict:
        u = getattr(response, "usage", None)
        if u is None:
            return {"prompt_tokens": 0, "completion_tokens": 0,
                    "total_tokens": 0, "tokens_unknown": True}
        return {"prompt_tokens":     u.prompt_tokens,
                "completion_tokens": u.completion_tokens,
                "total_tokens":      u.total_tokens}

    def _update_counters(self, usage: dict, latency: float):
        self._calls += 1
        self._tokens["prompt"]     += usage.get("prompt_tokens",     0)
        self._tokens["completion"] += usage.get("completion_tokens", 0)
        self._tokens["total"]      += usage.get("total_tokens",      0)
        self._latency += latency

    @staticmethod
    def _write_call_log(out_dir: Path, record: dict):
        out_dir.mkdir(parents=True, exist_ok=True)
        with open(out_dir / "llm_calls.jsonl", "a") as f:
            f.write(json.dumps(record) + "\n")
