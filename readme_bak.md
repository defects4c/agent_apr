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
BASELINES = ["agentless", "swe_agent", "openhands", "openclaw", "claude_code"]
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

## 10. Five Baseline Implementations

### 10.1 `agentless.py` — 1–2 LLM calls per attempt

```python
# swe_agent/patch_generators/agentless.py
"""
Strategy:
  Call 1 → generate diff from failing info + snippets
  Call 2 (optional) → fix build error if compile fails (counted within MAX_LLM_CALLS_PER_ATTEMPT)
"""

SYSTEM = ("You are an automated program repair system. "
          "Output ONLY a unified diff. No markdown fences. No explanations. "
          "Paths must be relative to the project root. Begin with ---")

USER_TEMPLATE = """## Bug: {bug_id}

## Failing tests
{trigger_test_names}

## Failure messages and stack traces (top 50 lines)
{stack_trace_excerpt}

## Suspicious location(s)
{locations}

## Task
Produce a unified diff that makes all failing tests pass without breaking other tests.
Output ONLY the diff. Begin with --- and +++."""


def build_location_block(hits) -> str:
    blocks = []
    for h in hits:
        blocks.append(
            f"### {h.filepath} lines {h.start_line}–{h.end_line} "
            f"(confidence {h.confidence:.2f})\n"
            f"```java\n{h.snippet}\n```"
        )
    return "\n\n".join(blocks)


class AgentlessPatchGenerator(PatchGenerator):

    def generate_patch(self, bug_id, workdir, failing_info, trigger_tests,
                       localization_hits, attempt_index, out_dir, llm_client):
        traces = "\n\n".join(
            (fi["error_message"] + "\n" + fi["stack_trace"])[:2000]
            for fi in failing_info.values()
        )
        prompt = USER_TEMPLATE.format(
            bug_id=bug_id,
            trigger_test_names="\n".join(trigger_tests),
            stack_trace_excerpt=traces[:3000],
            locations=build_location_block(localization_hits),
        )
        messages = [{"role": "system", "content": SYSTEM},
                    {"role": "user",   "content": prompt}]
        diff = llm_client.chat(messages, purpose="patch_gen",
                               attempt=attempt_index, out_dir=out_dir)
        return PatchResult(diff_text=diff, metadata={"strategy": "agentless"})
```

---

### 10.2 `swe_agent.py` — ReAct loop (Thought → Action → Observation)

```python
# swe_agent/patch_generators/swe_agent.py
"""
Strategy: ReAct loop.
  Each turn: LLM outputs Thought + Action (one of: read_file, search, submit_patch)
  Harness executes action, returns Observation.
  Loop ends on submit_patch or budget exhausted.
  Max turns = MAX_LLM_CALLS_PER_ATTEMPT.
"""

SYSTEM = """You are a software engineer fixing a bug. Respond with:
Thought: <reasoning>
Action: <one of the actions below>

Available actions:
  read_file(path, start_line, end_line)   → returns file lines
  search(pattern, path)                   → returns matching lines
  submit_patch(diff)                      → submit unified diff and finish

Rules: output ONLY in the format above. No markdown. One action per turn."""

class SWEAgentPatchGenerator(PatchGenerator):

    def generate_patch(self, bug_id, workdir, failing_info, trigger_tests,
                       localization_hits, attempt_index, out_dir, llm_client):
        from ..config import MAX_LLM_CALLS_PER_ATTEMPT
        from . import _react_tools  # read_file, search implementations

        history = [{"role": "system", "content": SYSTEM},
                   {"role": "user",   "content": self._initial_message(
                       bug_id, trigger_tests, failing_info, localization_hits)}]

        for turn in range(MAX_LLM_CALLS_PER_ATTEMPT):
            response = llm_client.chat(history, purpose=f"react_turn_{turn}",
                                       attempt=attempt_index, out_dir=out_dir)
            history.append({"role": "assistant", "content": response})

            action, arg = _react_tools.parse_action(response)

            if action == "submit_patch":
                return PatchResult(diff_text=arg, metadata={"strategy": "swe_agent", "turns": turn+1})

            # execute tool and add observation
            observation = _react_tools.execute(action, arg, workdir)
            history.append({"role": "user", "content": f"Observation:\n{observation}"})

        return PatchResult(diff_text="", metadata={"strategy": "swe_agent", "reason": "budget_exhausted"})

    @staticmethod
    def _initial_message(bug_id, trigger_tests, failing_info, loc_hits) -> str:
        traces = "\n".join(
            fi["error_message"] + "\n" + fi["stack_trace"][:500]
            for fi in list(failing_info.values())[:2]
        )
        return (f"Bug: {bug_id}\nFailing tests: {trigger_tests}\n"
                f"Traces:\n{traces}\n\n"
                f"Suspicious files: {[h.filepath for h in loc_hits]}\n\n"
                "Fix the bug. Start exploring the code.")
```

