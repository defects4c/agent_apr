# Claude Code Implementation Guide
# Multi-Baseline Agent-Based LLM Repair for Defects4J

> **How to use this file:** Drop it in your repo root. Claude Code reads it top-to-bottom and
> implements every section in order. Sections marked `[CODE]` contain exact contracts to implement.
> Sections marked `[PATTERN]` describe patterns derived from the reference HyperAgent implementation.

---

## 0. Shared Problem Folder — The Single Source of Truth

All five baselines (Agentless, SWE-agent, OpenHands, OpenClaw, Claude Code) read from the
**same** problem folder. This folder is pre-built once; no baseline may write into it.

```
data/
├── defects4j/                         ← read-only problem data
│   ├── Lang_1/
│   │   ├── failing_tests              ← raw D4J failure output
│   │   ├── snippet.json               ← buggy method snippets + is_bug flags
│   │   └── test_snippet.json          ← test case snippets + metadata
│   ├── Lang_2/
│   ├── Math_5/
│   └── ...
└── repos/                             ← live D4J checkouts (one per bug)
    ├── Lang-1/                        ← checked out by runner, cleaned after run
    └── Math-5/
```

### `snippet.json` schema (per entry)
```json
{
  "name":       "org.apache.commons.lang3.math.NumberUtils.createNumber",
  "file":       "src/main/java/org/apache/commons/lang3/math/NumberUtils.java",
  "begin_line": 442,
  "end_line":   540,
  "snippet":    "...",
  "is_bug":     true
}
```

### `failing_tests` format (from D4J — parse with `_load_fail_info`)
```
--- org.apache.commons.lang3.math.NumberUtilsTest::testLang300
java.lang.StringIndexOutOfBoundsException: String index out of range: 0
	at org.apache.commons.lang3.math.NumberUtils.createNumber(NumberUtils.java:455)
	at org.apache.commons.lang3.math.NumberUtilsTest.testLang300(NumberUtilsTest.java:154)
```

**Bug name convention:** `{Project}_{ID}` in filesystem, `{Project}-{ID}` in D4J CLI.

---

### The final objective :
 the executed result of report for different baselines, about the sucessrate, costin token , cost in time and failure case in group analysis. 
save the execute result into swe_agent/result.md 

## 1. Full Project Layout

```
swe_agent/                         ← Python package (note: underscore for importability)
├── __init__.py
├── config.py
├── llm_client.py                  ← single LLM wrapper for ALL baselines
├── budget.py
├── trace.py
├── reason.py
├── defects4j.py                   ← D4J CLI wrapper + bash bridge
├── localize.py
├── apply_patch.py
├── tests_runner.py
│
├── tasks/                         ← mirrors HyperAgent task structure
│   ├── __init__.py
│   ├── base.py                    ← BaseTask, Result (matches reference impl)
│   ├── fault_localization.py      ← FaultLocalization (refactored from reference)
│   └── automated_program_repair.py← AutomatedProgramRepair (refactored from reference)
│
├── patch_generators/
│   ├── __init__.py
│   ├── base.py                    ← abstract PatchGenerator interface
│   ├── agentless.py               ← 1–2 LLM calls per attempt
...... more baseline from .claude
│   ├── swe_agent.py               ← ReAct loop with file tools
│   ├── openhands.py               ← budgeted tool-use loop
│   ├── openclaw.py                ← structured search → analyze → patch
│   └── claude_code.py             ← skill-based read/search/propose
│
├── runner.py                      ← single-bug pipeline
└── eval.py                        ← batch evaluation + report

benchmarks/
├── defects4j_small.txt            ← e.g. Lang_1 … Lang_10
└── defects4j_full.txt

outputs/                           ← auto-created
data/
├── defects4j/                     ← problem data (pre-built, read-only)
└── repos/                         ← live checkouts
```

---

## 2. `config.py` [CODE]

```python
# swe_agent/config.py
import os

# ── Paths ──────────────────────────────────────────────────────────────────
D4J_HOME         = os.environ.get("D4J_HOME", "/opt/defects4j")
D4J_FOLDER       = os.environ.get("D4J_FOLDER", "data/defects4j")
REPOS_DIR        = os.environ.get("REPOS_DIR",  "data/repos")
WORKSPACE_ROOT   = os.environ.get("WORKSPACE_ROOT", "outputs")

# ── LLM endpoint (NEVER hardcode; always read from env) ────────────────────
OPENAI_API_KEY      = os.environ.get("OPENAI_API_KEY",      "11")
OPENAI_API_BASE_URL = os.environ.get("OPENAI_API_BASE_URL", "http://157.10.162.82:443/v1/")
GPT_MODEL           = os.environ.get("GPT_MODEL",           "gpt-5.1")

# ── Budget (identical across ALL baselines — enforced by BudgetManager) ────
MAX_ATTEMPTS_PER_BUG       = int(os.environ.get("MAX_ATTEMPTS", "5"))
MAX_LLM_CALLS_PER_ATTEMPT  = 3
MAX_LLM_CALLS_PER_BUG      = 15
MAX_TOKENS_PER_BUG         = 200_000
MAX_PATCH_LINES            = 200
MAX_FILES_CHANGED          = 2
CONTEXT_LINES_PER_LOCATION = 200
MAX_LOCATIONS_PER_ATTEMPT  = 3

# ── Timeouts (seconds) ─────────────────────────────────────────────────────
TIMEOUT_PATCH_GEN  = 60
TIMEOUT_COMPILE    = 120
TIMEOUT_FUNC_TEST  = 180
TIMEOUT_REG_TEST   = 600

# ── JDK routing ────────────────────────────────────────────────────────────
JDK_MAP = {
    "Lang":    os.environ.get("JAVA8_HOME", "/usr/lib/jvm/java-8-openjdk-amd64"),
    "Math":    os.environ.get("JAVA8_HOME", "/usr/lib/jvm/java-8-openjdk-amd64"),
    "Time":    os.environ.get("JAVA8_HOME", "/usr/lib/jvm/java-8-openjdk-amd64"),
    "Chart":   os.environ.get("JAVA8_HOME", "/usr/lib/jvm/java-8-openjdk-amd64"),
    "Closure": os.environ.get("JAVA8_HOME", "/usr/lib/jvm/java-8-openjdk-amd64"),
}

# ── Baseline names (use these strings everywhere) ──────────────────────────
# Original 5 APR agent baselines
BASELINES_AGENT = ["agentless", "swe_agent", "openhands", "openclaw", "claude_code"]

# Prompting-strategy baselines (all adapted from the prompting literature)
#   standard         — no scaffold, direct patch request          (control)
#   zero_shot_cot    — "Let's think step by step"                 Kojima et al. NeurIPS 2022
#   few_shot_cot     — hand-written reasoning demonstrations      Wei et al. NeurIPS 2022
#   react            — Thought / Action / Observation loop        Yao et al. ICLR 2023
#   reflexion        — multi-trial verbal RL + memory             Shinn et al. NeurIPS 2023
#   self_consistency — N samples + majority / judge vote          Wang et al. ICLR 2023
#   tot              — branch + evaluate + backtrack (BFS)        Yao et al. NeurIPS 2023
#   got              — graph ops: generate + aggregate + refine   Besta et al. AAAI 2024
#   pot              — model writes executable Python fix         Chen et al. TMLR 2023
#   function_calling — structured tool-use via JSON schemas       OpenAI API (2023)
BASELINES_PROMPTING = [
    "standard",
    "zero_shot_cot",
    "few_shot_cot",
    "react",
    "reflexion",
    "self_consistency",
    "tot",
    "got",
    "pot",
    "function_calling",
]

# Combined list used by runner.py and eval.py
BASELINES = BASELINES_AGENT + BASELINES_PROMPTING
```

---

## 3. `tasks/base.py` [PATTERN from reference impl]

Refactor from the reference `FaultLocalization` / `BaseTask` pattern:

```python
# swe_agent/tasks/base.py
import os, json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

@dataclass
class Result:
    task:         str
    test_result:  str = ""        # "PASS" | "FAIL" | "ERROR"
    result_reason: str = ""
    proposed_patch: str = ""
    patch_diff:   str = ""
    kwargs:       dict = field(default_factory=dict)

    def __post_init__(self):
        # merge extra kwargs into .kwargs for backward compat
        if "correct" in self.kwargs:
            self.test_result = "PASS" if self.kwargs["correct"] else "FAIL"


class BaseTask:
    """
    Mirrors HyperAgent BaseTask.
    Subclasses override: setup(), construct_prompt(idx), run(system, idx), validate(...)
    """
    BUG_INFO_DIR: str = "data/defects4j"

    def __init__(self, logdir: str, split: str, _type: str = "pred", **kwargs):
        self.logdir    = Path(logdir)
        self.split     = split
        self._type     = _type
        self.logdir.mkdir(parents=True, exist_ok=True)
        self.setup()

    def setup(self):
        self.bug_names = sorted(os.listdir(self.BUG_INFO_DIR))

    def __len__(self):
        return len(self.bug_names)

    def bug_dir(self, bug_name: str) -> Path:
        return Path(self.BUG_INFO_DIR) / bug_name

    # ── Shared data loaders ────────────────────────────────────────────────

    def _load_fail_info(self, bug_name: str) -> dict:
        """
        Parses data/defects4j/<bug_name>/failing_tests
        Returns: {tc_signature: {error_message, stack_trace}}

        Format (from D4J):
          --- TestClass::testMethod
          ExceptionType: message
          \tat frame1
          \tat frame2
        """
        fail_info = {}
        tc_signature = None
        with open(self.bug_dir(bug_name) / "failing_tests") as f:
            for line in f:
                if line.startswith("--- "):
                    tc_name = line.split()[-1]
                    tc_signature = tc_name.replace("::", ".") + "()"
                    fail_info[tc_signature] = {"error_message": "", "stack_trace": ""}
                elif tc_signature:
                    key = "stack_trace" if line.startswith("\tat") else "error_message"
                    fail_info[tc_signature][key] += line
        return fail_info

    def _load_test_lists(self, bug_name: str) -> list[dict]:
        with open(self.bug_dir(bug_name) / "test_snippet.json") as f:
            return json.load(f)

    def _load_snippet_data(self, bug_name: str) -> list[dict]:
        with open(self.bug_dir(bug_name) / "snippet.json") as f:
            return json.load(f)

    def failing_test_signatures(self, fail_info: dict) -> list[str]:
        return list(fail_info.keys())
```

---

## 4. `tasks/fault_localization.py` [PATTERN]

Key methods to keep from reference, with LLM calls moved through `LLMClient`:

```python
# swe_agent/tasks/fault_localization.py
from .base import BaseTask, Result
from ..llm_client import LLMClient
import re

class FaultLocalization(BaseTask):

    RANGE_REGEX = r"\(line (?P<beginline>\d+),col (?P<begincol>\d+)\)-\(line (?P<endline>\d+),col (?P<endcol>\d+)\)"
    _MAX_REPETITION_IN_STACK = 5

    TASK_TEMPLATE = """Given following failed test case, localize which method in the codebase is responsible for the failure.
Failed Test: {test}
The test looks like:

```java
{test_snippets}
```

It failed with the following error message and call stack:

```
{failing_traces}
```

<output>Provide the method name in the format 'package.ClassName.methodName' that you think is responsible for the failure. No need to call editor to fix the fault.</output>"""

    def __init__(self, logdir, split, max_repetitions=3, max_num_tests=2, **kwargs):
        self.max_repetitions = max_repetitions
        self.max_num_tests   = max_num_tests
        super().__init__(logdir, split, _type="pred", **kwargs)

    def construct_prompt(self, idx: int) -> str:
        bug_name = self.bug_names[idx]
        fail_info = self._load_fail_info(bug_name)
        sigs = [s for s in self.failing_test_signatures(fail_info)
                if self.get_test_snippet(s, bug_name) is not None][:self.max_num_tests]
        snippets = "\n\n".join(self.get_test_snippet(s, bug_name).rstrip() for s in sigs)
        traces   = "\n\n".join(self.get_fail_info(s, bug_name, minimize=False).rstrip() for s in sigs)
        return self.TASK_TEMPLATE.format(test=sigs, test_snippets=snippets, failing_traces=traces)

    def get_fail_info(self, tc_signature: str, bug_name: str,
                      minimize: bool = False) -> str:
        """Returns error_message + stack_trace. If minimize=True, cleans both."""
        fi = self._load_fail_info(bug_name)[tc_signature]
        msg   = fi["error_message"].rstrip()
        stack = fi["stack_trace"].rstrip()
        if minimize:
            msg   = "\n".join(msg.splitlines()[:5])
            stack = self._clean_stack_trace(stack)
        return msg + "\n" + stack

    def get_test_snippet(self, signature: str, bug_name: str) -> str | None:
        """
        Retrieves and annotates test snippet with error location.
        Keeps the annotation logic from the reference implementation.
        Returns None if test case not found.
        """
        # ... (preserve full reference implementation logic here)
        pass

    def _clean_stack_trace(self, stack_trace: str) -> str:
        """Remove junit.framework frames and compress repeated subsequences."""
        # ... (preserve reference implementation logic)
        pass
```

---

## 5. `tasks/automated_program_repair.py` [PATTERN]

