#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════
# run_parallel.sh — Run single_shot_thought on bugs in parallel
#
# Usage:
#   bash run_parallel.sh bugs_114.txt 8 cot outputs llm 30
#   bash run_parallel.sh bugs_114.txt 8 "cot react tot" outputs llm 30
#   bash run_parallel.sh bugs_114.txt 4 "standard cot zero_shot_cot few_shot_cot react reflexion self_consistency tot got pot function_calling" outputs llm 30
#
# Features:
#   - Auto-resume: skips bugs with existing result.json (repaired/unrepaired)
#   - Live summary: prints cumulative stats after each bug completes
#   - Per-bug logs: outputs/{Project}-{Bug}_{baseline}.log
# ═══════════════════════════════════════════════════════════════════
set -uo pipefail

BUGS_FILE="${1:?Usage: $0 <bugs_file> <parallel> <baselines> [out_dir] [fl_mode] [max_attempts]}"
PARALLEL="${2:-8}"
BASELINES="${3:-cot}"
OUT_DIR="${4:-outputs}"
FL_MODE="${5:-llm}"
MAX_ATTEMPTS="${6:-30}"

[ ! -f "$BUGS_FILE" ] && { echo "ERROR: $BUGS_FILE not found"; exit 1; }

TOTAL_BUGS=$(grep -cve '^\s*#' -e '^\s*$' "$BUGS_FILE")
N_BASELINES=$(echo "$BASELINES" | wc -w)
TOTAL_JOBS=$((TOTAL_BUGS * N_BASELINES))

echo "═══════════════════════════════════════════════════════════════"
echo "  single_shot_thought parallel batch"
echo "  Bugs: $TOTAL_BUGS × Baselines: $N_BASELINES = $TOTAL_JOBS jobs"
echo "  Parallel: $PARALLEL | FL: $FL_MODE | pass@$MAX_ATTEMPTS"
echo "  Output: $OUT_DIR"
echo "═══════════════════════════════════════════════════════════════"
mkdir -p "$OUT_DIR"

# Summary file (atomically updated by each worker)
SUMMARY_FILE="$OUT_DIR/.live_summary.lock"
touch "$SUMMARY_FILE"

# ── Per-job wrapper ──
_RUN_ONE=$(mktemp /tmp/run_one_XXXXXX.sh)
cat > "$_RUN_ONE" << 'INNER_EOF'
#!/usr/bin/env bash
set -uo pipefail
BUG_LINE="$1"; BASELINES="$2"; OUT_DIR="$3"; FL_MODE="$4"; MAX_ATTEMPTS="$5"

BUG_LINE=$(echo "$BUG_LINE" | tr -d '[:space:]')
[ -z "$BUG_LINE" ] && exit 0
[[ "$BUG_LINE" == \#* ]] && exit 0

# Parse "Chart_1" or "Chart-1"
if [[ "$BUG_LINE" == *_* ]]; then
    PROJECT="${BUG_LINE%%_*}"; BUG_ID="${BUG_LINE#*_}"
elif [[ "$BUG_LINE" == *-* ]]; then
    PROJECT="${BUG_LINE%%-*}"; BUG_ID="${BUG_LINE#*-}"
else
    exit 0
fi

for BASELINE in $BASELINES; do
    RESULT_FILE="$OUT_DIR/${PROJECT}-${BUG_ID}/${BASELINE}/result.json"
    LOG_FILE="$OUT_DIR/${PROJECT}-${BUG_ID}_${BASELINE}.log"

    # ── Resume: skip if terminal ──
    if [ -f "$RESULT_FILE" ]; then
        STATUS=$(python3 -c "
import json, sys
try:
    r = json.load(open('$RESULT_FILE'))
    print(r.get('status',''))
except: print('')
" 2>/dev/null)
        if [ "$STATUS" = "repaired" ] || [ "$STATUS" = "unrepaired" ]; then
            echo "[SKIP] ${PROJECT}-${BUG_ID}/${BASELINE}: $STATUS"
            # Still print summary
            python3 -c "
import json, os, glob
results = []
for f in glob.glob('$OUT_DIR/*/*/result.json'):
    try: results.append(json.load(open(f)))
    except: pass
total = len(results)
repaired = sum(1 for r in results if r.get('status')=='repaired')
unrepaired = sum(1 for r in results if r.get('status')=='unrepaired')
errors = total - repaired - unrepaired
print(f'  ── {total} done | repaired={repaired} unrepaired={unrepaired} error={errors} ──')
" 2>/dev/null
            continue
        fi
    fi

    echo "[START] ${PROJECT}-${BUG_ID}/${BASELINE}"

    # ── Run with retries on crash ──
    MAX_CRASH_RETRIES=3
    for RETRY in $(seq 1 $MAX_CRASH_RETRIES); do
        python -m single_shot_thought runner \
            --project "$PROJECT" --bug "$BUG_ID" \
            --baseline "$BASELINE" \
            --fl-mode "$FL_MODE" \
            --max-attempts "$MAX_ATTEMPTS" \
            --out "$OUT_DIR" \
            --patch_verbose \
            > "$LOG_FILE" 2>&1
        EXIT_CODE=$?

        if [ $EXIT_CODE -eq 0 ]; then
            break
        fi

        # Check if it's a retryable crash (502, connection error)
        if grep -qiE "502|503|504|Bad Gateway|Connection|InternalServerError" "$LOG_FILE" 2>/dev/null; then
            if [ $RETRY -lt $MAX_CRASH_RETRIES ]; then
                WAIT=$((30 * RETRY))
                echo "[RETRY] ${PROJECT}-${BUG_ID}/${BASELINE}: crash retry $RETRY/$MAX_CRASH_RETRIES (wait ${WAIT}s)"
                sleep $WAIT
                continue
            fi
        fi
        break  # non-retryable error
    done

    # ── Report result ──
    if [ -f "$RESULT_FILE" ]; then
        STATUS=$(python3 -c "import json; print(json.load(open('$RESULT_FILE')).get('status','error'))" 2>/dev/null || echo "error")
    else
        STATUS="crash"
    fi

    case "$STATUS" in
        repaired)   echo "[DONE] ${PROJECT}-${BUG_ID}/${BASELINE}: ★ REPAIRED" ;;
        unrepaired) echo "[DONE] ${PROJECT}-${BUG_ID}/${BASELINE}: unrepaired" ;;
        *)          echo "[FAIL] ${PROJECT}-${BUG_ID}/${BASELINE}: $STATUS" ;;
    esac

    # ── Live cumulative summary ──
    python3 -c "
