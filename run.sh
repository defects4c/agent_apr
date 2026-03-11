#!/usr/bin/env bash
# =============================================================================
# run.sh - Defects4J Agent-Based APR - All Baseline Commands
# =============================================================================
# This script contains commands to run each of the 16 baselines separately.
#
# Usage:
#   ./run.sh                           # Run all baselines sequentially
#   ./run.sh agentless                 # Run only agentless baseline
#   ./run.sh --llm_verbose             # Run with LLM verbose output
#   ./run.sh --patch_verbose           # Run with patch/verification verbose
#   ./run.sh claude_code --llm_verbose # Run specific baseline with verbose
#
# Environment Setup (modify as needed):
# =============================================================================

set -euo pipefail

# ── Environment Variables ──────────────────────────────────────────────────
export D4J_HOME="${D4J_HOME:-/opt/defects4j}"
export D4J_FOLDER="${D4J_FOLDER:-data/defects4j}"
export REPOS_DIR="${REPOS_DIR:-data/repos}"
export OPENAI_API_KEY="${OPENAI_API_KEY:-dummy}"
export OPENAI_API_BASE_URL="${OPENAI_API_BASE_URL:-http://157.10.162.82:443/v1/}"
export GPT_MODEL="${GPT_MODEL:-gpt-5.1}"
export JAVA8_HOME="${JAVA8_HOME:-/usr/lib/jvm/java-8-openjdk-amd64}"
export MAX_ATTEMPTS="${MAX_ATTEMPTS:-5}"

# ── Configuration ──────────────────────────────────────────────────────────
BENCHMARK_FILE="benchmarks/defects4j_small.txt"
OUTPUT_DIR="outputs_full"

# ── Parse flags ────────────────────────────────────────────────────────────
VERBOSE_FLAGS=""
BASELINE_TO_RUN=""

for arg in "$@"; do
    case $arg in
        --llm_verbose)
            VERBOSE_FLAGS="$VERBOSE_FLAGS --llm_verbose"
            ;;
        --patch_verbose)
            VERBOSE_FLAGS="$VERBOSE_FLAGS --patch_verbose"
            ;;
        --verbose)
            VERBOSE_FLAGS="$VERBOSE_FLAGS --llm_verbose --patch_verbose"
            ;;
        *)
            BASELINE_TO_RUN="$arg"
            ;;
    esac
done

# =============================================================================
# AGENT BASELINES (5 baselines)
# =============================================================================

run_agentless() {
    echo ">>> Running: agentless"
    python -m swe_agent.eval \
        --bugs "$BENCHMARK_FILE" \
        --baseline agentless \
        --out "$OUTPUT_DIR" $VERBOSE_FLAGS
}

run_swe_agent() {
    echo ">>> Running: swe_agent"
    python -m swe_agent.eval \
        --bugs "$BENCHMARK_FILE" \
        --baseline swe_agent \
        --out "$OUTPUT_DIR" $VERBOSE_FLAGS
}

run_openhands() {
    echo ">>> Running: openhands"
    python -m swe_agent.eval \
        --bugs "$BENCHMARK_FILE" \
        --baseline openhands \
        --out "$OUTPUT_DIR" $VERBOSE_FLAGS
}

run_openclaw() {
    echo ">>> Running: openclaw"
    python -m swe_agent.eval \
        --bugs "$BENCHMARK_FILE" \
        --baseline openclaw \
        --out "$OUTPUT_DIR" $VERBOSE_FLAGS
}

run_claude_code() {
    echo ">>> Running: claude_code"
    python -m swe_agent.eval \
        --bugs "$BENCHMARK_FILE" \
        --baseline claude_code \
        --out "$OUTPUT_DIR" $VERBOSE_FLAGS
}

# =============================================================================
# PROMPTING BASELINES (11 baselines)
# =============================================================================

run_standard() {
    echo ">>> Running: standard (direct prompting - control baseline)"
    python -m swe_agent.eval \
        --bugs "$BENCHMARK_FILE" \
        --baseline standard \
        --out "$OUTPUT_DIR" $VERBOSE_FLAGS
}

run_cot() {
    echo ">>> Running: cot (Chain-of-Thought)"
    python -m swe_agent.eval \
        --bugs "$BENCHMARK_FILE" \
        --baseline cot \
        --out "$OUTPUT_DIR" $VERBOSE_FLAGS
}

run_zero_shot_cot() {
    echo ">>> Running: zero_shot_cot (Kojima et al. NeurIPS 2022)"
    python -m swe_agent.eval \
        --bugs "$BENCHMARK_FILE" \
        --baseline zero_shot_cot \
        --out "$OUTPUT_DIR" $VERBOSE_FLAGS
}

run_few_shot_cot() {
    echo ">>> Running: few_shot_cot (Wei et al. NeurIPS 2022)"
    python -m swe_agent.eval \
        --bugs "$BENCHMARK_FILE" \
        --baseline few_shot_cot \
        --out "$OUTPUT_DIR" $VERBOSE_FLAGS
}

