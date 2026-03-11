#!/usr/bin/env bash
# run_all_separate.sh - Run each baseline separately on Defects4J benchmarks
# Each baseline runs independently and can be executed individually
#
# Usage:
#   ./run_all_separate.sh              # Run all baselines sequentially
#   ./run_all_separate.sh agentless    # Run only agentless baseline
#
# Environment variables (set defaults if not provided):
#   D4J_HOME, OPENAI_API_KEY, OPENAI_API_BASE_URL, GPT_MODEL, JAVA8_HOME

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

# ── Configuration ──────────────────────────────────────────────────────────
BENCHMARK_FILE="${1:-benchmarks/defects4j_small.txt}"
OUTPUT_DIR="${2:-outputs_full}"
SPECIFIC_BASELINE="${3:-}"

# ── Helper function to run a single baseline ───────────────────────────────
run_baseline() {
    local baseline="$1"
    echo "=============================================="
    echo "Running: $baseline"
    echo "=============================================="
    python -m swe_agent.eval \
        --bugs "$BENCHMARK_FILE" \
        --baseline "$baseline" \
        --out "$OUTPUT_DIR"
    echo ""
}

# ── Agent Baselines (5) ────────────────────────────────────────────────────
run_agentless() {
    run_baseline "agentless"
}

run_swe_agent() {
    run_baseline "swe_agent"
}

run_openhands() {
    run_baseline "openhands"
}

run_openclaw() {
    run_baseline "openclaw"
}

run_claude_code() {
    run_baseline "claude_code"
}

# ── Prompting Baselines (11) ───────────────────────────────────────────────
run_standard() {
    run_baseline "standard"
}

run_zero_shot_cot() {
    run_baseline "zero_shot_cot"
}

run_few_shot_cot() {
    run_baseline "few_shot_cot"
}

run_react() {
    run_baseline "react"
}

run_reflexion() {
    run_baseline "reflexion"
}

run_self_consistency() {
    run_baseline "self_consistency"
}

run_tot() {
    run_baseline "tot"
}

run_got() {
    run_baseline "got"
}

run_pot() {
    run_baseline "pot"
}

run_function_calling() {
    run_baseline "function_calling"
}

run_cot() {
    run_baseline "cot"
}

# ── Main execution ─────────────────────────────────────────────────────────
echo "=============================================="
echo "Defects4J Agent-Based APR - All Baselines"
echo "=============================================="
echo "Benchmark: $BENCHMARK_FILE"
echo "Output:    $OUTPUT_DIR"
echo ""

if [[ -n "$SPECIFIC_BASELINE" ]]; then
    # Run specific baseline if provided
    echo "Running single baseline: $SPECIFIC_BASELINE"
    run_baseline "$SPECIFIC_BASELINE"
else
    # Run all baselines sequentially

    echo ">>> Running Agent Baselines (5)..."
    echo ""
    run_agentless
    run_swe_agent
    run_openhands
    run_openclaw
    run_claude_code

    echo ">>> Running Prompting Baselines (11)..."
    echo ""
    run_standard
    run_zero_shot_cot
    run_few_shot_cot
    run_react
    run_reflexion
    run_self_consistency
    run_tot
    run_got
    run_pot
    run_function_calling
    run_cot
fi

echo "=============================================="
echo "All baselines complete!"
echo "Results: $OUTPUT_DIR/"
echo "=============================================="
