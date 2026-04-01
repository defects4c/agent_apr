#!/bin/bash
# ═══════════════════════════════════════════════════════════════════
# test_single_shot.sh — Quick test for the fixed single_shot_thought
# ═══════════════════════════════════════════════════════════════════
#
# Prerequisites:
#   1. Docker containers running (the defects4j webapp):
#      cd defects4j_docker_web && docker compose up -d
#   2. Environment variables:
#      export D4J_LOCAL_WORKSPACE=/home/taicen/wangjian/defects4c_dirs/defects4j_docker_web/d4j-workspace
#      export D4J_CONTAINER_WORKSPACE=/workspace
#      export D4J_URL=http://127.0.0.1:8091   # or 8090 for the other cluster
#      export OPENAI_API_KEY=your_key
#      export OPENAI_API_BASE_URL=https://api.ai2wj.com/v1/
#      export GPT_MODEL=gpt-5.1
#
# ═══════════════════════════════════════════════════════════════════
set -euo pipefail

echo "=== 1. Health check ==="
curl -s "${D4J_URL:-http://127.0.0.1:8091}/health" && echo " ✓ Docker webapp OK" || { echo " ✗ Docker webapp not reachable"; exit 1; }

echo ""
echo "=== 2. Single bug, verbose (Chart:1, react, FL=llm) ==="
echo "This should show:"
echo "  - Failure info FROM Docker container (not 'No failure information')"
echo "  - FL locations FROM defects4j export (not 'No specific code locations')"
echo "  - Patch content for each attempt"
echo "  - Compile/test status with error details"
echo ""
echo "Running..."
python -m single_shot_thought runner \
    --project Chart --bug 1 \
    --baseline react \
    --fl-mode llm \
    --max-attempts 3 \
    --out outputs \
    --patch_verbose --llm_verbose

echo ""
echo "=== 3. Structured CoT (quick, 1 LLM call per attempt) ==="
python -m single_shot_thought runner \
    --project Chart --bug 1 \
    --baseline cot \
    --fl-mode llm \
    --max-attempts 5 \
    --out outputs \
    --patch_verbose

echo ""
echo "=== 4. Batch test (2 bugs × 2 baselines) ==="
cat > /tmp/test_bugs.txt << 'EOF'
Chart_1
Lang_1
EOF

python -m single_shot_thought eval \
    --bugs /tmp/test_bugs.txt \
    --baseline cot react \
    --fl-mode llm \
    --max-attempts 3 \
    --out outputs \
    --patch_verbose

echo ""
echo "=== Done ==="
echo "Check outputs/ for results"
