#!/usr/bin/env bash
# run_baselines.sh - Run individual baselines on Defects4J benchmarks
# Usage: ./run_baselines.sh <baseline_name> [benchmark_file] [output_dir]
#
# Available baselines:
#   Agent baselines: agentless, swe_agent, openhands, openclaw, claude_code
#   Prompting baselines: standard, zero_shot_cot, few_shot_cot, react,
#                        reflexion, self_consistency, tot, got, pot,
#                        function_calling, cot

set -euo pipefail

# ── Environment Setup ──────────────────────────────────────────────────────
export D4J_HOME="${D4J_HOME:-/opt/defects4j}"
export D4J_FOLDER="${D4J_FOLDER:-data/defects4j}"
export REPOS_DIR="${REPOS_DIR:-data/repos}"
export OPENAI_API_KEY="${OPENAI_API_KEY:-dummy}"
export OPENAI_API_BASE_URL="${OPENAI_API_BASE_URL:-http://157.10.162.82:443/v1/}"
export GPT_MODEL="${GPT_MODEL:-gpt-5.1}"
export JAVA8_HOME="${JAVA8_HOME:-/usr/lib/jvm/java-8-openjdk-amd64}"
export MAX_ATTEMPTS="${MAX_ATTEMPTS:-5}"

# ── Arguments ──────────────────────────────────────────────────────────────
BASELINE="${1:-agentless}"
BENCHMARK_FILE="${2:-benchmarks/defects4j_small.txt}"
OUTPUT_DIR="${3:-outputs}"

echo "=============================================="
echo "Baseline: $BASELINE"
echo "Benchmark: $BENCHMARK_FILE"
echo "Output: $OUTPUT_DIR"
echo "=============================================="

python -m swe_agent.eval \
    --bugs "$BENCHMARK_FILE" \
    --baseline "$BASELINE" \
    --out "$OUTPUT_DIR"

echo "Done! Results in $OUTPUT_DIR/"