import json, glob
results = []
for f in glob.glob('$OUT_DIR/*/*/result.json'):
    try: results.append(json.load(open(f)))
    except: pass
total = len(results)
repaired = sum(1 for r in results if r.get('status')=='repaired')
unrepaired = sum(1 for r in results if r.get('status')=='unrepaired')
errors = total - repaired - unrepaired
rate = repaired/total*100 if total else 0
print(f'  ── {total} done | repaired={repaired} ({rate:.1f}%) unrepaired={unrepaired} error={errors} ──')
" 2>/dev/null

done
INNER_EOF
chmod +x "$_RUN_ONE"

# ── Launch parallel ──
grep -ve '^\s*#' -e '^\s*$' "$BUGS_FILE" | shuf | \
    xargs -I {} -P "$PARALLEL" bash "$_RUN_ONE" {} "$BASELINES" "$OUT_DIR" "$FL_MODE" "$MAX_ATTEMPTS"

rm -f "$_RUN_ONE"

# ── Final Summary ──
echo ""
echo "═══════════════════════════════════════════════════════════════"
echo "  FINAL RESULTS"
echo "═══════════════════════════════════════════════════════════════"
python3 << PYEOF
import json, glob, os
from collections import Counter, defaultdict

results = []
for f in sorted(glob.glob("$OUT_DIR/*/*/result.json")):
    try:
        r = json.load(open(f))
        r["_file"] = f
        results.append(r)
    except:
        pass

if not results:
    print("  No results found!")
    exit()

# Per-baseline summary
by_baseline = defaultdict(list)
for r in results:
    by_baseline[r.get("baseline", "?")].append(r)

print(f"\n  {'Baseline':<22s} {'Total':>6s} {'Fixed':>6s} {'Rate':>7s} {'Tokens':>10s}")
print(f"  {'-'*22} {'-'*6} {'-'*6} {'-'*7} {'-'*10}")
for bl in sorted(by_baseline):
    rs = by_baseline[bl]
    total = len(rs)
    repaired = sum(1 for r in rs if r.get("status") == "repaired")
    rate = repaired / total * 100 if total else 0
    tokens = sum(r.get("llm", {}).get("total_tokens", 0) for r in rs)
    print(f"  {bl:<22s} {total:>6d} {repaired:>6d} {rate:>6.1f}% {tokens:>10,d}")

total = len(results)
repaired = sum(1 for r in results if r.get("status") == "repaired")
rate = repaired / total * 100 if total else 0
print(f"  {'─'*55}")
print(f"  {'TOTAL':<22s} {total:>6d} {repaired:>6d} {rate:>6.1f}%")

# Failure breakdown
print(f"\n  Failure reasons:")
reasons = Counter()
for r in results:
    if r.get("status") != "repaired":
        summaries = r.get("attempt_summaries", [])
        cats = Counter()
        for s in summaries:
            st = s.get("status", "")
            if "EMPTY" in st: cats["empty_patch"] += 1
            elif "APPLY" in st: cats["patch_apply_fail"] += 1
            elif "BUILD" in st: cats["compile_fail"] += 1
            elif "FUNCTIONALITY" in st: cats["func_test_fail"] += 1
            elif "REGRESSION" in st: cats["regression"] += 1
            elif "VISIBLE" in st: cats["docker_not_visible"] += 1
            elif "REPAIRED" in st: pass
            elif st: cats[st] += 1
        if cats:
            reasons[cats.most_common(1)[0][0]] += 1
        else:
            reasons["unknown"] += 1

for reason, count in reasons.most_common():
    print(f"    {reason:<25s} {count:>4d}")

# Repaired bugs list
repaired_bugs = [r.get("bug","") for r in results if r.get("status") == "repaired"]
if repaired_bugs:
    print(f"\n  Repaired bugs ({len(repaired_bugs)}):")
    for b in sorted(set(repaired_bugs)):
        baselines = [r.get("baseline") for r in results if r.get("bug")==b and r.get("status")=="repaired"]
        print(f"    {b}: {', '.join(baselines)}")
PYEOF
echo "═══════════════════════════════════════════════════════════════"