```python
# swe_agent/tasks/automated_program_repair.py
from .fault_localization import FaultLocalization
from .base import Result

class AutomatedProgramRepair(FaultLocalization):

    TASK_TEMPLATE = """Given following failed test case, fix the code responsible for the failure. If there are multiple faults, find and fix them.
Failed Test: {test}
The test looks like:

```java
{test_snippets}
```

It failed with the following error message and call stack:

```
{failing_traces}
```

<output>Provide the method name in the format 'package.ClassName.methodName' that you think is responsible for the failure. You also need to edit the code to fix the fault.</output>"""

    def __init__(self, logdir, **kwargs):
        super().__init__(logdir=logdir, split=kwargs.pop("split", "test"),
                         _type="patch", **kwargs)

    def validate(self, proposed_patch: str, idx: int) -> Result:
        """
        Checkout buggy version → apply patch → run D4J tests → parse result.
        Returns Result with test_result in {"PASS", "FAIL", "ERROR"}.
        """
        bug_name = self.bug_names[idx]
        project, bug_id = bug_name.split("_", 1)

        # apply + test via defects4j bash bridge
        result = self._run_bash("validate_patch", project, bug_id, proposed_patch)

        if result.returncode != 0:
            reason = self._extract_error_reason(result.stderr)
            return Result("apr", test_result="ERROR", result_reason=reason,
                          proposed_patch=proposed_patch)

        if "Failing tests: 0" in result.stdout:
            return Result("apr", test_result="PASS", result_reason="all tests passed",
                          proposed_patch=proposed_patch)

        reason = self._run_bash("get_test_error", project, bug_id).stdout
        return Result("apr", test_result="FAIL", result_reason=reason,
                      proposed_patch=proposed_patch)

    def report(self, results: list) -> dict:
        counts = {"correct": 0, "incorrect": 0, "error": 0}
        for r in results:
            if r.test_result == "PASS":   counts["correct"]   += 1
            elif r.test_result == "FAIL": counts["incorrect"] += 1
            else:                          counts["error"]     += 1
        total = len(results)
        counts["repair_rate"] = counts["correct"] / total if total else 0.0
        return counts

    @staticmethod
    def _extract_error_reason(stderr: str) -> str:
        if "error: " in stderr:
            s = stderr[stderr.find("error: "):]
            return s[:s.find("\n")]
        if "BUILD FAILED" in stderr:
            lines = stderr.split("\n")
            i = next((j for j, l in enumerate(lines) if "BUILD FAILED" in l), None)
            return lines[i + 1].strip() if i is not None else "BUILD FAILED"
        return "Test timed out after 600 seconds"
```

---

## 6. `defects4j.py` — CLI Wrapper + Bash Bridge [CODE]

This mirrors `run_bash` from the reference but adds structured return types.

```python
# swe_agent/defects4j.py
import os, subprocess
from pathlib import Path
from .config import D4J_HOME, REPOS_DIR, JDK_MAP, TIMEOUT_COMPILE, TIMEOUT_REG_TEST

BASH_SCRIPT = "swe_agent/tasks/utils/defects4j.sh"   # port from reference's defects4j.sh


def _env(project: str) -> dict:
    """Build subprocess env with correct JAVA_HOME for the project."""
    java_home = JDK_MAP.get(project, os.environ.get("JAVA_HOME", "/usr"))
    return {**os.environ, "JAVA_HOME": java_home,
            "PATH": f"{D4J_HOME}/framework/bin:{os.environ['PATH']}"}


def _run(cmd: list[str], project: str, timeout: int, log_path: Path | None = None):
    result = subprocess.run(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        universal_newlines=True, timeout=timeout, env=_env(project)
    )
    if log_path:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(result.stdout + "\n---STDERR---\n" + result.stderr)
    return result


def checkout(project: str, bug_id: int | str, version: str, workdir: Path,
             log_path: Path | None = None):
    """defects4j checkout -p {project} -v {bug_id}{b|f} -w {workdir}"""
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    cmd = ["defects4j", "checkout",
           "-p", project, "-v", f"{bug_id}{version}", "-w", str(workdir)]
    r = _run(cmd, project, timeout=120, log_path=log_path)
    if r.returncode != 0:
        raise RuntimeError(f"Checkout failed: {r.stderr[:300]}")


def compile(workdir: Path, project: str, log_path: Path | None = None):
    """Returns (success: bool, log: str)"""
    r = _run(["defects4j", "compile", "-w", str(workdir)],
             project, TIMEOUT_COMPILE, log_path)
    return r.returncode == 0, r.stdout + r.stderr


def test(workdir: Path, project: str,
         log_path: Path | None = None) -> tuple[int, list[str], str]:
    """Returns (failing_count, failing_test_names, full_log)"""
    r = _run(["defects4j", "test", "-w", str(workdir)],
             project, TIMEOUT_REG_TEST, log_path)
    log = r.stdout + r.stderr
    # parse "Failing tests: N" and "  - ClassName::method"
    failing_count = 0
    failing_tests = []
    for line in log.splitlines():
        if line.startswith("Failing tests:"):
            try: failing_count = int(line.split(":")[-1].strip())
            except ValueError: pass
        elif line.strip().startswith("- "):
            failing_tests.append(line.strip()[2:])
    return failing_count, failing_tests, log


def export_trigger_tests(workdir: Path, project: str) -> list[str]:
    r = _run(["defects4j", "export", "-p", "tests.trigger", "-w", str(workdir)],
             project, timeout=60)
    return [l.strip() for l in r.stdout.splitlines() if l.strip()]


def get_modified_classes(workdir: Path, project: str) -> list[str]:
    r = _run(["defects4j", "export", "-p", "classes.modified", "-w", str(workdir)],
             project, timeout=60)
    return [l.strip() for l in r.stdout.splitlines() if l.strip()]


def run_bash(function: str, project: str, bug_id: str,
             extra_arg1=None, extra_arg2=None):
    """
    Direct bridge to defects4j.sh — mirrors reference implementation's run_bash.
    Used by AutomatedProgramRepair.validate().
    """
    work_dir = os.path.join(REPOS_DIR, f"{project}-{bug_id}")
    java_home = JDK_MAP.get(project, "/usr")
    cmd = ["bash", BASH_SCRIPT, function, project, bug_id,
           work_dir, java_home, D4J_HOME,
           str(extra_arg1) if extra_arg1 else "",
           str(extra_arg2) if extra_arg2 else ""]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            universal_newlines=True)
    if result.stdout.endswith("\n"):
        result.stdout = result.stdout[:-1]
    return result
```

### `defects4j.sh` (bash bridge — port from reference)

```bash
#!/usr/bin/env bash
# swe_agent/tasks/utils/defects4j.sh
# Usage: defects4j.sh <function> <project> <bug_id> <work_dir> <java_home> <d4j_path> [extra1] [extra2]

FUNCTION=$1; PROJECT=$2; BUG_ID=$3; WORK_DIR=$4
export JAVA_HOME=$5
D4J_PATH=$6
EXTRA1=$7; EXTRA2=$8
export PATH="$D4J_PATH/framework/bin:$JAVA_HOME/bin:$PATH"

checkout_bug()    { defects4j checkout -p "$PROJECT" -v "${BUG_ID}b" -w "$WORK_DIR"; }
compile_bug()     { cd "$WORK_DIR" && defects4j compile; }
test_bug()        { cd "$WORK_DIR" && defects4j test; }
validate_patch()  {
    cd "$WORK_DIR"
    echo "$EXTRA1" | git apply --whitespace=fix -
    defects4j compile && defects4j test
}
get_patch_git_diff() { cd "$WORK_DIR" && git diff; }
get_test_error()     { cd "$WORK_DIR" && defects4j export -p tests.trigger; }

$FUNCTION
```

---

## 7. `llm_client.py` — Single LLM Wrapper [CODE]

```python
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
        })

        return response.choices[0].message.content

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
```

---

## 8. `trace.py` and `reason.py` [CODE]

### `trace.py`

```python
# swe_agent/trace.py
import json
from datetime import datetime, timezone
from pathlib import Path


class TraceWriter:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._path = path

    def log(self, event: dict):
        if "ts" not in event:
            event["ts"] = datetime.now(timezone.utc).isoformat()
        with open(self._path, "a") as f:
            f.write(json.dumps(event) + "\n")
```

### `reason.py`

```python
# swe_agent/reason.py

# ── Patch-apply codes ──────────────────────────────────────────────────────
PATCH_APPLY_HUNK_FAILED    = "PATCH_APPLY_HUNK_FAILED"
PATCH_APPLY_PATH_NOT_FOUND = "PATCH_APPLY_PATH_NOT_FOUND"
PATCH_SIZE_EXCEEDED        = "PATCH_SIZE_EXCEEDED"
PATCH_SCOPE_VIOLATION      = "PATCH_SCOPE_VIOLATION"

# ── Build codes ────────────────────────────────────────────────────────────
JAVAC_SYMBOL_NOT_FOUND = "JAVAC_SYMBOL_NOT_FOUND"
JAVAC_TYPE_MISMATCH    = "JAVAC_TYPE_MISMATCH"
MAVEN_ENFORCER         = "MAVEN_ENFORCER"
BUILD_FAILED_UNKNOWN   = "BUILD_FAILED_UNKNOWN"

# ── Test codes ─────────────────────────────────────────────────────────────
TRIGGER_TEST_STILL_FAILING = "TRIGGER_TEST_STILL_FAILING"
NEW_FAILURES_INTRODUCED    = "NEW_FAILURES_INTRODUCED"
TIMEOUT_FUNC_TEST          = "TIMEOUT_FUNC_TEST"
TIMEOUT_REG_TEST           = "TIMEOUT_REG_TEST"

# ── Terminal attempt status ────────────────────────────────────────────────
PATCH_GENERATED      = "PATCH_GENERATED"
PATCH_APPLY_FAILED   = "PATCH_APPLY_FAILED"
BUILD_FAILED         = "BUILD_FAILED"
FUNCTIONALITY_FAILED = "FUNCTIONALITY_FAILED"
REGRESSION_FAILED    = "REGRESSION_FAILED"
TIMEOUT              = "TIMEOUT"
REPAIRED             = "REPAIRED"


def parse_build_reason(compiler_output: str) -> str:
    if "cannot find symbol" in compiler_output:     return JAVAC_SYMBOL_NOT_FOUND
    if "incompatible types"  in compiler_output:     return JAVAC_TYPE_MISMATCH
    if "enforcer"            in compiler_output.lower(): return MAVEN_ENFORCER
    return BUILD_FAILED_UNKNOWN


def parse_test_reason(failing_before: set[str], failing_after: set[str]) -> str:
    """
    failing_before: set of trigger test names that failed pre-patch
    failing_after:  set of ALL tests failing post-patch
    """
    trigger_still_failing = failing_before & failing_after
    new_failures          = failing_after - failing_before
    if trigger_still_failing: return TRIGGER_TEST_STILL_FAILING
    if new_failures:          return NEW_FAILURES_INTRODUCED
    return REPAIRED
```

---

## 9. `patch_generators/base.py` [CODE]

```python
# swe_agent/patch_generators/base.py
from abc import ABC, abstractmethod
from pathlib import Path
from dataclasses import dataclass


@dataclass
class PatchResult:
    diff_text: str          # unified diff string; "" means generation failed
    metadata:  dict         # prompt_sha256, model, localization targets, etc.


class PatchGenerator(ABC):
    """Common interface all five baselines implement."""

    @abstractmethod
    def generate_patch(
        self,
        bug_id:            str,
        workdir:           Path,
        failing_info:      dict,   # {test_name: {error_message, stack_trace}}
        trigger_tests:     list[str],
        localization_hits: list,   # list[LocalizationHit]
        attempt_index:     int,
        out_dir:           Path,
        llm_client,                # LLMClient
    ) -> PatchResult:
        ...
```

---

### 10f. `patch_generators/standard.py` — Standard Prompting

**Paper:** Used as the no-scaffold control condition in Wei et al. (NeurIPS 2022).  
**APR design:** The minimal possible baseline — ask for a patch directly with no reasoning
instructions. Measures the model's default repair ability. Every other baseline is compared
against this as the zero-cost control.

```
Call 1  →  [context + location] + "output the patch"
```

```python
# swe_agent/patch_generators/standard.py
"""
Standard (direct) patch generator — zero-scaffold control baseline.
Paper: used as control in Wei et al. NeurIPS 2022.
Strategy: one call, no reasoning scaffold, direct patch request.
Budget: 1 LLM call per attempt (cheapest possible).
"""
from pathlib import Path
from .base import PatchGenerator, PatchResult
from ._shared import (build_fail_context, build_location_context,
                       extract_search_replace, PATCH_SYSTEM)

USER_TEMPLATE = """{fail_context}

## Suspicious location(s)
{location_context}

Output the patch that fixes this bug:

FILE: path/to/File.java
SEARCH: <exact lines>
REPLACE: <corrected lines>
"""


class StandardPatchGenerator(PatchGenerator):

    def generate_patch(
        self,
        bug_id, workdir, failing_info, trigger_tests,
        localization_hits, attempt_index, out_dir, llm_client,
    ) -> PatchResult:

        prompt = USER_TEMPLATE.format(
            fail_context=build_fail_context(bug_id, trigger_tests, failing_info),
            location_context=build_location_context(localization_hits, workdir),
        )
        response = llm_client.chat(
            [{"role": "system", "content": PATCH_SYSTEM},
             {"role": "user",   "content": prompt}],
            purpose="standard_patch_gen", attempt=attempt_index,
            out_dir=out_dir, max_tokens=1200,
        )
        return PatchResult(
            diff_text=extract_search_replace(response or ""),
            metadata={"strategy": "standard", "raw_response": response or ""},
        )
```

