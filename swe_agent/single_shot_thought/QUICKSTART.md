# SWE-Agent Quick Start Guide

## Overview

SWE-Agent is a multi-baseline agent-based APR (Automatic Program Repair) system for Defects4J.
It implements 5 different repair strategies:

1. **agentless** - Direct patch generation from failing info (1-2 LLM calls)
2. **swe_agent** - ReAct loop with file tools (read_file, search, submit_patch)
3. **openhands** - Budgeted tool-use loop with context limits
4. **openclaw** - Structured 3-call pipeline (Search → Analyze → Patch)
5. **claude_code** - Skill-based read/search/propose loop

## Environment Setup

```bash
# Set required environment variables
export D4J_HOME="/opt/defects4j"           # Defects4J installation path
export D4J_FOLDER="data/defects4j"          # Problem data folder
export REPOS_DIR="data/repos"               # Repository checkout directory
export OPENAI_API_KEY="your-api-key"        # LLM API key
export OPENAI_API_BASE_URL="http://host:port/v1/"  # LLM endpoint
export GPT_MODEL="gpt-5.1"                  # Model name
export JAVA8_HOME="/usr/lib/jvm/java-8-openjdk-amd64"  # Java 8 path
```

## Usage

### Single Bug Repair

```bash
# Run single bug with agentless baseline
python -m swe_agent.runner \
    --project Math \
    --bug 1 \
    --baseline agentless \
    --out outputs

# Run with different baselines
for baseline in agentless swe_agent openhands openclaw claude_code; do
    python -m swe_agent.runner \
        --project Math --bug 1 \
        --baseline $baseline \
        --out outputs
done
```

### Batch Evaluation

```bash
# Run evaluation on multiple bugs
python -m swe_agent.eval \
    --bugs benchmarks/defects4j_small.txt \
    --baseline agentless swe_agent openclaw \
    --out outputs
```

## Output Structure

```
outputs/
└── Math-1/
    └── agentless/
        ├── result.json           # Final result with status
        ├── trace.jsonl           # Execution trace
        ├── llm_calls.jsonl       # LLM call logs
        ├── patch.diff            # Final patch (if repaired)
        ├── logs/
        │   ├── checkout.log
        │   ├── test_before.log
        │   └── attempt_001_*.log
        └── attempts/
            ├── 001.patch.diff
            └── 001.meta.json
```

## Result Format

```json
{
  "bug": "Math_1",
  "baseline": "agentless",
  "status": "repaired",
  "attempts_used": 2,
  "failing_count_before": 2,
  "failing_count_after": 0,
  "time_sec": 120.5,
  "llm": {
    "calls": 5,
    "total_tokens": 12000,
    "latency_sec_total": 45.2
  },
  "artifacts": {
    "trace": "trace.jsonl",
    "final_patch": "patch.diff"
  }
}
```

## Configuration

Key configuration options in `config.py`:

| Parameter | Default | Description |
|-----------|---------|-------------|
| MAX_ATTEMPTS_PER_BUG | 5 | Maximum repair attempts per bug |
| MAX_LLM_CALLS_PER_ATTEMPT | 3 | LLM calls per attempt |
| MAX_LLM_CALLS_PER_BUG | 15 | Total LLM calls budget |
| MAX_TOKENS_PER_BUG | 200000 | Token budget per bug |
| MAX_PATCH_LINES | 200 | Maximum patch size |
| MAX_FILES_CHANGED | 2 | Maximum files in patch |

## Data Format

### snippet.json (per buggy method)
```json
{
  "name": "org.apache.commons.lang3.math.NumberUtils.createNumber",
  "file": "src/main/java/org/apache/commons/lang3/math/NumberUtils.java",
  "begin_line": 442,
  "end_line": 540,
  "snippet": "...",
  "is_bug": true
}
```

### failing_tests format
```
--- org.apache.commons.lang3.math.NumberUtilsTest::testLang300
java.lang.StringIndexOutOfBoundsException: String index out of range: 0
	at org.apache.commons.lang3.math.NumberUtils.createNumber(NumberUtils.java:455)
```

## Data Preparation

Before running the APR system, you need to prepare the problem data folder:

```bash
# Prepare data for a single bug
python -m swe_agent.prepare_data \
    --project Lang \
    --bug 1 \
    --d4j-home /opt/defects4j

# Prepare data for multiple bugs
python -m swe_agent.prepare_data \
    --batch benchmarks/defects4j_small.txt \
    --d4j-home /opt/defects4j
```

This creates the required files in `data/defects4j/{Project}_{BugId}/`:
- `failing_tests` - Raw D4J test failure output
- `snippet.json` - Buggy method snippets with is_bug flags
- `test_snippet.json` - Test case snippets with metadata

## Troubleshooting

1. **Checkout fails**: Ensure D4J_HOME points to valid Defects4J installation
2. **Compilation fails**: Check JAVA8_HOME points to Java 8 JDK
3. **LLM calls fail**: Verify OPENAI_API_KEY and OPENAI_API_BASE_URL are correct
4. **Patch apply fails**: Ensure git is initialized in the working directory
5. **No failing tests**: Run data preparation script first to populate the data folder