---

### 10.3 `openhands.py` — Budgeted tool-use loop

```python
# swe_agent/patch_generators/openhands.py
"""
Strategy: OpenAI tool-use (function calling) loop.
  Tools: read_snippet, search_in_file, propose_patch
  Each tool call is one LLM interaction (counted against budget).
  context_lines_budget caps total lines read.
  Harness validates the diff from propose_patch — not OpenHands internally.
"""

TOOLS = [
    {"type": "function", "function": {
        "name": "read_snippet",
        "description": "Read lines from a source file",
        "parameters": {"type": "object",
                       "properties": {"path":       {"type": "string"},
                                      "start_line": {"type": "integer"},
                                      "end_line":   {"type": "integer"}},
                       "required": ["path", "start_line", "end_line"]}}},
    {"type": "function", "function": {
        "name": "search_in_file",
        "description": "Search for a pattern in a file",
        "parameters": {"type": "object",
                       "properties": {"path":    {"type": "string"},
                                      "pattern": {"type": "string"}},
                       "required": ["path", "pattern"]}}},
    {"type": "function", "function": {
        "name": "propose_patch",
        "description": "Submit a unified diff as your repair",
        "parameters": {"type": "object",
                       "properties": {"diff": {"type": "string"}},
                       "required": ["diff"]}}},
]

class OpenHandsPatchGenerator(PatchGenerator):

    def generate_patch(self, bug_id, workdir, failing_info, trigger_tests,
                       localization_hits, attempt_index, out_dir, llm_client):
        from ..config import MAX_LLM_CALLS_PER_ATTEMPT, CONTEXT_LINES_PER_LOCATION, MAX_LOCATIONS_PER_ATTEMPT
        context_budget = CONTEXT_LINES_PER_LOCATION * MAX_LOCATIONS_PER_ATTEMPT
        context_used   = 0

        messages = [{"role": "user", "content":
                     self._task_message(bug_id, trigger_tests, failing_info, localization_hits)}]

        for turn in range(MAX_LLM_CALLS_PER_ATTEMPT):
            # NOTE: tool-use is handled via prompt engineering since we use a generic endpoint
            response = llm_client.chat(messages, purpose=f"tool_turn_{turn}",
                                       attempt=attempt_index, out_dir=out_dir)
            messages.append({"role": "assistant", "content": response})

            tool_name, tool_args = _parse_tool_call(response)

            if tool_name == "propose_patch":
                return PatchResult(diff_text=tool_args.get("diff", ""),
                                   metadata={"strategy": "openhands", "turns": turn+1,
                                             "context_lines_used": context_used})

            if tool_name in ("read_snippet", "search_in_file"):
                result, lines_consumed = _execute_tool(tool_name, tool_args, workdir)
                context_used += lines_consumed
                if context_used > context_budget:
                    result = "[context budget exhausted — no more reads allowed]"
                messages.append({"role": "user",
                                 "content": f"Tool result ({tool_name}):\n{result}"})

        return PatchResult(diff_text="", metadata={"strategy": "openhands",
                                                    "reason": "no_patch_proposed"})

    @staticmethod
    def _task_message(bug_id, trigger_tests, failing_info, loc_hits) -> str:
        # ... build rich initial message with failing traces + suspicious files
        pass
```

---

### 10.4 `openclaw.py` — Structured 3-call pipeline