---

### 10g. `patch_generators/zero_shot_cot.py` — Zero-Shot CoT

**Paper:** Kojima et al., NeurIPS 2022 (arXiv:2205.11916).  
**APR design:** Append "Let's think step by step" to trigger a reasoning chain, then
run a second call to extract only the patch. This implements Kojima et al.'s original
two-stage process: Stage 1 elicits reasoning; Stage 2 extracts the structured answer.

```
Call 1  →  [context] + "Let's think step by step." → reasoning chain
Call 2  →  [context + reasoning] + "Now output the patch" → SEARCH/REPLACE
```

```python
# swe_agent/patch_generators/zero_shot_cot.py
"""
Zero-Shot Chain-of-Thought patch generator.
Paper: Kojima et al., NeurIPS 2022 (arXiv:2205.11916)
Strategy:
  Call 1 — "Let's think step by step" elicits a reasoning chain (Stage 1).
  Call 2 — reasoning chain fed back to extract a clean patch (Stage 2).
Budget: 2 LLM calls per attempt.
Note: "Let's think step by step" belongs to Kojima et al., NOT Wei et al.
      Wei et al. introduced few-shot CoT with demonstrations (see few_shot_cot.py).
"""
from pathlib import Path
from .base import PatchGenerator, PatchResult
from ._shared import (build_fail_context, build_location_context,
                       extract_search_replace, PATCH_SYSTEM)

STAGE1_TEMPLATE = """{fail_context}

## Suspicious location(s)
{location_context}

Let's think step by step about the root cause of this bug and how to fix it."""

STAGE2_TEMPLATE = """{fail_context}

## Your reasoning
{reasoning}

Now output the patch based on your reasoning above:

FILE: path/to/File.java
SEARCH: <exact lines>
REPLACE: <corrected lines>
"""


class ZeroShotCoTPatchGenerator(PatchGenerator):

    def generate_patch(
        self,
        bug_id, workdir, failing_info, trigger_tests,
        localization_hits, attempt_index, out_dir, llm_client,
    ) -> PatchResult:

        fail_ctx = build_fail_context(bug_id, trigger_tests, failing_info)
        loc_ctx  = build_location_context(localization_hits, workdir)

        # ── Call 1: elicit reasoning chain (Stage 1) ──────────────────────
        reasoning = llm_client.chat(
            [{"role": "user", "content": STAGE1_TEMPLATE.format(
                fail_context=fail_ctx, location_context=loc_ctx,
            )}],
            purpose="zscot_stage1", attempt=attempt_index,
            out_dir=out_dir, max_tokens=800,
        )

        # ── Call 2: extract structured patch (Stage 2) ────────────────────
        response = llm_client.chat(
            [{"role": "system", "content": PATCH_SYSTEM},
             {"role": "user",   "content": STAGE2_TEMPLATE.format(
                 fail_context=fail_ctx, reasoning=reasoning or "",
             )}],
            purpose="zscot_stage2", attempt=attempt_index,
            out_dir=out_dir, max_tokens=1200,
        )

        return PatchResult(
            diff_text=extract_search_replace(response or ""),
            metadata={"strategy": "zero_shot_cot",
                      "reasoning": reasoning or "",
                      "raw_response": response or ""},
        )
```

---

### 10h. `patch_generators/few_shot_cot.py` — Few-Shot CoT

**Paper:** Wei et al., NeurIPS 2022 (arXiv:2201.11903) — the original CoT paper.  
**APR design:** Prepend 2–3 hand-written *(bug context → step-by-step reasoning → patch)*
demonstrations before the new bug. The model learns the reasoning pattern from examples
and applies it. This is consistently stronger than zero-shot CoT on reasoning tasks.

> The demonstrations are domain-specific (Java APR), not general arithmetic examples.
> Teams using this baseline should maintain a small library of worked repair examples.

```
Call 1  →  [demo 1] + [demo 2] + [new bug context] → patch
```

```python
# swe_agent/patch_generators/few_shot_cot.py
"""
Few-Shot Chain-of-Thought patch generator.
Paper: Wei et al., NeurIPS 2022 (arXiv:2201.11903) — the ORIGINAL CoT paper.
Strategy: one call with 2 hand-written APR demonstrations before the new bug.
Budget: 1 LLM call per attempt.
Note: this is distinct from zero_shot_cot.py — no "Let's think step by step",
      relies entirely on in-context demonstrations showing reasoning chains.
"""
from pathlib import Path
from .base import PatchGenerator, PatchResult
from ._shared import (build_fail_context, build_location_context,
                       extract_search_replace, PATCH_SYSTEM)

# ── APR-specific reasoning demonstrations ─────────────────────────────────────
# Replace/extend these with real Defects4J examples from your benchmark.
DEMONSTRATIONS = """
## Demonstration 1

Bug: Lang_6
Failing test: org.apache.commons.lang3.StringEscapeUtilsTest::testEscapeJson
Error: AssertionError: expected:<...> but was:<...>
Stack trace:
  at StringEscapeUtils.escapeJson(StringEscapeUtils.java:248)

Suspicious location: StringEscapeUtils.java lines 245-255
```java
245: public static String escapeJson(String input) {
246:     if (input == null) return null;
247:     StringBuilder sb = new StringBuilder();
248:     for (char c : input.toCharArray()) {
249:         if (c == '"') sb.append("\\\"");
250:         if (c == '\\') sb.append("\\\\");
251:         sb.append(c);
252:     }
253:     return sb.toString();
254: }
```

Step 1 — Root cause: lines 249-251 append the escape sequence AND then unconditionally
  append the original character `c`, so escaped chars appear doubled.
Step 2 — Fix: use `else` so `c` is only appended when no escape was emitted.
Step 3 — Implementation:

FILE: src/main/java/org/apache/commons/lang3/StringEscapeUtils.java
SEARCH:
        if (c == '"') sb.append("\\\"");
        if (c == '\\') sb.append("\\\\");
        sb.append(c);
REPLACE:
        if (c == '"') { sb.append("\\\""); }
        else if (c == '\\') { sb.append("\\\\"); }
        else { sb.append(c); }

---

## Demonstration 2

Bug: Math_5
Failing test: org.apache.commons.math3.complex.ComplexTest::testReciprocalZero
Error: AssertionError: expected NaN but was <Infinity>
Stack trace:
  at Complex.reciprocal(Complex.java:299)

Suspicious location: Complex.java lines 295-305
```java
295: public Complex reciprocal() {
296:     if (isNaN) return NaN;
297:     if (real == 0.0 && imaginary == 0.0) {
298:         return NaN;                     // BUG: should be INF
299:     }
300:     ...
301: }
```

Step 1 — Root cause: reciprocal of zero should be Complex.INF per IEEE semantics,
  but line 298 returns NaN instead.
Step 2 — Fix: return INF when the denominator is zero.
Step 3 — Implementation:

FILE: src/main/java/org/apache/commons/math3/complex/Complex.java
SEARCH:
        if (real == 0.0 && imaginary == 0.0) {
            return NaN;
        }
REPLACE:
        if (real == 0.0 && imaginary == 0.0) {
            return INF;
        }
""".strip()

USER_TEMPLATE = """{demonstrations}

---

## New bug to fix

{fail_context}

## Suspicious location(s)
{location_context}

Step 1 — Root cause:
Step 2 — Fix strategy:
Step 3 — Implementation:

FILE: path/to/File.java
SEARCH: <exact lines>
REPLACE: <corrected lines>
"""


class FewShotCoTPatchGenerator(PatchGenerator):

    def generate_patch(
        self,
        bug_id, workdir, failing_info, trigger_tests,
        localization_hits, attempt_index, out_dir, llm_client,
    ) -> PatchResult:

        prompt = USER_TEMPLATE.format(
            demonstrations=DEMONSTRATIONS,
            fail_context=build_fail_context(bug_id, trigger_tests, failing_info),
            location_context=build_location_context(localization_hits, workdir),
        )
        response = llm_client.chat(
            [{"role": "system", "content": PATCH_SYSTEM},
             {"role": "user",   "content": prompt}],
            purpose="fscot_patch_gen", attempt=attempt_index,
            out_dir=out_dir, max_tokens=1500,
        )
        return PatchResult(
            diff_text=extract_search_replace(response or ""),
            metadata={"strategy": "few_shot_cot", "raw_response": response or ""},
        )
```

---

### 10i. `patch_generators/react.py` — ReAct

**Paper:** Yao et al., ICLR 2023 (arXiv:2210.03629).  
**APR design:** Simulate a Thought → Action → Observation loop where Actions are
file-inspection operations (ReadFile, SearchMethod, AnalyzeDiff). In a full agent
the Observations would come from real file-system calls; here the model simulates
them. When the model emits `Action: GeneratePatch`, the loop terminates and the
patch is extracted.

> **Verified:** in the original paper Observations come from real external tools.
> Here the model simulates them, which is the correct single-call approximation
> when no live tool bridge is wired up.

```
Call 1  →  structured Thought/Action/Observation loop
           model cycles until: Action: GeneratePatch
           then:               Observation: <SEARCH/REPLACE block>
```

```python
# swe_agent/patch_generators/react.py
"""
ReAct patch generator.
Paper: Yao et al., ICLR 2023 (arXiv:2210.03629)
Strategy: one call with a Thought/Action/Observation scaffold.
  The model reasons through file inspection actions before emitting the patch.
  Actions: ReadFile | SearchMethod | AnalyzeDiff | GeneratePatch
  Loop terminates when the model emits: Action: GeneratePatch
Budget: 1 LLM call per attempt.
Note: in production, replace simulated Observations with real file-system calls
      to get the full ReAct agent benefit.
"""
from pathlib import Path
from .base import PatchGenerator, PatchResult
from ._shared import (build_fail_context, build_location_context,
                       extract_search_replace, PATCH_SYSTEM)

SYSTEM = PATCH_SYSTEM + """

You are also a reasoning agent. Solve repair tasks using repeated:
  Thought:     [what you know and what you need to determine next]
  Action:      [one of: ReadFile(<path>) | SearchMethod(<name>) | AnalyzeDiff | GeneratePatch]
  Observation: [result of the action — simulate if no tool is available]

Rules:
- Cycle Thought/Action/Observation until you are confident in the fix.
- When ready, emit:
    Action: GeneratePatch
    Observation:
    FILE: path/to/File.java
    SEARCH: <exact lines>
    REPLACE: <corrected lines>
- Do NOT emit the patch before Action: GeneratePatch."""

USER_TEMPLATE = """## Bug: {bug_id}

{fail_context}

## Suspicious location(s)
{location_context}

Begin your Thought/Action/Observation loop now.
"""


class ReActPatchGenerator(PatchGenerator):

    def generate_patch(
        self,
        bug_id, workdir, failing_info, trigger_tests,
        localization_hits, attempt_index, out_dir, llm_client,
    ) -> PatchResult:

        prompt = USER_TEMPLATE.format(
            bug_id=bug_id,
            fail_context=build_fail_context(bug_id, trigger_tests, failing_info),
            location_context=build_location_context(localization_hits, workdir),
        )
        response = llm_client.chat(
            [{"role": "system", "content": SYSTEM},
             {"role": "user",   "content": prompt}],
            purpose="react_patch_gen", attempt=attempt_index,
            out_dir=out_dir, max_tokens=2000,
        )

        # Extract patch from the Observation block after "Action: GeneratePatch"
        raw = response or ""
        patch_section = raw
        marker = "Action: GeneratePatch"
        if marker in raw:
            patch_section = raw[raw.index(marker):]

        return PatchResult(
            diff_text=extract_search_replace(patch_section),
            metadata={"strategy": "react",
                      "full_trace": raw,
                      "raw_response": raw},
        )
```

---

### 10j. `patch_generators/pot.py` — Program of Thoughts

**Paper:** Chen et al., TMLR 2023 (arXiv:2211.12588). Concurrent work: Gao et al. PAL, ICML 2023.  
**APR design:** Ask the model to write a Python *repair script* that reads the buggy Java
file, applies the fix programmatically, and writes it back. The script is executed in a
subprocess sandbox. This shifts computation (string matching, line replacement) from the
LLM to the Python interpreter — eliminating a whole class of off-by-one and whitespace
errors that plague SEARCH/REPLACE patches.

> **Verified key insight:** PoT disentangles *reasoning* (what to change) from
> *computation* (how to apply the change precisely). The model is a program writer,
> not a calculator. Two-step extraction: generate script → sandbox exec.

```
Call 1  →  generate a Python repair script that edits the Java file in-place
           script assigns result = "patch applied" or result = "error: ..."
Exec   →  subprocess runs script in restricted sandbox, reads result
```

