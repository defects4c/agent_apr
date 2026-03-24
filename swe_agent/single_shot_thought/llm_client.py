# swe_agent/llm_client.py
import hashlib, json, time
from datetime import datetime, timezone
from pathlib import Path
from openai import OpenAI
from .config import (OPENAI_API_KEY, OPENAI_API_BASE_URL, GPT_MODEL,
                     MAX_LLM_CALLS_PER_BUG, MAX_TOKENS_PER_BUG)


class Colors:
    RESET = "\033[0m"; BOLD = "\033[1m"; DIM = "\033[2m"
    RED = "\033[91m"; GREEN = "\033[92m"; YELLOW = "\033[93m"
    BLUE = "\033[94m"; MAGENTA = "\033[95m"; CYAN = "\033[96m"
    WHITE = "\033[97m"; GRAY = "\033[90m"
    BG_BLUE = "\033[44m"; BG_MAGENTA = "\033[45m"; BG_CYAN = "\033[46m"


def colorize(text: str, color: str) -> str:
    return f"{color}{text}{Colors.RESET}"


class BudgetExceededError(Exception):
    pass


class LLMClient:
    """ONE instance per (baseline, bug). All baselines must use this."""

    def __init__(self, baseline: str, bug_id: str, verbose: bool = False):
        self.baseline = baseline
        self.bug_id = bug_id
        self._calls = 0
        self._tokens = {"prompt": 0, "completion": 0, "total": 0}
        self._latency = 0.0
        self.verbose = verbose
        self._client = OpenAI(api_key=OPENAI_API_KEY, base_url=OPENAI_API_BASE_URL)

    def chat(self, messages: list, purpose: str, attempt: int,
             out_dir: Path, max_tokens: int = 1000,
             temperature: float = None) -> str:
        """Make an LLM call.

        Args:
            temperature: If None, uses model default (typically 0 for deterministic).
                         Set >0 (e.g. 0.7) for Self-Consistency sampling.
        """
        self._check_budget()
        prompt_hash = hashlib.sha256(json.dumps(messages).encode()).hexdigest()
        ts_start = datetime.now(timezone.utc)
        t0 = time.monotonic()

        if self.verbose:
            print("\n" + "=" * 70)
            print(colorize(f" [LLM #{self._calls+1}] {self.baseline} - {purpose}", Colors.BOLD + Colors.CYAN))
            print("=" * 70)
            for msg in messages:
                role = msg.get("role", "?")
                content = msg.get("content", "")[:1500]
                rc = Colors.MAGENTA if role == "system" else Colors.BLUE
                print(colorize(f"\n [{role.upper()}]:", rc))
                print(content[:1200] + ("..." if len(content) > 1200 else ""))

        kwargs = {"model": GPT_MODEL, "messages": messages, "max_tokens": max_tokens}
        if temperature is not None:
            kwargs["temperature"] = temperature

        response = self._client.chat.completions.create(**kwargs)
        latency = time.monotonic() - t0
        usage = self._parse_usage(response)
        self._update_counters(usage, latency)
        content = response.choices[0].message.content

        if self.verbose:
            print(colorize(f"\n [RESPONSE] ({usage.get('total_tokens','?')} tok, {latency:.1f}s):", Colors.GREEN))
            print(content[:1500] + ("..." if len(content) > 1500 else ""))

        self._write_call_log(out_dir, {
            "ts_start": ts_start.isoformat(), "ts_end": datetime.now(timezone.utc).isoformat(),
            "baseline": self.baseline, "bug": self.bug_id, "attempt": attempt,
            "purpose": purpose, "model": GPT_MODEL, "usage": usage,
            "latency_sec": round(latency, 3), "prompt_sha256": prompt_hash,
            "temperature": temperature, "response": content,
        })
        return content

    def summary(self) -> dict:
        return {
            "calls": self._calls,
            "prompt_tokens": self._tokens["prompt"],
            "completion_tokens": self._tokens["completion"],
            "total_tokens": self._tokens["total"],
            "latency_sec_total": round(self._latency, 3),
        }

    def _check_budget(self):
        if self._calls >= MAX_LLM_CALLS_PER_BUG:
            raise BudgetExceededError(f"LLM calls: {self._calls}/{MAX_LLM_CALLS_PER_BUG}")
        if self._tokens["total"] >= MAX_TOKENS_PER_BUG:
            raise BudgetExceededError(f"Tokens: {self._tokens['total']}/{MAX_TOKENS_PER_BUG}")

    def _parse_usage(self, response) -> dict:
        u = getattr(response, "usage", None)
        if u is None:
            return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        return {"prompt_tokens": u.prompt_tokens, "completion_tokens": u.completion_tokens,
                "total_tokens": u.total_tokens}

    def _update_counters(self, usage, latency):
        self._calls += 1
        for k in ("prompt", "completion", "total"):
            self._tokens[k] += usage.get(f"{k}_tokens", 0)
        self._latency += latency

    @staticmethod
    def _write_call_log(out_dir: Path, record: dict):
        out_dir.mkdir(parents=True, exist_ok=True)
        with open(out_dir / "llm_calls.jsonl", "a") as f:
            f.write(json.dumps(record) + "\n")