run_react() {
    echo ">>> Running: react (Yao et al. ICLR 2023)"
    python -m swe_agent.eval \
        --bugs "$BENCHMARK_FILE" \
        --baseline react \
        --out "$OUTPUT_DIR" $VERBOSE_FLAGS
}

run_reflexion() {
    echo ">>> Running: reflexion (Shinn et al. NeurIPS 2023)"
    python -m swe_agent.eval \
        --bugs "$BENCHMARK_FILE" \
        --baseline reflexion \
        --out "$OUTPUT_DIR" $VERBOSE_FLAGS
}

run_self_consistency() {
    echo ">>> Running: self_consistency (Wang et al. ICLR 2023)"
    python -m swe_agent.eval \
        --bugs "$BENCHMARK_FILE" \
        --baseline self_consistency \
        --out "$OUTPUT_DIR" $VERBOSE_FLAGS
}

run_tot() {
    echo ">>> Running: tot (Tree of Thoughts - Yao et al. NeurIPS 2023)"
    python -m swe_agent.eval \
        --bugs "$BENCHMARK_FILE" \
        --baseline tot \
        --out "$OUTPUT_DIR" $VERBOSE_FLAGS
}

run_got() {
    echo ">>> Running: got (Graph of Thoughts - Besta et al. AAAI 2024)"
    python -m swe_agent.eval \
        --bugs "$BENCHMARK_FILE" \
        --baseline got \
        --out "$OUTPUT_DIR" $VERBOSE_FLAGS
}

run_pot() {
    echo ">>> Running: pot (Program of Thoughts - Chen et al. TMLR 2023)"
    python -m swe_agent.eval \
        --bugs "$BENCHMARK_FILE" \
        --baseline pot \
        --out "$OUTPUT_DIR" $VERBOSE_FLAGS
}

run_function_calling() {
    echo ">>> Running: function_calling (OpenAI API - structured tool use)"
    python -m swe_agent.eval \
        --bugs "$BENCHMARK_FILE" \
        --baseline function_calling \
        --out "$OUTPUT_DIR" $VERBOSE_FLAGS
}

# =============================================================================
# MAIN EXECUTION
# =============================================================================

echo "=============================================="
echo "Defects4J Agent-Based APR"
echo "=============================================="
echo "Benchmark: $BENCHMARK_FILE"
echo "Output:    $OUTPUT_DIR"
echo "Baselines: 16 total (5 agent + 11 prompting)"
if [[ -n "$VERBOSE_FLAGS" ]]; then
    echo "Verbose:  $VERBOSE_FLAGS"
fi
echo ""

if [[ -n "$BASELINE_TO_RUN" ]]; then
    # Run specific baseline
    case "$BASELINE_TO_RUN" in
        agentless)        run_agentless ;;
        swe_agent)        run_swe_agent ;;
        openhands)        run_openhands ;;
        openclaw)         run_openclaw ;;
        claude_code)      run_claude_code ;;
        standard)         run_standard ;;
        cot)              run_cot ;;
        zero_shot_cot)    run_zero_shot_cot ;;
        few_shot_cot)     run_few_shot_cot ;;
        react)            run_react ;;
        reflexion)        run_reflexion ;;
        self_consistency) run_self_consistency ;;
        tot)              run_tot ;;
        got)              run_got ;;
        pot)              run_pot ;;
        function_calling) run_function_calling ;;
        --llm_verbose|--patch_verbose|--verbose)
            echo "Error: Verbose flags must be combined with a baseline name"
            echo "Usage: ./run.sh [baseline] [--llm_verbose] [--patch_verbose]"
            exit 1
            ;;
        *)
            echo "Unknown baseline: $BASELINE_TO_RUN"
            echo "Available baselines: agentless, swe_agent, openhands, openclaw, claude_code,"
            echo "                     standard, cot, zero_shot_cot, few_shot_cot, react,"
            echo "                     reflexion, self_consistency, tot, got, pot, function_calling"
            exit 1
            ;;
    esac
else
    # Run all baselines sequentially

    echo "=== AGENT BASELINES (5) ==="
    echo ""
    run_agentless
    echo ""
    run_swe_agent
    echo ""
    run_openhands
    echo ""
    run_openclaw
    echo ""
    run_claude_code
    echo ""

    echo "=== PROMPTING BASELINES (11) ==="
    echo ""
    run_standard
    echo ""
    run_cot
    echo ""
    run_zero_shot_cot
    echo ""
    run_few_shot_cot
    echo ""
    run_react
    echo ""
    run_reflexion
    echo ""
    run_self_consistency
    echo ""
    run_tot
    echo ""
    run_got
    echo ""
    run_pot
    echo ""
    run_function_calling
    echo ""
fi

echo "=============================================="
echo "All baselines complete!"
echo "Results: $OUTPUT_DIR/"
echo "=============================================="