```python
# swe_agent/patch_generators/pot.py
"""
Program of Thoughts patch generator.
Paper: Chen et al., TMLR 2023 (arXiv:2211.12588)
Concurrent: Gao et al. PAL, ICML 2023
Strategy:
  Call 1 — model writes a Python script that edits the buggy Java file.
  Exec   — script runs in a subprocess; git diff captures the resulting patch.
Budget: 1 LLM call + 1 subprocess execution per attempt.
Key insight: separates reasoning (what to change) from computation (how to
apply it precisely) — eliminates whitespace / off-by-one patch failures.
"""
import re, subprocess, textwrap
from pathlib import Path
from .base import PatchGenerator, PatchResult
from ._shared import build_fail_context, build_location_context

SYSTEM = """You are an automated Java bug repair system.
Output ONLY a Python script (no markdown fences, no prose).
The script must:
  1. Read the buggy Java file using its absolute path.
  2. Apply the minimal fix in-place.
  3. Write the corrected file back.
  4. Print exactly one line: RESULT: OK  (or RESULT: ERROR <reason> on failure)
Use only: open, str, re, pathlib.Path — no other imports."""

USER_TEMPLATE = """{fail_context}

## Suspicious location(s)
{location_context}

## Absolute path to the buggy file
{abs_path}

Write a Python script that fixes the bug by editing the file above.
"""


def _run_script_sandbox(script: str, workdir: Path, timeout: int = 30) -> tuple[bool, str]:
    """
    Execute the repair script in a subprocess with a restricted environment.
    Returns (success, stdout).
    """
    # Write script to a temp file inside workdir
    script_path = workdir / "_pot_repair.py"
    script_path.write_text(script)
    try:
        result = subprocess.run(
            ["python3", str(script_path)],
            cwd=str(workdir),
            capture_output=True, text=True,
            timeout=timeout,
        )
        ok = result.returncode == 0 and "RESULT: OK" in result.stdout
        return ok, result.stdout + result.stderr
    except subprocess.TimeoutExpired:
        return False, "TIMEOUT"
    except Exception as e:
        return False, str(e)
    finally:
        script_path.unlink(missing_ok=True)


def _extract_script(text: str) -> str:
    """Pull out the Python script — handles both fenced and bare output."""
    m = re.search(r"```python(.*?)```", text, re.DOTALL)
    if m:
        return m.group(1).strip()
    # bare output: assume entire response is the script
    return text.strip()


class PoTPatchGenerator(PatchGenerator):

    def generate_patch(
        self,
        bug_id, workdir, failing_info, trigger_tests,
        localization_hits, attempt_index, out_dir, llm_client,
    ) -> PatchResult:

        fail_ctx = build_fail_context(bug_id, trigger_tests, failing_info)
        loc_ctx  = build_location_context(localization_hits, workdir)

        # Resolve absolute path of the primary suspicious file
        abs_path = str(workdir)
        if localization_hits:
            candidate = Path(workdir) / localization_hits[0].filepath
            if candidate.exists():
                abs_path = str(candidate)

        # ── Call 1: generate repair script ────────────────────────────────
        response = llm_client.chat(
            [{"role": "system", "content": SYSTEM},
             {"role": "user",   "content": USER_TEMPLATE.format(
                 fail_context=fail_ctx,
                 location_context=loc_ctx,
                 abs_path=abs_path,
             )}],
            purpose="pot_script_gen", attempt=attempt_index,
            out_dir=out_dir, max_tokens=1200,
        )

        script = _extract_script(response or "")
        if not script:
            return PatchResult(diff_text="",
                               metadata={"strategy": "pot", "stage": "no_script"})

        # Save script for debugging
        (out_dir / f"attempt_{attempt_index:03d}_pot_script.py").write_text(script)

        # ── Exec: run script, then capture git diff as the patch ──────────
        ok, exec_log = _run_script_sandbox(script, workdir)
        (out_dir / f"attempt_{attempt_index:03d}_pot_exec.log").write_text(exec_log)

        if not ok:
            return PatchResult(diff_text="",
                               metadata={"strategy": "pot", "stage": "exec_failed",
                                         "exec_log": exec_log})

        diff_result = subprocess.run(
            ["git", "diff", "--no-color"],
            cwd=str(workdir), capture_output=True, text=True,
        )
        diff_text = diff_result.stdout.strip()

        return PatchResult(
            diff_text=diff_text,
            metadata={"strategy": "pot", "script": script,
                      "exec_log": exec_log, "exec_ok": ok},
        )
```

---

### 10k. `patch_generators/function_calling.py` — Function Calling

**Origin:** OpenAI API feature, June 2023. Not a prompting paper — requires model fine-tuning.  
**Related:** Schick et al. *Toolformer* NeurIPS 2023; Patil et al. *Gorilla* 2023.

**APR design:** Define APR-specific tools (`read_file_lines`, `search_method`, `apply_patch`)
as JSON schemas. The model decides which tool to call and with what arguments. The
application executes the tool and returns the result. This gives the model grounded,
accurate file content rather than having it hallucinate line numbers.

> **Verified:** Function calling is not a prompting technique — it involves model fine-tuning
> for structured JSON output. It is included here as the **tool-use agent baseline**.
> The `LLMClient.chat_with_tools()` extension handles the two-call loop.

```
Call 1  →  model selects tool (e.g. read_file_lines) + arguments
App     →  executes tool, appends tool result to messages
Call 2  →  model sees real file content → emits apply_patch tool call
App     →  executes apply_patch → git diff → PatchResult
```

```python
# swe_agent/patch_generators/function_calling.py
"""
Function Calling patch generator — structured tool-use baseline.
Origin: OpenAI API feature, June 2023 (not a prompting paper).
Strategy:
  The model selects from APR-specific tools (read_file, search_method,
  apply_patch) rather than generating free-form text patches.
  Grounded file reads eliminate hallucinated line numbers.
Budget: 2-4 LLM calls per attempt (one per tool invocation).
Note: requires a model fine-tuned for function/tool calling output.
      Falls back to standard prompting if tool_calls is absent.
"""
import json
from pathlib import Path
from openai import OpenAI
from .base import PatchGenerator, PatchResult
from ._shared import build_fail_context, build_location_context, PATCH_SYSTEM
from ...config import OPENAI_API_KEY, OPENAI_API_BASE_URL, GPT_MODEL

# ── Tool definitions ──────────────────────────────────────────────────────────

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_file_lines",
            "description": "Read specific line range from a Java source file in the working directory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filepath": {"type": "string",
                                 "description": "Relative path from workdir, e.g. src/main/java/Foo.java"},
                    "start_line": {"type": "integer", "description": "First line to read (1-indexed)"},
                    "end_line":   {"type": "integer", "description": "Last line to read (inclusive)"},
                },
                "required": ["filepath", "start_line", "end_line"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_method",
            "description": "Find all occurrences of a method name in the working directory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "method_name": {"type": "string", "description": "Method name to search for"},
                },
                "required": ["method_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "apply_patch",
            "description": "Apply a SEARCH/REPLACE patch to fix the bug.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filepath":    {"type": "string", "description": "Relative path of file to patch"},
                    "search_text": {"type": "string",
                                    "description": "Exact lines to find (verbatim, including whitespace)"},
                    "replace_text": {"type": "string", "description": "Replacement lines"},
                },
                "required": ["filepath", "search_text", "replace_text"],
            },
        },
    },
]


# ── Tool implementations ──────────────────────────────────────────────────────

def _read_file_lines(workdir: Path, filepath: str,
                     start_line: int, end_line: int) -> str:
    full = workdir / filepath
    if not full.exists():
        return f"ERROR: {filepath} not found"
    lines = full.read_text().splitlines()
    s = max(0, start_line - 1)
    e = min(len(lines), end_line)
    return "\n".join(f"{s+i+1}: {l}" for i, l in enumerate(lines[s:e]))


def _search_method(workdir: Path, method_name: str) -> str:
    import subprocess
    r = subprocess.run(
        ["grep", "-rn", f"\\b{method_name}\\b", "--include=*.java", "."],
        cwd=str(workdir), capture_output=True, text=True,
    )
    return r.stdout[:2000] if r.stdout else "No matches found."


def _apply_patch(workdir: Path, filepath: str,
                 search_text: str, replace_text: str) -> str:
    full = workdir / filepath
    if not full.exists():
        return f"ERROR: {filepath} not found"
    content = full.read_text()
    if search_text not in content:
        return "ERROR: SEARCH text not found in file (check whitespace/indentation)"
    full.write_text(content.replace(search_text, replace_text, 1))
    return "OK"


def _dispatch_tool(tool_name: str, args: dict, workdir: Path) -> str:
    if tool_name == "read_file_lines":
        return _read_file_lines(workdir, **args)
    elif tool_name == "search_method":
        return _search_method(workdir, **args)
    elif tool_name == "apply_patch":
        return _apply_patch(workdir, **args)
    return f"ERROR: unknown tool {tool_name}"


class FunctionCallingPatchGenerator(PatchGenerator):

    def __init__(self, max_tool_rounds: int = 4):
        self.max_tool_rounds = max_tool_rounds
        # Direct OpenAI client needed for tools parameter
        # (LLMClient.chat() does not expose tools — extend if needed)
        self._client = OpenAI(api_key=OPENAI_API_KEY, base_url=OPENAI_API_BASE_URL)

    def generate_patch(
        self,
        bug_id, workdir, failing_info, trigger_tests,
        localization_hits, attempt_index, out_dir, llm_client,
    ) -> PatchResult:

        fail_ctx = build_fail_context(bug_id, trigger_tests, failing_info)
        loc_ctx  = build_location_context(localization_hits, workdir)

        messages = [
            {"role": "system", "content": PATCH_SYSTEM},
            {"role": "user",   "content": (
                f"## Bug: {bug_id}\n\n{fail_ctx}\n\n"
                f"## Suspicious location(s)\n{loc_ctx}\n\n"
                "Use the available tools to inspect the code and then call "
                "apply_patch to fix the bug."
            )},
        ]

        patch_applied = False
        applied_filepath = applied_search = applied_replace = ""

        for _round in range(self.max_tool_rounds):
            llm_client._calls += 1      # count against bug budget manually
            response = self._client.chat.completions.create(
                model=GPT_MODEL,
                messages=messages,
                tools=TOOLS,
                tool_choice="auto",
                max_tokens=1500,
            )

            msg = response.choices[0].message
            messages.append(msg.model_dump(exclude_unset=True))

            tool_calls = getattr(msg, "tool_calls", None) or []
            if not tool_calls:
                break       # model gave a text response — no more tool calls

            for tc in tool_calls:
                fn_name = tc.function.name
                fn_args = json.loads(tc.function.arguments)

                tool_result = _dispatch_tool(fn_name, fn_args, Path(workdir))

                messages.append({
                    "role":         "tool",
                    "tool_call_id": tc.id,
                    "content":      tool_result,
                })

                if fn_name == "apply_patch" and tool_result == "OK":
                    patch_applied     = True
                    applied_filepath  = fn_args["filepath"]
                    applied_search    = fn_args["search_text"]
                    applied_replace   = fn_args["replace_text"]

            if patch_applied:
                break

        if not patch_applied:
            return PatchResult(diff_text="",
                               metadata={"strategy": "function_calling",
                                         "stage": "no_apply_patch_call"})

        # Capture git diff as the canonical patch
        import subprocess
        diff_result = subprocess.run(
            ["git", "diff", "--no-color"],
            cwd=str(workdir), capture_output=True, text=True,
        )

        # Reconstruct a SEARCH/REPLACE record for metadata
        sr_record = (
            f"FILE: {applied_filepath}\n"
            f"SEARCH: {applied_search}\n"
            f"REPLACE: {applied_replace}"
        )
        return PatchResult(
            diff_text=diff_result.stdout.strip(),
            metadata={"strategy":       "function_calling",
                      "tool_rounds":    _round + 1,
                      "search_replace": sr_record},
        )
```

---

## 11. `runner.py` — Single-Bug Orchestration [CODE]