```python
# swe_agent/patch_generators/openclaw.py
"""
Strategy: fixed 3-call pipeline (Search → Analyze → Patch).
  Call 1: identify suspicious methods → JSON {suspicious_methods: [...]}
  Call 2: analyze methods → JSON {root_cause, fix_strategy}
  Call 3: generate diff from analysis

Matches Claude-skill approach but named "openclaw" for the baseline.
"""

class OpenClawPatchGenerator(PatchGenerator):

    def generate_patch(self, bug_id, workdir, failing_info, trigger_tests,
                       localization_hits, attempt_index, out_dir, llm_client):
        traces = self._format_traces(failing_info)

        # ── Call 1: Search ─────────────────────────────────────────────────
        search_prompt = (
            f"Bug {bug_id}. Failing traces:\n{traces}\n\n"
            "Output ONLY JSON: {\"suspicious_methods\": [\"pkg.Class.method\", ...]}"
        )
        raw = llm_client.chat(
            [{"role": "user", "content": search_prompt}],
            purpose="search", attempt=attempt_index, out_dir=out_dir)
        suspicious = self._parse_json(raw).get("suspicious_methods", [])

        # ── Call 2: Analyze ────────────────────────────────────────────────
        snippets = self._load_snippets(suspicious, workdir)
        analyze_prompt = (
            f"Bug {bug_id}. Suspected methods:\n{snippets}\n\nFailing traces:\n{traces}\n\n"
            "Output ONLY JSON: {\"root_cause\": \"...\", \"fix_strategy\": \"...\"}"
        )
        raw2 = llm_client.chat(
            [{"role": "user", "content": analyze_prompt}],
            purpose="analyze", attempt=attempt_index, out_dir=out_dir)
        analysis = self._parse_json(raw2)

        # ── Call 3: Patch ──────────────────────────────────────────────────
        patch_prompt = (
            f"Bug {bug_id}.\nRoot cause: {analysis.get('root_cause')}\n"
            f"Fix strategy: {analysis.get('fix_strategy')}\n\n"
            f"Relevant code:\n{snippets}\n\n"
            "Output ONLY a unified diff. No fences. Begin with ---"
        )
        diff = llm_client.chat(
            [{"role": "user", "content": patch_prompt}],
            purpose="patch_gen", attempt=attempt_index, out_dir=out_dir)

        return PatchResult(diff_text=diff,
                           metadata={"strategy": "openclaw",
                                     "root_cause": analysis.get("root_cause"),
                                     "suspicious_methods": suspicious})

    @staticmethod
    def _format_traces(failing_info: dict) -> str:
        return "\n\n".join(
            fi["error_message"] + "\n" + fi["stack_trace"][:500]
            for fi in list(failing_info.values())[:2]
        )

    @staticmethod
    def _parse_json(raw: str) -> dict:
        import json, re
        raw = re.sub(r"```json|```", "", raw).strip()
        try: return json.loads(raw)
        except: return {}

    @staticmethod
    def _load_snippets(method_names: list[str], workdir: Path) -> str:
        # resolve dotted names → file paths → read ±50 lines
        # fallback to empty string per method if not found
        pass
```

---

### 10.5 `claude_code.py` — Skill-based (read / search / propose)

