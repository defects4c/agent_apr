# Multi-Baseline Automated Program Repair for Defects4J

A unified framework for evaluating **15 patch-generation strategies** on the
[Defects4J](https://github.com/rjust/defects4j) Java bug benchmark.
Five classical APR agent baselines are combined with ten prompting-strategy
baselines derived from the LLM reasoning literature, all sharing the same
infrastructure, budget controls, and evaluation pipeline.

---

## Table of Contents

1. [Overview](#1-overview)
2. [Project structure](#2-project-structure)
3. [Quick start](#3-quick-start)
4. [Environment variables](#4-environment-variables)
5. [Data preparation](#5-data-preparation)
6. [The 15 baselines](#6-the-15-baselines)
7. [Running experiments](#7-running-experiments)
8. [Output artefacts](#8-output-artefacts)
9. [Budget and safety constraints](#9-budget-and-safety-constraints)
10. [Key invariants](#10-key-invariants)
11. [Adding a new baseline](#11-adding-a-new-baseline)
12. [Claude Code guidance](#12-claude-code-guidance)
13. [References](#13-references)

---

## 1. Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                      Defects4J bug (read-only)                  │
│   data/defects4j/{Project}_{ID}/                                │
│     ├── failing_tests     ← ground-truth trigger tests          │
│     ├── snippet.json      ← buggy method snippets               │
│     └── test_snippet.json ← test case snippets                  │
└──────────────────────────┬──────────────────────────────────────┘
                           │  one pipeline per (baseline, bug)
               ┌───────────▼───────────┐
               │      runner.py        │
               │  checkout → localize  │
               │  → generate_patch()   │
               │  → apply → compile    │
               │  → func_test          │
               │  → reg_test           │
               └───────────┬───────────┘
                           │
              ┌────────────▼────────────┐
              │  PatchGenerator         │
              │  (one of 15 baselines)  │
              └─────────────────────────┘
                           │
              ┌────────────▼────────────┐
              │  LLMClient (shared)     │
              │  budget · trace · log   │
              └─────────────────────────┘
```

All baselines share:
- the same **read-only problem data folder**
- the same **`LLMClient`** (no direct OpenAI imports in baselines)
- the same **budget controls** (calls, tokens, patch size)
- the same **verify pipeline** (compile → func_test → reg_test)
- the same **`result.json` schema** and `eval.py` aggregation

---

## 2. Project structure

```
.claude/
├── guidance.md          ← Claude Code implementation spec (15 baselines)
└── tutorial.md          ← Prompting baselines tutorial with full PoC code

swe_agent/
├── config.py            ← paths, LLM endpoint, budgets, baseline lists
├── llm_client.py        ← single LLM wrapper for all baselines
├── budget.py            ← BudgetManager: patch size + scope checks
├── trace.py             ← TraceWriter: JSONL event log
├── reason.py            ← reason-code constants (REPAIRED, BUILD_FAILED, …)
├── defects4j.py         ← D4J CLI wrapper + bash bridge
├── localize.py          ← fault localisation from test output
├── apply_patch.py       ← git-apply wrapper + rollback
├── tests_runner.py      ← func / regression test runners
├── prepare_data.py      ← build data/defects4j/ from D4J checkouts
├── runner.py            ← single-bug pipeline (checkout→patch→verify)
├── eval.py              ← batch evaluation + report generation
│
├── tasks/
│   ├── base.py                      ← BaseTask, Result dataclass
│   ├── fault_localization.py        ← FaultLocalization task
│   ├── automated_program_repair.py  ← AutomatedProgramRepair task
│   └── utils/
│       ├── defects4j.sh             ← bash bridge (checkout, compile, test)
│       └── bl/sequence_utils.py     ← stack-trace utilities
│
└── patch_generators/
    ├── base.py            ← PatchGenerator ABC + PatchResult dataclass
    ├── _shared.py         ← shared prompt helpers for all prompting baselines
    │
    │  ── Agent baselines ──────────────────────────────────────────────────
    ├── agentless.py       ← 1–2 LLM calls, SEARCH/REPLACE format
    ├── swe_agent.py       ← ReAct loop with file tools
    ├── openhands.py       ← budgeted tool-use loop
    ├── openclaw.py        ← search → analyse → patch
    ├── claude_code.py     ← skill-based read/search/propose
    │
    │  ── Prompting-strategy baselines ────────────────────────────────────
    ├── cot.py             ← Chain-of-Thought (step-by-step scaffold)
    ├── reflexion.py       ← Reflexion (multi-trial verbal RL + memory)
    ├── self_consistency.py← Self-Consistency (N samples + judge vote)
    ├── tot.py             ← Tree of Thoughts (BFS + state evaluation)
    └── got.py             ← Graph of Thoughts (generate + aggregate + refine)

benchmarks/
├── defects4j_small.txt   ← e.g. Lang_1 … Lang_10
└── defects4j_full.txt

data/
├── defects4j/            ← read-only problem data (built by prepare_data.py)
└── repos/                ← live D4J checkouts (one per bug, cleaned after run)

outputs/                  ← auto-created by runner.py / eval.py
```

---

## 3. Quick start

### Prerequisites

| Requirement | Version |
|---|---|
| Python | ≥ 3.11 |
| Java (JDK 8) | OpenJDK 8 |
| Defects4J | ≥ 2.0 (via Docker — see below) |
| Git | any recent version |

### 1 — Clone and install

```bash
git clone <this-repo>
cd <this-repo>
pip install -e ".[dev]"
```

### 2 — Start the Defects4J Docker container

```bash
cd /path/to/defects4j
docker-compose up -d

# Verify
docker-compose exec defects4j defects4j info -p Lang
```

Useful alias (add to `~/.bashrc`):
```bash
alias d4j='docker-compose -f ~/path/to/defects4j/docker-compose.yml \
  exec -w /workspace defects4j defects4j'
```

### 3 — Set environment variables

```bash
export OPENAI_API_KEY="your-key"
export OPENAI_API_BASE_URL="http://your-endpoint/v1/"
export GPT_MODEL="gpt-4o"
export D4J_HOME="/opt/defects4j"
export JAVA8_HOME="/usr/lib/jvm/java-8-openjdk-amd64"
```

### 4 — Prepare problem data

```bash
python -m swe_agent.prepare_data \
  --bugs benchmarks/defects4j_small.txt \
  --out  data/defects4j
```

This runs once. The resulting `data/defects4j/` folder is **read-only** — no
baseline may write into it.

### 5 — Run a single bug

```bash
# One baseline
python -m swe_agent.runner \
  --project Lang --bug 1 --baseline agentless --out outputs

# All agent baselines
for bl in agentless swe_agent openhands openclaw claude_code; do
  python -m swe_agent.runner --project Lang --bug 1 --baseline $bl --out outputs
done

# All prompting-strategy baselines
for bl in cot reflexion self_consistency tot got; do
  python -m swe_agent.runner --project Lang --bug 1 --baseline $bl --out outputs
done
```

### 6 — Batch evaluation

```bash
python -m swe_agent.eval \
  --bugs      benchmarks/defects4j_small.txt \
  --baseline  agentless swe_agent openhands openclaw claude_code \
              cot reflexion self_consistency tot got \
  --out       outputs
```

Results are written to `outputs/report.md`, `outputs/summary.json`, and
`outputs/summary.csv`.

---

## 4. Environment variables

| Variable | Default | Description |
|---|---|---|
| `OPENAI_API_KEY` | `""` | API key for the LLM endpoint |
| `OPENAI_API_BASE_URL` | `http://157.10.162.82:443/v1/` | Compatible OpenAI-format endpoint |
| `GPT_MODEL` | `gpt-5.1` | Model name passed to the API |
| `D4J_HOME` | `/opt/defects4j` | Defects4J installation root |
| `D4J_FOLDER` | `data/defects4j` | Read-only problem data folder |
| `REPOS_DIR` | `data/repos` | Live D4J checkout directory |
| `WORKSPACE_ROOT` | `outputs` | Output root for all runs |
| `JAVA8_HOME` | `/usr/lib/jvm/java-8-openjdk-amd64` | JDK 8 home for Lang/Math/etc. |
| `MAX_ATTEMPTS` | `5` | Retry attempts per bug |

All variables can also be set in a `.env` file at the project root.

---

## 5. Data preparation

### `snippet.json` schema (per buggy method entry)

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

### `failing_tests` format (ground truth, parsed by `_load_fail_info`)

```
--- org.apache.commons.lang3.math.NumberUtilsTest::testLang300
java.lang.StringIndexOutOfBoundsException: String index out of range: 0
    at org.apache.commons.lang3.math.NumberUtils.createNumber(NumberUtils.java:455)
    at org.apache.commons.lang3.math.NumberUtilsTest.testLang300(NumberUtilsTest.java:154)
```

**Bug name convention:** `{Project}_{ID}` in filesystem, `{Project}-{ID}` in D4J CLI.
Always convert with `.replace("_", "-", 1)` before passing to D4J commands.

---

## 6. The 15 baselines

### Agent baselines (5)

These implement full agentic repair loops with real file-system interaction.

| Baseline key | Strategy | LLM calls/attempt |
|---|---|---|
| `agentless` | 1–2 calls, SEARCH/REPLACE patch format | 1–2 |
| `swe_agent` | ReAct loop with file read/write/search tools | 3–8 |
| `openhands` | Budgeted tool-use loop (OpenHands framework) | 3–8 |
| `openclaw` | Structured: search → analyse → patch | 3 |
| `claude_code` | Skill-based: read → search → propose | 3 |

### Prompting-strategy baselines (10)

These adapt techniques from the LLM reasoning literature into the
`PatchGenerator` interface. All go through `_shared.py` helpers for
prompt construction and SEARCH/REPLACE extraction.

| Baseline key | Paper | Venue | LLM calls/attempt | Core mechanism |
|---|---|---|---|---|
| `cot` | Wei et al. | NeurIPS 2022 | 1 | Step-by-step reasoning scaffold before patch output |
| `reflexion` | Shinn et al. | NeurIPS 2023 | ≤3 | Actor → Evaluator → Reflector; sliding-window memory across attempts |
| `self_consistency` | Wang et al. | ICLR 2023 | N+1 | N independent patches with different CoT phrasings; LLM meta-judge selects most consistent |
| `tot` | Yao et al. | NeurIPS 2023 | 3 | Branch N candidates → state evaluation (plausible/risky/incorrect) → select best |
| `got` | Besta et al. | AAAI 2024 | 5 | Seed → Generation ×2 → Aggregation (the GoT novelty) → Synthesis from full graph |
| `standard`* | — (control) | — | 1 | No scaffold, direct patch request |
| `zero_shot_cot`* | Kojima et al. | NeurIPS 2022 | 2 | "Let's think step by step" + two-stage extraction |
| `few_shot_cot`* | Wei et al. | NeurIPS 2022 | 1 | Hand-written APR reasoning demonstrations |
| `react`* | Yao et al. | ICLR 2023 | 1 | Thought/Action/Observation loop; patch from `Action: GeneratePatch` |
| `pot`* | Chen et al. | TMLR 2023 | 1+exec | Model writes Python repair script; subprocess sandbox executes it |

> \* These five are specified in `.claude/guidance.md` but not yet present in
> `patch_generators/` — they are the next batch to implement. See
> [Section 11](#11-adding-a-new-baseline) for the implementation contract.

### Baseline design comparison

```
Standard          prompt = question                        (zero overhead)
Zero-Shot CoT     prompt = question + "Let's think..."     (Kojima et al.)
Few-Shot CoT      prompt = [demos] + question              (Wei et al.)
ReAct             prompt = T/A/O loop scaffold             (Yao et al.)
Reflexion         2+ calls: Actor → Evaluator → Reflector  (Shinn et al.)
Self-Consistency  N calls: different phrasings → vote      (Wang et al.)
ToT               3 calls: branch → evaluate → select      (Yao et al.)
GoT               5 calls: seed → gen → aggregate → synth  (Besta et al.)
PoT               1 call + exec: write Python → run it     (Chen et al.)
```

---

## 7. Running experiments

### Single bug, single baseline

```bash
python -m swe_agent.runner \
  --project Lang \
  --bug     1 \
  --baseline agentless \
  --out     outputs
```

### Single bug, compare all baselines

```bash
for bl in agentless swe_agent openhands openclaw claude_code \
          cot reflexion self_consistency tot got; do
  python -m swe_agent.runner \
    --project Lang --bug 1 --baseline $bl --out outputs
done
```

### Batch run

```bash
# Small benchmark (10 bugs)
python -m swe_agent.eval \
  --bugs     benchmarks/defects4j_small.txt \
  --baseline agentless cot reflexion tot got \
  --out      outputs

# Full benchmark
python -m swe_agent.eval \
  --bugs     benchmarks/defects4j_full.txt \
  --baseline agentless swe_agent openhands openclaw claude_code \
             cot reflexion self_consistency tot got \
  --out      outputs
```

### Inspect results

```bash
# Per-bug JSON
cat outputs/Lang-1/agentless/result.json

# LLM call log
cat outputs/Lang-1/agentless/llm_calls.jsonl

# Event trace
cat outputs/Lang-1/agentless/trace.jsonl

# Applied patch
cat outputs/Lang-1/agentless/patch.diff

# Batch summary
cat outputs/report.md
cat outputs/summary.csv
```

---

## 8. Output artefacts

Every `runner.py` run for `(bug, baseline)` produces:

```
outputs/{Project}-{ID}/{baseline}/
├── result.json            ← status, attempts, LLM usage, timing
├── trace.jsonl            ← per-phase events (apply, compile, test, …)
├── llm_calls.jsonl        ← every LLM call with timing, tokens, sha256
├── patch.diff             ← final accepted patch (only if repaired)
├── attempts/
│   ├── 001.patch.diff     ← patch from attempt 1
│   ├── 001.meta.json      ← metadata from attempt 1
│   └── ...
└── logs/
    ├── checkout.log
    ├── test_before.log
    ├── attempt_001_compile.log
    ├── attempt_001_func_test.log
    └── attempt_001_reg_test.log
```

`result.json` schema:

```json
{
  "bug":             "Lang_1",
  "baseline":        "agentless",
  "status":          "repaired",
  "attempts_used":   2,
  "attempt_summaries": [
    {"attempt": 1, "status": "BUILD_FAILED", "reason_code": "JAVAC_SYMBOL_NOT_FOUND"},
    {"attempt": 2, "status": "REPAIRED"}
  ],
  "failing_count_before": 1,
  "failing_count_after":  0,
  "time_sec": 38.4,
  "llm": {
    "calls": 3,
    "prompt_tokens": 4200,
    "completion_tokens": 820,
    "total_tokens": 5020,
    "latency_sec_total": 12.1
  },
  "verification_time_sec": {
    "apply_patch": 0.1,
    "compile":     6.2,
    "func_test":   8.9,
    "reg_test":   22.1,
    "total":       37.3
  }
}
```

Batch `eval.py` additionally writes:

| File | Contents |
|---|---|
| `outputs/report.md` | Human-readable repair summary per baseline + failure analysis |
| `outputs/summary.json` | Aggregated stats per baseline (repair rate, median tokens, median time) |
| `outputs/summary.csv` | Per-bug, per-baseline flat table for plotting |

---

## 9. Budget and safety constraints

All limits are defined in `config.py` and enforced by `BudgetManager` and `LLMClient`.

| Constraint | Default | Config key |
|---|---|---|
| Attempts per bug | 5 | `MAX_ATTEMPTS_PER_BUG` |
| LLM calls per attempt | 3 | `MAX_LLM_CALLS_PER_ATTEMPT` |
| LLM calls per bug total | 15 | `MAX_LLM_CALLS_PER_BUG` |
| Tokens per bug total | 200,000 | `MAX_TOKENS_PER_BUG` |
| Patch lines | 200 | `MAX_PATCH_LINES` |
| Files changed per patch | 2 | `MAX_FILES_CHANGED` |
| Source lines per location | 200 | `CONTEXT_LINES_PER_LOCATION` |
| Patch generation timeout | 60 s | `TIMEOUT_PATCH_GEN` |
| Compile timeout | 120 s | `TIMEOUT_COMPILE` |
| Trigger test timeout | 180 s | `TIMEOUT_FUNC_TEST` |
| Regression test timeout | 600 s | `TIMEOUT_REG_TEST` |

`self_consistency.py` automatically caps `n_samples` at
`MAX_LLM_CALLS_PER_ATTEMPT - 1` to leave room for the judge call.

---

## 10. Key invariants

These must never be violated across any baseline:

1. **One LLM client.** No baseline imports `openai` directly (except `function_calling.py`
   which needs the `tools=` parameter; extend `LLMClient.chat_with_tools()` to unify).
2. **Budget before every call and every patch apply.**
3. **Rollback after every failed attempt** — workspace must be clean before the next attempt.
4. **Pre-patch baseline run mandatory** — `test_before.log` must exist before any patching.
5. **Functionality gate before regression gate** — never run full suite if trigger tests still fail.
6. **All timing via `time.monotonic()`** — no `datetime` arithmetic for durations.
7. **`result.json` written even on error** — pipeline must not silently swallow exceptions.
8. **Problem folder is read-only** — `data/defects4j/` is never written by any baseline.
9. **`failing_tests` is ground truth** — parsed identically by all baselines via `_load_fail_info`.
10. **Bug name convention** — `Project_ID` in filesystem, `Project-ID` in D4J CLI.
11. **Stateful baselines (`reflexion`) are instantiated once per bug** — `GENERATORS[bl]()`
    is called fresh inside `run_bug()` for each bug.
12. **GoT synthesis must include the full graph summary** — passing only `fail_ctx`
    discards all graph reasoning.
13. **PoT repair script runs via subprocess** — never bare `exec()`.
14. **`_shared.py` is the single source of prompt helpers** — do not duplicate
    `PATCH_SYSTEM`, `extract_search_replace`, or context builders in baseline files.

---

## 11. Adding a new baseline

1. Create `swe_agent/patch_generators/my_baseline.py` implementing `PatchGenerator`:

```python
# swe_agent/patch_generators/my_baseline.py
from pathlib import Path
from .base import PatchGenerator, PatchResult
from ._shared import build_fail_context, build_location_context, \
                     extract_search_replace, PATCH_SYSTEM

class MyBaselinePatchGenerator(PatchGenerator):

    def generate_patch(
        self,
        bug_id, workdir, failing_info, trigger_tests,
        localization_hits, attempt_index, out_dir, llm_client,
    ) -> PatchResult:
        fail_ctx = build_fail_context(bug_id, trigger_tests, failing_info)
        loc_ctx  = build_location_context(localization_hits, workdir)

        # ... your prompt strategy ...

        response = llm_client.chat(
            [{"role": "system", "content": PATCH_SYSTEM},
             {"role": "user",   "content": f"{fail_ctx}\n{loc_ctx}"}],
            purpose="my_baseline", attempt=attempt_index,
            out_dir=out_dir, max_tokens=1500,
        )
        return PatchResult(
            diff_text=extract_search_replace(response or ""),
            metadata={"strategy": "my_baseline"},
        )
```

2. Register in `config.py`:

```python
BASELINES_PROMPTING = [..., "my_baseline"]
```

3. Register in `runner.py`:

```python
from .patch_generators.my_baseline import MyBaselinePatchGenerator

GENERATORS = {
    ...,
    "my_baseline": MyBaselinePatchGenerator,
}
```

4. Run:

```bash
python -m swe_agent.runner \
  --project Lang --bug 1 --baseline my_baseline --out outputs
```

---

## 12. Claude Code guidance

This repository ships with a `.claude/` folder for Claude Code:

```
.claude/
├── guidance.md   ← full implementation spec for all 15 baselines
│                   (sections 0-16, implementation order, key invariants)
└── tutorial.md   ← prompting baselines tutorial with verified paper citations
                    and full PoC code for all 10 prompting strategies
```

When using Claude Code to implement or extend this project:

- **Read `.claude/guidance.md` first.** It contains the complete module-by-module
  contract and a strict implementation order (steps 1–31) to avoid import errors.
- **Read `.claude/tutorial.md` for any prompting baseline.** It contains the full
  OOP implementation with design principles, paper citations, and working PoC code.
- Implement modules in the order specified in `guidance.md` Section 13.
- All new baselines must use `_shared.py` — never duplicate `PATCH_SYSTEM` or
  `extract_search_replace`.

---

## 13. References

| Paper | Authors | Venue | arXiv |
|---|---|---|---|
| Chain-of-Thought Prompting Elicits Reasoning in LLMs | Wei et al. | NeurIPS 2022 | 2201.11903 |
| Large Language Models are Zero-Shot Reasoners | Kojima et al. | NeurIPS 2022 | 2205.11916 |
| Self-Consistency Improves CoT Reasoning in LMs | Wang et al. | ICLR 2023 | 2203.11171 |
| ReAct: Synergizing Reasoning and Acting in LMs | Yao et al. | ICLR 2023 | 2210.03629 |
| Reflexion: Language Agents with Verbal RL | Shinn et al. | NeurIPS 2023 | 2303.11366 |
| Tree of Thoughts: Deliberate Problem Solving with LLMs | Yao et al. | NeurIPS 2023 | 2305.10601 |
| Program of Thoughts Prompting | Chen et al. | TMLR 2023 | 2211.12588 |
| PAL: Program-aided Language Models | Gao et al. | ICML 2023 | 2211.10435 |
| Toolformer: LMs Can Teach Themselves to Use Tools | Schick et al. | NeurIPS 2023 | 2302.04761 |
| Graph of Thoughts: Solving Elaborate Problems with LLMs | Besta et al. | AAAI 2024 | 2308.09687 |
| Defects4J: A Database of Existing Faults to Enable Studies | Just et al. | ISSTA 2014 | — |
| The Prompt Report: A Systematic Survey of PE Techniques | Schulhoff et al. | 2024 | 2406.06608 |


[submodule "Agentless"]
        path = Agentless
        url = https://github.com/OpenAutoCoder/Agentless.git
[submodule "HyperAgent"]
        path = HyperAgent
        url = https://github.com/FSoft-AI4Code/HyperAgent.git
[submodule "RepairAgent"]
        path = RepairAgent
        url = https://github.com/sola-st/RepairAgent
[submodule "SWE-agent"]
        path = SWE-agent
        url = https://github.com/SWE-agent/SWE-agent