```python
# swe_agent/runner.py
"""
CLI: python -m swe_agent.runner --project Lang --bug 1 --baseline agentless --out outputs/Lang-1
"""
import argparse, json, time
from pathlib import Path
from . import config, defects4j as d4j, reason
from .llm_client import LLMClient, BudgetExceededError
from .budget import BudgetManager
from .trace import TraceWriter
from .localize import localize
from .apply_patch import apply_patch, init_git_baseline, rollback
from .tests_runner import run_functionality_tests, run_regression_tests, get_trigger_tests
from .patch_generators.agentless         import AgentlessPatchGenerator
from .patch_generators.swe_agent         import SWEAgentPatchGenerator
from .patch_generators.openhands         import OpenHandsPatchGenerator
from .patch_generators.openclaw          import OpenClawPatchGenerator
from .patch_generators.claude_code       import ClaudeCodePatchGenerator
# Prompting-strategy baselines (all adapted from prompting literature)
from .patch_generators.standard          import StandardPatchGenerator
from .patch_generators.zero_shot_cot     import ZeroShotCoTPatchGenerator
from .patch_generators.few_shot_cot      import FewShotCoTPatchGenerator
from .patch_generators.react             import ReActPatchGenerator
from .patch_generators.reflexion         import ReflexionPatchGenerator
from .patch_generators.self_consistency  import SelfConsistencyPatchGenerator
from .patch_generators.tot               import ToTPatchGenerator
from .patch_generators.got               import GoTPatchGenerator
from .patch_generators.pot               import PoTPatchGenerator
from .patch_generators.function_calling  import FunctionCallingPatchGenerator

GENERATORS = {
    # ── Original APR agent baselines ──────────────────────────────────────
    "agentless":         AgentlessPatchGenerator,
    "swe_agent":         SWEAgentPatchGenerator,
    "openhands":         OpenHandsPatchGenerator,
    "openclaw":          OpenClawPatchGenerator,
    "claude_code":       ClaudeCodePatchGenerator,
    # ── Prompting-strategy baselines ──────────────────────────────────────
    "standard":          StandardPatchGenerator,
    "zero_shot_cot":     ZeroShotCoTPatchGenerator,
    "few_shot_cot":      FewShotCoTPatchGenerator,
    "react":             ReActPatchGenerator,
    "reflexion":         ReflexionPatchGenerator,
    "self_consistency":  SelfConsistencyPatchGenerator,
    "tot":               ToTPatchGenerator,
    "got":               GoTPatchGenerator,
    "pot":               PoTPatchGenerator,
    "function_calling":  FunctionCallingPatchGenerator,
}


def run_bug(project: str, bug_id: str, baseline: str, out_dir: Path) -> dict:
    bug_name = f"{project}_{bug_id}"
    workdir  = Path(config.REPOS_DIR) / f"{project}-{bug_id}"
    out_dir  = out_dir / baseline
    out_dir.mkdir(parents=True, exist_ok=True)

    trace   = TraceWriter(out_dir / "trace.jsonl")
    llm     = LLMClient(baseline, bug_name)
    budget  = BudgetManager()
    gen     = GENERATORS[baseline]()

    t_start = time.monotonic()
    result  = {
        "bug": bug_name, "baseline": baseline,
        "status": "unrepaired", "attempts_used": 0,
        "attempt_summaries": [],
    }

    # ── 1. Checkout ──────────────────────────────────────────────────────────
    try:
        d4j.checkout(project, bug_id, "b", workdir,
                     log_path=out_dir / "logs" / "checkout.log")
    except Exception as e:
        result["status"] = "error"; result["notes"] = str(e)
        _write_result(result, out_dir); return result

    # ── 2. Pre-patch baseline ────────────────────────────────────────────────
    n_before, failing_before, _ = d4j.test(
        workdir, project, log_path=out_dir / "logs" / "test_before.log")
    if n_before == 0:
        result["notes"] = "already passing"; _write_result(result, out_dir); return result

    result["failing_count_before"] = n_before
    result["failing_tests_before"] = failing_before

    trigger_tests = get_trigger_tests(workdir, project)
    init_git_baseline(workdir)
    loc_hits  = localize(workdir, project, (out_dir / "logs" / "test_before.log").read_text())
    fail_info = _load_fail_info(bug_name)

    # ── 3. Attempt loop ───────────────────────────────────────────────────────
    v_time = {"apply_patch": 0.0, "compile": 0.0, "func_test": 0.0, "reg_test": 0.0}

    for attempt in range(1, config.MAX_ATTEMPTS_PER_BUG + 1):
        result["attempts_used"] = attempt
        attempt_status = {}

        # a) Generate patch
        try:
            patch_result = gen.generate_patch(
                bug_name, workdir, fail_info, trigger_tests, loc_hits,
                attempt, out_dir, llm)
        except BudgetExceededError as e:
            trace.log({"bug": bug_name, "baseline": baseline, "attempt": attempt,
                       "phase": "patch_gen", "status": "FAIL",
                       "reason_code": reason.TIMEOUT, "reason": str(e)})
            break

        _save_attempt(patch_result, attempt, out_dir)
        if not patch_result.diff_text:
            attempt_status = {"attempt": attempt, "status": "PATCH_GENERATED", "reason_code": "EMPTY_DIFF"}
            result["attempt_summaries"].append(attempt_status)
            continue

        # b) Budget / safety check
        try:
            budget.check_patch(patch_result.diff_text)
        except Exception as e:
            rc = reason.PATCH_SCOPE_VIOLATION
            trace.log(_event(bug_name, baseline, attempt, "apply_patch", "FAIL", rc, str(e)))
            attempt_status = {"attempt": attempt, "status": reason.PATCH_APPLY_FAILED, "reason_code": rc}
            result["attempt_summaries"].append(attempt_status); continue

        # c) Apply patch
        t0 = time.monotonic()
        ok, err = apply_patch(patch_result.diff_text, workdir)
        v_time["apply_patch"] += time.monotonic() - t0
        if not ok:
            rc = reason.PATCH_APPLY_HUNK_FAILED
            trace.log(_event(bug_name, baseline, attempt, "apply_patch", "FAIL", rc, err))
            attempt_status = {"attempt": attempt, "status": reason.PATCH_APPLY_FAILED, "reason_code": rc}
            result["attempt_summaries"].append(attempt_status)
            rollback(workdir); continue

        # d) Compile
        t0 = time.monotonic()
        ok, build_log = d4j.compile(workdir, project,
            log_path=out_dir / "logs" / f"attempt_{attempt:03d}_compile.log")
        v_time["compile"] += time.monotonic() - t0
        if not ok:
            rc = reason.parse_build_reason(build_log)
            trace.log(_event(bug_name, baseline, attempt, "compile", "FAIL", rc))
            attempt_status = {"attempt": attempt, "status": reason.BUILD_FAILED, "reason_code": rc}
            result["attempt_summaries"].append(attempt_status)
            rollback(workdir); continue

        # e) Functionality gate
        t0 = time.monotonic()
        n_func, still_failing, _ = run_functionality_tests(
            workdir, trigger_tests, project,
            log_path=out_dir / "logs" / f"attempt_{attempt:03d}_func_test.log")
        v_time["func_test"] += time.monotonic() - t0
        if n_func > 0:
            rc = reason.TRIGGER_TEST_STILL_FAILING
            trace.log(_event(bug_name, baseline, attempt, "func_test", "FAIL", rc,
                             metrics={"failing_count": n_func, "failing_tests": still_failing}))
            attempt_status = {"attempt": attempt, "status": reason.FUNCTIONALITY_FAILED, "reason_code": rc}
            result["attempt_summaries"].append(attempt_status)
            rollback(workdir); continue

        # f) Regression gate
        t0 = time.monotonic()
        n_reg, reg_failing, _ = run_regression_tests(
            workdir, project,
            log_path=out_dir / "logs" / f"attempt_{attempt:03d}_reg_test.log")
        v_time["reg_test"] += time.monotonic() - t0
        new_failures = set(reg_failing) - set(failing_before)
        if new_failures:
            rc = reason.NEW_FAILURES_INTRODUCED
            trace.log(_event(bug_name, baseline, attempt, "reg_test", "FAIL", rc,
                             metrics={"failing_count": n_reg, "failing_tests": reg_failing}))
            attempt_status = {"attempt": attempt, "status": reason.REGRESSION_FAILED, "reason_code": rc}
            result["attempt_summaries"].append(attempt_status)
            rollback(workdir); continue

        # g) REPAIRED ✓
        (out_dir / "patch.diff").write_text(patch_result.diff_text)
        trace.log(_event(bug_name, baseline, attempt, "reg_test", "OK", reason.REPAIRED))
        result["status"] = "repaired"
        result["failing_count_after"] = 0
        attempt_status = {"attempt": attempt, "status": reason.REPAIRED}
        result["attempt_summaries"].append(attempt_status)
        break

    # ── 4. Finalize result.json ───────────────────────────────────────────────
    v_time["total"] = sum(v_time.values())
    result["time_sec"]              = round(time.monotonic() - t_start, 1)
    result["llm"]                   = llm.summary()
    result["verification_time_sec"] = {k: round(v, 2) for k, v in v_time.items()}
    result["constraints"] = {
        "max_attempts":          config.MAX_ATTEMPTS_PER_BUG,
        "max_llm_calls_per_bug": config.MAX_LLM_CALLS_PER_BUG,
        "max_tokens_per_bug":    config.MAX_TOKENS_PER_BUG,
        "max_patch_lines":       config.MAX_PATCH_LINES,
        "max_files_changed":     config.MAX_FILES_CHANGED,
    }
    result["artifacts"] = {"trace": "trace.jsonl", "llm_calls": "llm_calls.jsonl"}
    if result["status"] == "repaired":
        result["artifacts"]["final_patch"] = "patch.diff"

    _write_result(result, out_dir)
    return result


# ── Helpers ──────────────────────────────────────────────────────────────────

def _event(bug, baseline, attempt, phase, status, reason_code, reason_msg="", metrics=None):
    e = {"bug": bug, "baseline": baseline, "attempt": attempt,
         "phase": phase, "status": status,
         "reason_code": reason_code, "reason": reason_msg}
    if metrics: e["metrics"] = metrics
    return e

def _save_attempt(patch_result, attempt: int, out_dir: Path):
    att_dir = out_dir / "attempts"
    att_dir.mkdir(exist_ok=True)
    (att_dir / f"{attempt:03d}.patch.diff").write_text(patch_result.diff_text)
    (att_dir / f"{attempt:03d}.meta.json").write_text(
        json.dumps(patch_result.metadata, indent=2))

def _write_result(result: dict, out_dir: Path):
    (out_dir / "result.json").write_text(json.dumps(result, indent=2))

def _load_fail_info(bug_name: str) -> dict:
    from .tasks.base import BaseTask
    return BaseTask()._load_fail_info(bug_name)


# ── CLI ──────────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--project",  required=True)
    p.add_argument("--bug",      required=True)
    p.add_argument("--baseline", default="agentless",
                   choices=list(GENERATORS.keys()))
    p.add_argument("--out",      default="outputs")
    args = p.parse_args()
    result = run_bug(args.project, args.bug, args.baseline,
                     Path(args.out) / f"{args.project}-{args.bug}")
    print(json.dumps(result, indent=2))

if __name__ == "__main__":
    main()
```

---

## 12. `eval.py` — Batch Evaluation + Report [CODE]

```python
# swe_agent/eval.py
"""
CLI:
  python -m swe_agent.eval --bugs benchmarks/defects4j_small.txt \
    --baseline agentless swe_agent openclaw --out outputs
"""
import argparse, json, csv
from pathlib import Path
from datetime import date
from .runner import run_bug, GENERATORS


def load_bug_list(path: str) -> list[tuple[str, str]]:
    bugs = []
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"): continue
        # accept both "Lang_1" and "Lang-1"
        sep = "_" if "_" in line else "-"
        project, bug_id = line.split(sep, 1)
        bugs.append((project, bug_id))
    return bugs


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--bugs",     required=True)
    p.add_argument("--baseline", nargs="+", default=["agentless"],
                   choices=list(GENERATORS.keys()))
    p.add_argument("--out",      default="outputs")
    args = p.parse_args()

    bugs    = load_bug_list(args.bugs)
    out_dir = Path(args.out)
    all_results: list[dict] = []

    for project, bug_id in bugs:
        for baseline in args.baseline:
            bug_out = out_dir / f"{project}-{bug_id}"
            print(f"  [{baseline}] {project}-{bug_id} ...", end=" ", flush=True)
            try:
                result = run_bug(project, bug_id, baseline, bug_out)
            except Exception as e:
                result = {"bug": f"{project}_{bug_id}", "baseline": baseline,
                          "status": "error", "notes": str(e)}
                (bug_out / baseline).mkdir(parents=True, exist_ok=True)
                (bug_out / baseline / "result.json").write_text(json.dumps(result, indent=2))
            print(result["status"])
            all_results.append(result)

    _write_summary(all_results, out_dir)
    _write_report(all_results, out_dir)
    print(f"\nReport: {out_dir}/report.md")


def _aggregate(results: list[dict], baseline: str | None = None) -> dict:
    subset = [r for r in results if (baseline is None or r.get("baseline") == baseline)]
    total    = len(subset)
    repaired = sum(1 for r in subset if r.get("status") == "repaired")
    func_fixed = sum(1 for r in subset
                     if r.get("status") == "repaired" or
                     any(a.get("status") in ("FUNCTIONALITY_FAILED",) is False
                         and a.get("status") not in ("BUILD_FAILED","PATCH_APPLY_FAILED")
                         for a in r.get("attempt_summaries", [])))

    llm_calls_list  = [r.get("llm", {}).get("calls", 0) for r in subset]
    tokens_list     = [r.get("llm", {}).get("total_tokens", 0) for r in subset]
    verify_list     = [r.get("verification_time_sec", {}).get("total", 0) for r in subset]

    def median(lst):
        s = sorted(lst)
        n = len(s)
        return round(s[n // 2], 1) if n else 0

    return {
        "baseline":   baseline or "all",
        "total":      total,
        "repaired":   repaired,
        "repair_rate": f"{repaired/total*100:.1f}%" if total else "0%",
        "llm_calls_median":   median(llm_calls_list),
        "tokens_median":      median(tokens_list),
        "verify_time_median": median(verify_list),
    }


def _write_summary(results: list[dict], out_dir: Path):
    out_dir.mkdir(parents=True, exist_ok=True)

    # JSON
    baselines = sorted({r.get("baseline","") for r in results})
    summary = {b: _aggregate(results, b) for b in baselines}
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2))

    # CSV
    with open(out_dir / "summary.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["bug","baseline","status","attempts_used",
                                          "llm_calls","total_tokens","verify_time_sec"])
        w.writeheader()
        for r in results:
            w.writerow({
                "bug":          r.get("bug",""),
                "baseline":     r.get("baseline",""),
                "status":       r.get("status",""),
                "attempts_used": r.get("attempts_used",0),
                "llm_calls":    r.get("llm",{}).get("calls",0),
                "total_tokens": r.get("llm",{}).get("total_tokens",0),
                "verify_time_sec": r.get("verification_time_sec",{}).get("total",0),
            })


def _write_report(results: list[dict], out_dir: Path):
    baselines  = sorted({r.get("baseline","") for r in results})
    aggs       = {b: _aggregate(results, b) for b in baselines}

    lines = [
        "# Defects4J Automatic Repair Report",
        f"\nDate: {date.today().isoformat()}",
        "",
        "## Summary",
        "",
        "| Baseline | Attempted | Repaired | Repair rate | LLM calls/bug (med) | Tokens/bug (med) | Verify time/bug (med) |",
        "|:---------|----------:|---------:|------------:|--------------------:|-----------------:|----------------------:|",
    ]
    for b, ag in aggs.items():
        lines.append(
            f"| {b} | {ag['total']} | {ag['repaired']} | {ag['repair_rate']} "
            f"| {ag['llm_calls_median']} | {ag['tokens_median']} | {ag['verify_time_median']}s |"
        )

    for baseline in baselines:
        subset   = [r for r in results if r.get("baseline") == baseline]
        repaired = [r for r in subset if r.get("status") == "repaired"]
        failed   = [r for r in subset if r.get("status") != "repaired"]

        lines += ["", f"## Repaired — {baseline}"]
        for r in repaired:
            att = r.get("attempts_used", "?")
            t   = r.get("time_sec", "?")
            lines.append(f"- {r['bug']} (attempt {att}, {t}s)")

        lines += [f"## Unrepaired / Errors — {baseline}"]
        for r in failed:
            last = (r.get("attempt_summaries") or [{}])[-1]
            rc   = last.get("reason_code", r.get("notes",""))
            lines.append(f"- {r['bug']} — {rc}")

    (out_dir / "report.md").write_text("\n".join(lines))


if __name__ == "__main__":
    main()
```