```python
# swe_agent/patch_generators/claude_code.py
"""
Strategy: same 3-skill loop as openclaw but with an explicit "skill" framing
that Claude Code responds to natively.
Skill set: read_file, search, write_patch.
"""

SKILL_SYSTEM = """You are Claude Code, an expert software repair agent.
You have three skills:
  read_file(path, start, end)  → returns lines of code
  search(pattern)              → searches project source for a regex pattern
  write_patch(diff)            → submit your repair as a unified diff

Think step by step. Use skills to gather context, then write_patch once confident.
Output format per turn:
  SKILL: <skill_name>
  ARGS: <json args>
Do not explain or use markdown."""

class ClaudeCodePatchGenerator(PatchGenerator):

    def generate_patch(self, bug_id, workdir, failing_info, trigger_tests,
                       localization_hits, attempt_index, out_dir, llm_client):
        from ..config import MAX_LLM_CALLS_PER_ATTEMPT

        history = [
            {"role": "system", "content": SKILL_SYSTEM},
            {"role": "user",   "content": self._task_msg(
                bug_id, trigger_tests, failing_info, localization_hits)},
        ]

        for turn in range(MAX_LLM_CALLS_PER_ATTEMPT):
            resp = llm_client.chat(history, purpose=f"skill_turn_{turn}",
                                   attempt=attempt_index, out_dir=out_dir)
            history.append({"role": "assistant", "content": resp})

            skill, args = self._parse_skill(resp)

            if skill == "write_patch":
                return PatchResult(
                    diff_text=args.get("diff", ""),
                    metadata={"strategy": "claude_code", "turns": turn + 1})

            observation = self._execute_skill(skill, args, workdir)
            history.append({"role": "user", "content": f"Result:\n{observation}"})

        return PatchResult(diff_text="", metadata={"strategy": "claude_code",
                                                    "reason": "budget_exhausted"})

    @staticmethod
    def _task_msg(bug_id, trigger_tests, failing_info, loc_hits) -> str:
        traces = "\n\n".join(
            fi["error_message"] + "\n" + fi["stack_trace"][:500]
            for fi in list(failing_info.values())[:2]
        )
        hints = "\n".join(f"  {h.filepath}:{h.start_line}" for h in loc_hits)
        return (f"Fix bug {bug_id}.\nFailing tests: {trigger_tests}\n"
                f"Traces:\n{traces}\nSuspected locations:\n{hints}")

    @staticmethod
    def _parse_skill(response: str) -> tuple[str, dict]:
        import re, json
        sm = re.search(r"SKILL:\s*(\w+)", response)
        am = re.search(r"ARGS:\s*(\{.*\})", response, re.DOTALL)
        skill = sm.group(1) if sm else ""
        args  = json.loads(am.group(1)) if am else {}
        return skill, args

    @staticmethod
    def _execute_skill(skill: str, args: dict, workdir: Path) -> str:
        if skill == "read_file":
            path = workdir / args["path"]
            start, end = int(args.get("start", 1)), int(args.get("end", 50))
            try:
                lines = path.read_text().splitlines()
                return "\n".join(f"{i+start}: {l}"
                                 for i, l in enumerate(lines[start-1:end]))
            except FileNotFoundError:
                return f"File not found: {args['path']}"
        if skill == "search":
            import subprocess
            r = subprocess.run(
                ["grep", "-rn", "--include=*.java", args["pattern"], str(workdir / "src")],
                capture_output=True, text=True)
            return r.stdout[:2000] or "(no matches)"
        return f"Unknown skill: {skill}"
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
from .patch_generators.agentless   import AgentlessPatchGenerator
from .patch_generators.swe_agent   import SWEAgentPatchGenerator
from .patch_generators.openhands   import OpenHandsPatchGenerator
from .patch_generators.openclaw    import OpenClawPatchGenerator
from .patch_generators.claude_code import ClaudeCodePatchGenerator

GENERATORS = {
    "agentless":   AgentlessPatchGenerator,
    "swe_agent":   SWEAgentPatchGenerator,
    "openhands":   OpenHandsPatchGenerator,
    "openclaw":    OpenClawPatchGenerator,
    "claude_code": ClaudeCodePatchGenerator,
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
| 14 | `patch_generators/agentless.py` | base, llm_client |
| 15 | `patch_generators/swe_agent.py` | base, llm_client |
| 16 | `patch_generators/openhands.py` | base, llm_client |
| 17 | `patch_generators/openclaw.py` | base, llm_client |
| 18 | `patch_generators/claude_code.py` | base, llm_client |
| 19 | `runner.py` | all above |
| 20 | `eval.py` | runner |

---

## 14. CLI Quick Reference

```bash
# Single bug, single baseline
python -m swe_agent.runner --project Lang --bug 1 --baseline agentless --out outputs

# Single bug, all baselines
for bl in agentless swe_agent openhands openclaw claude_code; do
  python -m swe_agent.runner --project Lang --bug 1 --baseline $bl --out outputs
done

# Batch run, all baselines
python -m swe_agent.eval \
  --bugs benchmarks/defects4j_small.txt \
  --baseline agentless swe_agent openhands openclaw claude_code \
  --out outputs
```

---

## 15. Key Invariants — Never Violate

1. **One LLM client.** No baseline imports `openai` directly.
2. **Budget before every call and every patch apply.**
3. **Rollback after every failed attempt** — workspace clean before next attempt.
4. **Pre-patch run mandatory** — `test_before.log` must exist for every bug.
5. **Functionality gate before regression gate** — never run full suite if trigger tests still fail.
6. **All timing via `time.monotonic()`.**
7. **`result.json` written even on error.**
8. **Problem folder (`data/defects4j/`) is read-only** — never written by any baseline.
9. **`failing_tests` file is the ground truth** — parsed identically by all baselines via `_load_fail_info`.
10. **Bug name convention:** `Project_ID` in filesystem, `Project-ID` in D4J CLI — convert with `replace("_","-",1)`.




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
