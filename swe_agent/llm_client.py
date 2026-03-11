# swe_agent/llm_client.py
import hashlib, json, time
from datetime import datetime, timezone
from pathlib import Path
from openai import OpenAI
from .config import (OPENAI_API_KEY, OPENAI_API_BASE_URL, GPT_MODEL,
                     MAX_LLM_CALLS_PER_BUG, MAX_TOKENS_PER_BUG)

# ── ANSI Color Codes ────────────────────────────────────────────────────────
class Colors:
    """ANSI color codes for terminal output."""
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"

    # Foreground colors
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    MAGENTA = "\033[95m"
    CYAN = "\033[96m"
    WHITE = "\033[97m"
    GRAY = "\033[90m"

    # Background colors
    BG_BLUE = "\033[44m"
    BG_MAGENTA = "\033[45m"
    BG_CYAN = "\033[46m"


def colorize(text: str, color: str) -> str:
    """Wrap text with ANSI color codes."""
    return f"{color}{text}{Colors.RESET}"


class BudgetExceededError(Exception):
    pass


class LLMClient:
    """
    ONE instance per (baseline, bug). All baselines must use this — no direct OpenAI imports.
    """
    def __init__(self, baseline: str, bug_id: str, verbose: bool = False):
        self.baseline   = baseline
        self.bug_id     = bug_id
        self._calls     = 0
        self._tokens    = {"prompt": 0, "completion": 0, "total": 0}
        self._latency   = 0.0
        self.verbose    = verbose
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

        # Verbose: Show prompt
        if self.verbose:
            print("\n" + "=" * 70)
            print(colorize(f" [LLM CALL #{self._calls + 1}] {self.baseline} - {purpose}", Colors.BOLD + Colors.CYAN))
            print("=" * 70)
            for msg in messages:
                role = msg.get("role", "unknown")
                content = msg.get("content", "")[:2000]  # Truncate long prompts
                role_color = Colors.MAGENTA if role == "system" else Colors.BLUE if role == "user" else Colors.GRAY
                print(colorize(f"\n [{role.upper()}]:", role_color))
                print("-" * 50)
                print(content[:1500] + ("..." if len(content) > 1500 else ""))
            print(colorize("\n [Waiting for response...]", Colors.DIM + Colors.YELLOW))
            print("-" * 70)

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

        # Verbose: Show response
        if self.verbose:
            print(colorize("\n [RESPONSE]:", Colors.BOLD + Colors.GREEN))
            print("-" * 50)
            print(response_content[:2000] + ("..." if len(response_content) > 2000 else ""))
            print("-" * 70)
            tokens = usage.get("total_tokens", "?")
            print(colorize(f" → Tokens: {tokens} | Latency: {latency:.2f}s", Colors.DIM + Colors.CYAN))

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