---

## 13. Implementation Order for Claude Code

Implement modules in this exact sequence to avoid import errors:

| Step | File | Key dependency |
|------|------|---------------|
| 1 | `config.py` | none |
| 2 | `reason.py` | none |
| 3 | `trace.py` | none |
| 4 | `budget.py` | config |
| 5 | `llm_client.py` | config, trace, budget |
| 6 | `tasks/base.py` | none |
| 7 | `tasks/fault_localization.py` | tasks/base |
| 8 | `tasks/automated_program_repair.py` | tasks/fault_localization |
| 9 | `defects4j.py` + `defects4j.sh` | config |
| 10 | `localize.py` | defects4j, tasks/base |
| 11 | `apply_patch.py` | budget |
| 12 | `tests_runner.py` | defects4j |
| 13 | `patch_generators/base.py` | none |
| 14 | `patch_generators/_shared.py` | base — shared helpers for ALL prompting baselines |
| 15 | `patch_generators/agentless.py` | base, llm_client |
| 16 | `patch_generators/swe_agent.py` | base, llm_client |
| 17 | `patch_generators/openhands.py` | base, llm_client |
| 18 | `patch_generators/openclaw.py` | base, llm_client |
| 19 | `patch_generators/claude_code.py` | base, llm_client |
| 20 | `patch_generators/standard.py` | base, _shared, llm_client |
| 21 | `patch_generators/zero_shot_cot.py` | base, _shared, llm_client |
| 22 | `patch_generators/few_shot_cot.py` | base, _shared, llm_client |
| 23 | `patch_generators/react.py` | base, _shared, llm_client |
| 24 | `patch_generators/reflexion.py` | base, _shared, llm_client |
| 25 | `patch_generators/self_consistency.py` | base, _shared, llm_client, config |
| 26 | `patch_generators/tot.py` | base, _shared, llm_client |
| 27 | `patch_generators/got.py` | base, _shared, llm_client |
| 28 | `patch_generators/pot.py` | base, _shared, llm_client |
| 29 | `patch_generators/function_calling.py` | base, _shared, config (OpenAI direct) |
| 30 | `runner.py` | all above |
| 31 | `eval.py` | runner |

---

## 14. CLI Quick Reference

```bash
# Single bug, single baseline
python -m swe_agent.runner --project Lang --bug 1 --baseline standard --out outputs

# Single bug, all agent baselines
for bl in agentless swe_agent openhands openclaw claude_code; do
  python -m swe_agent.runner --project Lang --bug 1 --baseline $bl --out outputs
done

# Single bug, all prompting-strategy baselines
for bl in standard zero_shot_cot few_shot_cot react reflexion \
           self_consistency tot got pot function_calling; do
  python -m swe_agent.runner --project Lang --bug 1 --baseline $bl --out outputs
done

# Batch run, all 15 baselines
python -m swe_agent.eval \
  --bugs benchmarks/defects4j_small.txt \
  --baseline agentless swe_agent openhands openclaw claude_code \
             standard zero_shot_cot few_shot_cot react reflexion \
             self_consistency tot got pot function_calling \
  --out outputs
```

---

## 15. Key Invariants — Never Violate

1. **One LLM client.** No baseline imports `openai` directly — except `function_calling.py` which needs the `tools=` parameter not yet exposed by `LLMClient`. Extend `LLMClient.chat_with_tools()` if you want to unify this.
2. **Budget before every call and every patch apply.**
3. **Rollback after every failed attempt** — workspace clean before next attempt.
4. **Pre-patch run mandatory** — `test_before.log` must exist for every bug.
5. **Functionality gate before regression gate** — never run full suite if trigger tests still fail.
6. **All timing via `time.monotonic()`.**
7. **`result.json` written even on error.**
8. **Problem folder (`data/defects4j/`) is read-only** — never written by any baseline.
9. **`failing_tests` file is the ground truth** — parsed identically by all baselines via `_load_fail_info`.
10. **Bug name convention:** `Project_ID` in filesystem, `Project-ID` in D4J CLI — convert with `replace("_","-",1)`.
11. **Stateful baselines (`reflexion`) are instantiated once per bug** — `runner.py` calls `GENERATORS[baseline]()` fresh per bug, which is already correct.
12. **`self_consistency` call count** — `n_samples` is capped at `MAX_LLM_CALLS_PER_ATTEMPT - 1` to leave room for the judge call.
13. **GoT synthesis must include the graph summary** — passing only `fail_ctx` to the synthesis call discards all graph reasoning. Always pass `g.summary()`.
14. **`_shared.py` is the single source of prompt helpers** — do not duplicate `PATCH_SYSTEM`, `extract_search_replace`, or context builders in individual baseline files.
15. **`pot.py` exec sandbox** — the repair script runs via `subprocess`, not bare `exec()`. Verify the script path is inside `workdir` before running.
16. **`zero_shot_cot.py` is two calls** — Stage 1 elicits reasoning, Stage 2 extracts the patch. Do not collapse into one call or the Kojima et al. two-stage design is lost.
17. **`few_shot_cot.py` demonstrations** — the two example bugs in `DEMONSTRATIONS` are illustrative placeholders. Replace with real Defects4J worked examples from your benchmark for best performance.




## 16. Some references for how to fix and call the defects4j 
```
ls 
/home/taicen/wangjian/defects4c_dirs/agent_apr/Agentless
/home/taicen/wangjian/defects4c_dirs/agent_apr/RepairAgent

/home/taicen/wangjian/defects4c_dirs/agent_apr/SWE-agent
/home/taicen/wangjian/defects4c_dirs/agent_apr/OpenHands


uv pip list
openhands                                1.13.0
sweagent                  1.1.0       /home/taicen/wangjian/defects4c_dirs/agent_apr/SWE-agent
```

** How to use defects4j  **

```
(env_defects4j) (base) taicen@GPU-23:~/wangjian/defects4c_dirs/agent_apr/defects4j$ docker-compose exec defects4j defects4j info -p Chart 
Summary of configuration for Project: Chart
--------------------------------------------------------------------------------
    Script dir: /defects4j/framework
      Base dir: /defects4j
    Major root: /defects4j/major
      Repo dir: /defects4j/project_repos
--------------------------------------------------------------------------------
    Project ID: Chart
       Program: jfreechart
    Build file: /defects4j/framework/projects/Chart/Chart.build.xml
--------------------------------------------------------------------------------
           Vcs: Vcs::Svn
    Repository: file:///defects4j/project_repos/jfreechart/trunk
     Commit db: /defects4j/framework/projects/Chart/active-bugs.csv
Number of bugs: 26
--------------------------------------------------------------------------------
(env_defects4j) (base) taicen@GPU-23:~/wangjian/defects4c_dirs/agent_apr/defects4j$ docker-compose exec defects4j defects4j checkout -p Math -v 1b -w /workspace/math_1

Checking out 86545dab to /workspace/math_1................................. OK
Init local repository...................................................... OK
Tag post-fix revision...................................................... OK
Run post-checkout hook..................................................... OK
Excluding broken/flaky tests............................................... OK
Excluding broken/flaky tests............................................... OK
Excluding broken/flaky tests............................................... OK
Initialize fixed program version........................................... OK
Apply patch................................................................ OK
Initialize buggy program version........................................... OK
Diff 86545dab:d7fd760e..................................................... OK
Apply patch................................................................ OK
Tag pre-fix revision....................................................... OK
Check out program version: Math-1b......................................... OK
(env_defects4j) (base) taicen@GPU-23:~/wangjian/defects4c_dirs/agent_apr/defects4j$ docker-compose exec -w /workspace/math_1 defects4j defects4j compile
Running ant (compile)...................................................... OK
Running ant (compile.tests)................................................ OK
(env_defects4j) (base) taicen@GPU-23:~/wangjian/defects4c_dirs/agent_apr/defects4j$ docker-compose exec -w /workspace/math_1 defects4j defects4j test
Running ant (compile.tests)................................................ OK
Running ant (run.dev.tests)................................................ 






OK
Failing tests: 2
  - org.apache.commons.math3.fraction.BigFractionTest::testDigitLimitConstructor
  - org.apache.commons.math3.fraction.FractionTest::testDigitLimitConstructor
(env_defects4j) (base) taicen@GPU-23:~/wangjian/defects4c_dirs/agent_apr/defects4j$ alias d4j='docker-compose -f ~/wangjian/defects4c_dirs/agent_apr/defects4j/docker-compose.yml exec -w /workspace defects4j defects4j'
(env_defects4j) (base) taicen@GPU-23:~/wangjian/defects4c_dirs/agent_apr/defects4j$ 




(env_defects4j) (base) taicen@GPU-23:~/wangjian/defects4c_dirs/agent_apr$ docker-compose exec defects4j defects4j info -p Chart 
(env_defects4j) (base) taicen@GPU-23:~/wangjian/defects4c_dirs/agent_apr$ d4j info -p Chart 
Summary of configuration for Project: Chart
--------------------------------------------------------------------------------
    Script dir: /defects4j/framework
      Base dir: /defects4j
    Major root: /defects4j/major
      Repo dir: /defects4j/project_repos
--------------------------------------------------------------------------------
    Project ID: Chart
       Program: jfreechart
    Build file: /defects4j/framework/projects/Chart/Chart.build.xml
--------------------------------------------------------------------------------
           Vcs: Vcs::Svn
    Repository: file:///defects4j/project_repos/jfreechart/trunk
     Commit db: /defects4j/framework/projects/Chart/active-bugs.csv
Number of bugs: 26
--------------------------------------------------------------------------------
(env_defects4j) (base) taicen@GPU-23:~/wangjian/defects4c_dirs/agent_apr$ 

```





## 10. More Baseline Implementations

These five baselines adapt the prompting strategies from the academic literature
(`Chain-of-Thought`, `Reflexion`, `Tree of Thoughts`, `Self-Consistency`, `Graph of Thoughts`)
into `PatchGenerator` subclasses that plug directly into `runner.py` and `eval.py`.

Every class follows the same `generate_patch(...)` contract defined in `patch_generators/base.py`.
All LLM calls go through the shared `LLMClient` — no direct API imports.

---

### Shared prompt helpers (put in `patch_generators/_shared.py`)

```python
# swe_agent/patch_generators/_shared.py
"""
Common prompt building blocks reused across prompting-strategy baselines.
"""
import re


# ── Context builders ──────────────────────────────────────────────────────────

def build_fail_context(bug_id: str, trigger_tests: list[str],
                       failing_info: dict, max_chars: int = 3000) -> str:
    """Format failing test info into a compact context block."""
    traces = "\n\n".join(
        (fi["error_message"] + "\n" + fi["stack_trace"])[:1000]
        for fi in failing_info.values()
    )
    clean_tests = [t for t in trigger_tests if "::" in t or "." in t]
    return (
        f"Bug: {bug_id}\n"
        f"Failing tests: {', '.join(clean_tests[:3])}\n\n"
        f"Error output:\n{traces[:max_chars]}"
    )


def build_location_context(localization_hits: list,
                            workdir, max_hits: int = 3) -> str:
    """Read source snippets for the top-N localization hits."""
    from pathlib import Path
    blocks = []
    for h in localization_hits[:max_hits]:
        fp = Path(workdir) / h.filepath
        if fp.exists():
            try:
                lines = fp.read_text().splitlines()
                s = max(0, h.start_line - 1)
                e = min(len(lines), h.end_line + 1)
                numbered = "\n".join(
                    f"{h.start_line + i}: {l}"
                    for i, l in enumerate(lines[s:e])
                )
                blocks.append(
                    f"### {h.filepath} lines {h.start_line}-{h.end_line}\n"
                    f"```java\n{numbered}\n```"
                )
            except Exception:
                blocks.append(f"### {h.filepath} (unreadable)")
        else:
            blocks.append(f"### {h.filepath} (not found)")
    return "\n\n".join(blocks)


# ── Output parsers ────────────────────────────────────────────────────────────

SEARCH_REPLACE_RE = re.compile(
    r'FILE:\s*(\S+)\s*SEARCH:\s*(.*?)\s*REPLACE:\s*(.*?)(?=\nFILE:|\Z)',
    re.DOTALL,
)

def extract_search_replace(text: str) -> str:
    """Return the first valid SEARCH/REPLACE block, or '' if none found."""
    m = SEARCH_REPLACE_RE.search(text)
    if not m:
        return ""
    return f"FILE: {m.group(1)}\nSEARCH: {m.group(2).strip()}\nREPLACE: {m.group(3).strip()}"


# ── Common system prompt ──────────────────────────────────────────────────────

PATCH_SYSTEM = """You are an automated Java program repair system.

Output format — use EXACTLY this structure, no markdown fences around it:

FILE: path/to/File.java
SEARCH: <exact source lines to replace — must match the file character-for-character>
REPLACE: <corrected lines>

Rules:
- SEARCH must be a verbatim copy of existing source including all whitespace and indentation.
- Include 5-10 lines of context around the changed lines so the match is unique.
- Fix only the buggy logic; do not modify unrelated code.
- When you see a stack trace, the bug is in the CALLER, not the called method."""
```

---

### 10a. `patch_generators/cot.py` — Chain-of-Thought

**Paper:** Wei et al., NeurIPS 2022 (few-shot CoT); Kojima et al., NeurIPS 2022 (zero-shot CoT).

**APR design:** Prefix the patch-generation prompt with an explicit step-by-step reasoning
scaffold before the model outputs the SEARCH/REPLACE patch. This surfaces intermediate
reasoning (root-cause → fix strategy → implementation) in the trace log and tends to
produce better-justified patches on non-trivial bugs.

```
Call 1  →  [context] + "reason step by step, then output patch"
           model writes: Step 1: ... Step 2: ... SEARCH/REPLACE block
```

```python
# swe_agent/patch_generators/cot.py
"""
Chain-of-Thought patch generator.
Paper: Wei et al. (NeurIPS 2022), Kojima et al. (NeurIPS 2022)
Strategy: one call — step-by-step reasoning scaffold before the patch output.
Budget: 1 LLM call per attempt (cheapest structured reasoning baseline).
"""
from pathlib import Path
from .base import PatchGenerator, PatchResult
from ._shared import (build_fail_context, build_location_context,
                       extract_search_replace, PATCH_SYSTEM)


USER_TEMPLATE = """{fail_context}

## Suspicious location(s)
{location_context}

## Task
Reason step by step before writing the patch.

Step 1 — Root cause: explain what is wrong and why the test fails.
Step 2 — Fix strategy: describe the minimal code change that corrects the bug.
Step 3 — Implementation: write the patch.

Then output the patch using EXACTLY this format:

FILE: path/to/File.java
SEARCH: <exact lines>
REPLACE: <corrected lines>
"""


class CoTPatchGenerator(PatchGenerator):

    def generate_patch(
        self,
        bug_id, workdir, failing_info, trigger_tests,
        localization_hits, attempt_index, out_dir, llm_client,
    ) -> PatchResult:

        fail_ctx = build_fail_context(bug_id, trigger_tests, failing_info)
        loc_ctx  = build_location_context(localization_hits, workdir)

        prompt   = USER_TEMPLATE.format(
            fail_context=fail_ctx,
            location_context=loc_ctx,
        )
        messages = [
            {"role": "system", "content": PATCH_SYSTEM},
            {"role": "user",   "content": prompt},
        ]

        response = llm_client.chat(
            messages, purpose="cot_patch_gen",
            attempt=attempt_index, out_dir=out_dir, max_tokens=1500,
        )

        diff = extract_search_replace(response or "")
        return PatchResult(
            diff_text=diff,
            metadata={"strategy": "cot", "raw_response": response or ""},
        )
```

---

### 10b. `patch_generators/reflexion.py` — Reflexion

**Paper:** Shinn et al., NeurIPS 2023.

**APR design:** Three-stage loop within a single attempt's LLM budget.
Stage 1 (Actor) generates a patch with CoT. Stage 2 (Evaluator) checks whether
the patch is self-consistent. Stage 3 (Reflector) writes a verbal reflection
and produces a revised patch. Reflections are stored in a memory buffer and
included in subsequent attempts so the model learns from earlier failures.

```
Call 1  →  Actor:    CoT prompt → initial patch
Call 2  →  Evaluator: "is this patch logically correct?" → yes/no + reason
Call 3  →  Reflector: initial patch + eval feedback + memory → revised patch
```

```python
# swe_agent/patch_generators/reflexion.py
"""
Reflexion patch generator.
Paper: Shinn et al., NeurIPS 2023 (arXiv:2303.11366)
Strategy:
  Call 1 — Actor generates an initial patch (CoT).
  Call 2 — Evaluator checks the patch for logical consistency.
  Call 3 — Reflector critiques + revises using memory of past reflections.
Budget: up to 3 LLM calls per attempt; memory persists across attempts.
"""
from pathlib import Path
from .base import PatchGenerator, PatchResult
from ._shared import (build_fail_context, build_location_context,
                       extract_search_replace, PATCH_SYSTEM)


ACTOR_TEMPLATE = """{fail_context}

## Suspicious location(s)
{location_context}

{memory_block}

Reason step by step, then output the patch:

FILE: path/to/File.java
SEARCH: <exact lines>
REPLACE: <corrected lines>
"""

EVAL_TEMPLATE = """You are reviewing a proposed Java patch for bug {bug_id}.

## Patch
{patch}

## Failing test context
{fail_context}

Does this patch correctly fix the root cause without breaking other behaviour?
Respond with:
Verdict: CORRECT | WRONG | UNCERTAIN
Reason: <one sentence>
"""

REFLECT_TEMPLATE = """You proposed this patch for bug {bug_id}:

{patch}

The evaluator said:
{eval_result}

Previous reflections (if any):
{memory_block}

1. Reflection: <what specifically is wrong with the previous patch>
2. Revised patch — output using EXACTLY this format:

FILE: path/to/File.java
SEARCH: <exact lines>
REPLACE: <corrected lines>
"""


class ReflexionPatchGenerator(PatchGenerator):
    """
    Maintains an episodic memory buffer of verbal reflections across attempts.
    The buffer is cleared when a new bug starts (one instance per bug in runner.py).
    """

    def __init__(self):
        self._memory: list[str] = []   # episodic buffer — persists across attempts

    def generate_patch(
        self,
        bug_id, workdir, failing_info, trigger_tests,
        localization_hits, attempt_index, out_dir, llm_client,
    ) -> PatchResult:

        fail_ctx = build_fail_context(bug_id, trigger_tests, failing_info)
        loc_ctx  = build_location_context(localization_hits, workdir)
        mem_block = (
            "Memory from previous attempts:\n" +
            "\n".join(f"- {m}" for m in self._memory[-3:])  # sliding window of 3
            if self._memory else ""
        )

        # ── Call 1: Actor ─────────────────────────────────────────────────────
        actor_prompt = ACTOR_TEMPLATE.format(
            fail_context=fail_ctx, location_context=loc_ctx,
            memory_block=mem_block,
        )
        initial_response = llm_client.chat(
            [{"role": "system", "content": PATCH_SYSTEM},
             {"role": "user",   "content": actor_prompt}],
            purpose="reflexion_actor", attempt=attempt_index,
            out_dir=out_dir, max_tokens=1500,
        )
        initial_patch = extract_search_replace(initial_response or "")

        if not initial_patch:
            # Nothing to reflect on — return empty
            return PatchResult(diff_text="",
                               metadata={"strategy": "reflexion", "stage": "actor_failed"})

        # ── Call 2: Evaluator ────────────────────────────────────────────────
        eval_response = llm_client.chat(
            [{"role": "user", "content": EVAL_TEMPLATE.format(
                bug_id=bug_id, patch=initial_patch, fail_context=fail_ctx,
            )}],
            purpose="reflexion_evaluator", attempt=attempt_index,
            out_dir=out_dir, max_tokens=300,
        )

        verdict = "CORRECT"
        for line in (eval_response or "").splitlines():
            if line.lower().startswith("verdict:"):
                verdict = line.split(":", 1)[-1].strip().upper()
                break

        # If evaluator is confident the patch is correct, skip reflection
        if verdict == "CORRECT":
            return PatchResult(
                diff_text=initial_patch,
                metadata={"strategy": "reflexion", "stage": "accepted_by_evaluator",
                          "eval": eval_response or ""},
            )

        # ── Call 3: Reflector ────────────────────────────────────────────────
        reflect_response = llm_client.chat(
            [{"role": "system", "content": PATCH_SYSTEM},
             {"role": "user",   "content": REFLECT_TEMPLATE.format(
                 bug_id=bug_id, patch=initial_patch,
                 eval_result=eval_response or "",
                 memory_block=mem_block,
             )}],
            purpose="reflexion_reflector", attempt=attempt_index,
            out_dir=out_dir, max_tokens=1500,
        )

        # Store reflection in memory buffer for future attempts
        for line in (reflect_response or "").splitlines():
            if line.lower().startswith("reflection:"):
                self._memory.append(line.split(":", 1)[-1].strip())
                break

        revised_patch = extract_search_replace(reflect_response or "")
        return PatchResult(
            diff_text=revised_patch or initial_patch,
            metadata={"strategy": "reflexion", "stage": "reflected",
                      "verdict": verdict, "memory": list(self._memory),
                      "raw_reflect": reflect_response or ""},
        )
```

> **Runner note:** Because `ReflexionPatchGenerator` holds mutable state (`_memory`),
> `runner.py` must instantiate it **once per bug**, not once globally.
> The current `GENERATORS` dict stores classes; `run_bug()` calls `GENERATORS[baseline]()`
> fresh for each bug — this is already correct.

---

### 10c. `patch_generators/tot.py` — Tree of Thoughts

**Paper:** Yao et al., NeurIPS 2023 oral (arXiv:2305.10601).

**APR design:** Generate `N` candidate patches (branching), evaluate each for plausibility
without running the compiler, then synthesise the single best patch. This is BFS at depth-1
because the LLM budget is tight; depth can be increased by raising `n_branches`.

```
Call 1  →  generate N candidate patches in one response (branching)
Call 2  →  evaluate each candidate: plausible / risky / incorrect
Call 3  →  synthesise: given the evaluation, output the best patch
```

> **Key verified insight:** ToT's defining features are **state evaluation** and
> **backtracking / pruning**, not just branching. The evaluator call implements the
> state-evaluation step. Candidates scored `incorrect` are pruned before synthesis.

```python
# swe_agent/patch_generators/tot.py
"""
Tree of Thoughts patch generator.
Paper: Yao et al., NeurIPS 2023 (arXiv:2305.10601)
Strategy:
  Call 1 — generate N candidate patches (branching).
  Call 2 — evaluate each candidate (state evaluation / pruning).
  Call 3 — synthesise best candidate into final patch (selection).
Budget: 3 LLM calls per attempt.
"""
import re
from pathlib import Path
from .base import PatchGenerator, PatchResult
from ._shared import (build_fail_context, build_location_context,
                       extract_search_replace, PATCH_SYSTEM)


BRANCH_TEMPLATE = """{fail_context}

## Suspicious location(s)
{location_context}

## Task
Generate {n_branches} distinct candidate patches for this bug.
Each candidate must represent a different fix strategy.

For each candidate write:
### Candidate N
Strategy: <one sentence describing the approach>
FILE: path/to/File.java
SEARCH: <exact lines>
REPLACE: <corrected lines>
"""

EVAL_TEMPLATE = """{fail_context}

## Candidate patches to evaluate

{candidates_text}

For each candidate, rate it:
  plausible   — logically fixes the root cause, low regression risk
  risky       — might fix the symptom but could break other tests
  incorrect   — wrong diagnosis or wrong code change

Respond with one line per candidate:
Candidate 1: <plausible|risky|incorrect> — <one-sentence reason>
Candidate 2: <plausible|risky|incorrect> — <one-sentence reason>
...
"""

SELECT_TEMPLATE = """{fail_context}

## Candidate patches
{candidates_text}

## Evaluations
{evaluations}

Select the most plausible candidate (skip any rated incorrect).
Output ONLY the selected patch:

FILE: path/to/File.java
SEARCH: <exact lines>
REPLACE: <corrected lines>
"""


def _split_candidates(text: str) -> list[str]:
    """Split a multi-candidate response by '### Candidate N' headers."""
    parts = re.split(r"###\s*Candidate\s*\d+", text, flags=re.IGNORECASE)
    return [p.strip() for p in parts if p.strip()]


class ToTPatchGenerator(PatchGenerator):

    def __init__(self, n_branches: int = 3):
        self.n_branches = n_branches

    def generate_patch(
        self,
        bug_id, workdir, failing_info, trigger_tests,
        localization_hits, attempt_index, out_dir, llm_client,
    ) -> PatchResult:

        fail_ctx = build_fail_context(bug_id, trigger_tests, failing_info)
        loc_ctx  = build_location_context(localization_hits, workdir)

        # ── Call 1: Branching — generate N candidates ─────────────────────
        branch_response = llm_client.chat(
            [{"role": "system", "content": PATCH_SYSTEM},
             {"role": "user", "content": BRANCH_TEMPLATE.format(
                 fail_context=fail_ctx, location_context=loc_ctx,
                 n_branches=self.n_branches,
             )}],
            purpose="tot_branch", attempt=attempt_index,
            out_dir=out_dir, max_tokens=2000,
        )

        raw_candidates = _split_candidates(branch_response or "")
        if not raw_candidates:
            return PatchResult(diff_text="",
                               metadata={"strategy": "tot", "stage": "no_candidates"})

        candidates_text = "\n\n".join(
            f"### Candidate {i+1}\n{c}" for i, c in enumerate(raw_candidates)
        )

        # ── Call 2: Evaluate — state evaluation / pruning ────────────────
        eval_response = llm_client.chat(
            [{"role": "user", "content": EVAL_TEMPLATE.format(
                fail_context=fail_ctx, candidates_text=candidates_text,
            )}],
            purpose="tot_evaluate", attempt=attempt_index,
            out_dir=out_dir, max_tokens=500,
        )

        # ── Call 3: Select — synthesise the best surviving path ───────────
        select_response = llm_client.chat(
            [{"role": "system", "content": PATCH_SYSTEM},
             {"role": "user", "content": SELECT_TEMPLATE.format(
                 fail_context=fail_ctx,
                 candidates_text=candidates_text,
                 evaluations=eval_response or "",
             )}],
            purpose="tot_select", attempt=attempt_index,
            out_dir=out_dir, max_tokens=1200,
        )

        diff = extract_search_replace(select_response or "")
        return PatchResult(
            diff_text=diff,
            metadata={
                "strategy":    "tot",
                "n_branches":  self.n_branches,
                "evaluations": eval_response or "",
                "raw_select":  select_response or "",
            },
        )
```

---

### 10d. `patch_generators/self_consistency.py` — Self-Consistency

**Paper:** Wang et al., ICLR 2023 (arXiv:2203.11171).

**APR design:** Generate `N` independent patches using different CoT phrasings, then ask
the model to act as a meta-judge and pick the most consistent fix. Wang et al. showed
+17.9% on GSM8K over standard CoT using majority vote — here we use an LLM judge
instead of exact-string majority vote because patch diffs are not easily deduplicated.

```
Calls 1…N  →  N independent CoT patch generations (different prompts)
Call  N+1  →  meta-judge picks the most consistent patch
```

```python
# swe_agent/patch_generators/self_consistency.py
"""
Self-Consistency patch generator.
Paper: Wang et al., ICLR 2023 (arXiv:2203.11171)
Strategy:
  Calls 1..N — N independent CoT patch generations.
  Call N+1   — LLM judge selects the most consistent patch.
Budget: N+1 LLM calls per attempt (default N=3 to stay within budget).
Note: N is capped at MAX_LLM_CALLS_PER_ATTEMPT - 1.
"""
from pathlib import Path
from .base import PatchGenerator, PatchResult
from ._shared import (build_fail_context, build_location_context,
                       extract_search_replace, PATCH_SYSTEM)
from ...config import MAX_LLM_CALLS_PER_ATTEMPT


# Different CoT phrasings — each elicits a slightly different reasoning path
PHRASINGS = [
    "Reason step by step about the root cause, then output the patch.",
    "Think carefully about what the stack trace tells you, then output the patch.",
    "Identify the minimal code change that fixes the failing test, then output the patch.",
    "Trace the execution path from the test to the bug, then output the patch.",
    "Consider what invariant the buggy code violates, then output the patch.",
]

BASE_TEMPLATE = """{fail_context}

## Suspicious location(s)
{location_context}

{phrasing}

FILE: path/to/File.java
SEARCH: <exact lines>
REPLACE: <corrected lines>
"""

JUDGE_TEMPLATE = """{fail_context}

## {n} independently generated candidate patches

{candidates_text}

These patches were generated with different reasoning approaches for the same bug.
Select the patch that is most likely to be correct.
Prefer the patch that:
  1. Targets the actual root cause (not just a symptom)
  2. Makes the minimal necessary change
  3. Is consistent with multiple other candidates

Output ONLY the selected patch (no commentary):

FILE: path/to/File.java
SEARCH: <exact lines>
REPLACE: <corrected lines>
"""


class SelfConsistencyPatchGenerator(PatchGenerator):

    def __init__(self, n_samples: int = 3):
        # cap at budget - 1 to leave room for the judge call
        self.n_samples = min(n_samples, MAX_LLM_CALLS_PER_ATTEMPT - 1)

    def generate_patch(
        self,
        bug_id, workdir, failing_info, trigger_tests,
        localization_hits, attempt_index, out_dir, llm_client,
    ) -> PatchResult:

        fail_ctx = build_fail_context(bug_id, trigger_tests, failing_info)
        loc_ctx  = build_location_context(localization_hits, workdir)

        # ── Calls 1…N: independent patch samples ─────────────────────────
        patches = []
        raws    = []
        for i in range(self.n_samples):
            phrasing = PHRASINGS[i % len(PHRASINGS)]
            prompt   = BASE_TEMPLATE.format(
                fail_context=fail_ctx, location_context=loc_ctx,
                phrasing=phrasing,
            )
            raw = llm_client.chat(
                [{"role": "system", "content": PATCH_SYSTEM},
                 {"role": "user",   "content": prompt}],
                purpose=f"sc_sample_{i+1}", attempt=attempt_index,
                out_dir=out_dir, max_tokens=1200,
            )
            raws.append(raw or "")
            p = extract_search_replace(raw or "")
            if p:
                patches.append(p)

        if not patches:
            return PatchResult(diff_text="",
                               metadata={"strategy": "self_consistency",
                                         "stage": "no_patches_extracted"})

        # If only one unique patch, return it directly
        if len(set(patches)) == 1:
            return PatchResult(
                diff_text=patches[0],
                metadata={"strategy": "self_consistency", "n_samples": self.n_samples,
                          "unanimous": True},
            )

        # ── Call N+1: meta-judge ──────────────────────────────────────────
        candidates_text = "\n\n".join(
            f"### Candidate {i+1}\n{p}" for i, p in enumerate(patches)
        )
        judge_response = llm_client.chat(
            [{"role": "system", "content": PATCH_SYSTEM},
             {"role": "user",   "content": JUDGE_TEMPLATE.format(
                 fail_context=fail_ctx, n=len(patches),
                 candidates_text=candidates_text,
             )}],
            purpose="sc_judge", attempt=attempt_index,
            out_dir=out_dir, max_tokens=1200,
        )

        final_patch = extract_search_replace(judge_response or "") or patches[0]
        return PatchResult(
            diff_text=final_patch,
            metadata={
                "strategy":    "self_consistency",
                "n_samples":   self.n_samples,
                "n_patches":   len(patches),
                "raw_samples": raws,
                "raw_judge":   judge_response or "",
            },
        )
```

---

### 10e. `patch_generators/got.py` — Graph of Thoughts

**Paper:** Besta et al., AAAI 2024 (arXiv:2308.09687).

**APR design:** Represent repair reasoning as a directed graph with three node types.
`Generation` nodes branch from the root (multiple fix hypotheses). `Refinement` nodes
self-loop to improve a hypothesis. `Aggregation` nodes merge two hypotheses into one
combined patch — this is the key novelty that cannot be represented in a tree.
The graph is stored in Python and its summary is injected into the synthesis prompt.

```
Call 1  →  seed: high-level root-cause analysis
Call 2  →  Generation: hypothesis A (approach 1)
Call 3  →  Generation: hypothesis B (approach 2)
Call 4  →  Aggregation: merge A + B into a combined patch node
Call 5  →  Synthesis: given full graph, output final patch
```

> **Critical lesson from the tutorial:** the synthesis call must receive the
> **full graph summary** — not just the original question. Passing only the bug context
> discards all graph reasoning.

```python
# swe_agent/patch_generators/got.py
"""
Graph of Thoughts patch generator.
Paper: Besta et al., AAAI 2024 (arXiv:2308.09687)
Strategy:
  Call 1 — seed node: root-cause analysis.
  Call 2 — Generation node: fix hypothesis A.
  Call 3 — Generation node: fix hypothesis B.
  Call 4 — Aggregation node: merge A + B (the GoT novelty vs ToT).
  Call 5 — Synthesis: produce final patch from full graph.
Budget: 5 LLM calls per attempt.
"""
from dataclasses import dataclass, field
from pathlib import Path
from .base import PatchGenerator, PatchResult
from ._shared import (build_fail_context, build_location_context,
                       extract_search_replace, PATCH_SYSTEM)


# ── Graph data structure ──────────────────────────────────────────────────────

@dataclass
class GoTGraph:
    nodes: dict  = field(default_factory=dict)   # id → text
    edges: list  = field(default_factory=list)   # (src, dst, relation)
    _ctr:  int   = field(default=1, repr=False)

    def add(self, text: str, label: str = "") -> str:
        nid = f"N{self._ctr}"
        self._ctr += 1
        self.nodes[nid] = f"[{label}] {text}" if label else text
        return nid

    def connect(self, src: str, dst: str, rel: str):
        self.edges.append((src, dst, rel))

    def summary(self) -> str:
        node_lines = "\n".join(
            f"  {nid}: {txt[:200]}" for nid, txt in self.nodes.items()
        )
        edge_lines = "\n".join(
            f"  {s} --[{r}]--> {d}" for s, d, r in self.edges
        )
        return f"Nodes:\n{node_lines}\n\nEdges:\n{edge_lines}"


# ── Prompt templates ──────────────────────────────────────────────────────────

SEED_TEMPLATE = """{fail_context}

## Suspicious location(s)
{location_context}

Analyse the root cause of this bug. Do NOT write a patch yet.
Describe: what is wrong, why it causes the test to fail, which lines are buggy.
"""

GENERATE_TEMPLATE = """Task: fix bug {bug_id}.

Root cause analysis:
{seed_text}

Generate a {approach_label} fix strategy and the corresponding patch.

Strategy: <one sentence>
FILE: path/to/File.java
SEARCH: <exact lines>
REPLACE: <corrected lines>
"""

AGGREGATE_TEMPLATE = """Task: fix bug {bug_id}.

Root cause:
{seed_text}

Fix hypothesis A ({nid_a}):
{text_a}

Fix hypothesis B ({nid_b}):
{text_b}

Merge the strongest elements of A and B into a single improved patch.
Take the best diagnosis from each, resolve any contradictions, and output:

FILE: path/to/File.java
SEARCH: <exact lines>
REPLACE: <corrected lines>
"""

SYNTHESIS_TEMPLATE = """Task: fix bug {bug_id}.

{fail_context}

## Full reasoning graph

{graph_summary}

Review the graph. Select the reasoning path that best explains the root cause,
then output the final patch.

FILE: path/to/File.java
SEARCH: <exact lines>
REPLACE: <corrected lines>
"""


class GoTPatchGenerator(PatchGenerator):

    def generate_patch(
        self,
        bug_id, workdir, failing_info, trigger_tests,
        localization_hits, attempt_index, out_dir, llm_client,
    ) -> PatchResult:

        fail_ctx = build_fail_context(bug_id, trigger_tests, failing_info)
        loc_ctx  = build_location_context(localization_hits, workdir)
        g = GoTGraph()

        # ── Call 1: Seed — root-cause analysis ────────────────────────────
        seed_raw = llm_client.chat(
            [{"role": "user", "content": SEED_TEMPLATE.format(
                fail_context=fail_ctx, location_context=loc_ctx,
            )}],
            purpose="got_seed", attempt=attempt_index,
            out_dir=out_dir, max_tokens=600,
        )
        n_seed = g.add(seed_raw or "", label="root-cause")

        # ── Calls 2-3: Generation — two distinct fix hypotheses ───────────
        hypothesis_nodes = []
        for approach in ("conservative minimal fix", "alternative deeper fix"):
            hyp_raw = llm_client.chat(
                [{"role": "system", "content": PATCH_SYSTEM},
                 {"role": "user",   "content": GENERATE_TEMPLATE.format(
                     bug_id=bug_id, seed_text=seed_raw or "",
                     approach_label=approach,
                 )}],
                purpose=f"got_generate_{approach.split()[0]}", attempt=attempt_index,
                out_dir=out_dir, max_tokens=1000,
            )
            n_hyp = g.add(hyp_raw or "", label=f"gen:{approach}")
            g.connect(n_seed, n_hyp, "generation")
            hypothesis_nodes.append(n_hyp)

        # ── Call 4: Aggregation — merge hypotheses (GoT novelty) ──────────
        if len(hypothesis_nodes) == 2:
            nid_a, nid_b = hypothesis_nodes
            agg_raw = llm_client.chat(
                [{"role": "system", "content": PATCH_SYSTEM},
                 {"role": "user",   "content": AGGREGATE_TEMPLATE.format(
                     bug_id=bug_id,
                     seed_text=seed_raw or "",
                     nid_a=nid_a, text_a=g.nodes[nid_a],
                     nid_b=nid_b, text_b=g.nodes[nid_b],
                 )}],
                purpose="got_aggregate", attempt=attempt_index,
                out_dir=out_dir, max_tokens=1200,
            )
            n_agg = g.add(agg_raw or "", label="aggregation")
            g.connect(nid_a, n_agg, "aggregation")
            g.connect(nid_b, n_agg, "aggregation")

        # ── Call 5: Synthesis — final patch from full graph ───────────────
        # CRITICAL: pass graph summary, not just fail_ctx
        synth_raw = llm_client.chat(
            [{"role": "system", "content": PATCH_SYSTEM},
             {"role": "user",   "content": SYNTHESIS_TEMPLATE.format(
                 bug_id=bug_id,
                 fail_context=fail_ctx,
                 graph_summary=g.summary(),
             )}],
            purpose="got_synthesis", attempt=attempt_index,
            out_dir=out_dir, max_tokens=1200,
        )

        diff = extract_search_replace(synth_raw or "")
        return PatchResult(
            diff_text=diff,
            metadata={
                "strategy":     "got",
                "graph_nodes":  len(g.nodes),
                "graph_edges":  len(g.edges),
                "graph_summary": g.summary(),
                "raw_synth":    synth_raw or "",
            },
        )
```
