#!/usr/bin/env python3
"""
analyze_results.py — Analysis for single_shot_thought APR experiments.

Supports:
  - Single experiment analysis with failure root-cause breakdown
  - Cross-baseline comparison (cot vs react vs tot etc.)
  - Cross-FL-mode comparison (oracle vs stack vs llm)
  - Comparison with sweagent_selfcontainedqwen results
  - pass@k curve generation

Usage:
    # Analyze single run
    python analyze_results.py --dir outputs/gpt-5.1_k30_cot_oracle_bugs_114

    # Compare baselines
    python analyze_results.py --cross-compare outputs/

    # Compare with sweagent
    python analyze_results.py --compare-sweagent \
        --single-shot-dir outputs/gpt-5.1_k30_cot_oracle_bugs_114 \
        --sweagent-dir d4j_results/openai_gpt-oss-120b_c30_direct_bugs_114

    # pass@k curves from attempt_summaries
    python analyze_results.py --pass-at-k outputs/gpt-5.1_k30_cot_oracle_bugs_114
"""

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import List, Dict, Optional


# ═══════════════════════════════════════════════════════════════════
#  Loaders
# ═══════════════════════════════════════════════════════════════════

def load_single_shot_results(result_dir: str) -> List[dict]:
    """Load from single_shot_thought output: {dir}/{Project}-{Bug}/{baseline}/result.json
    or {dir}/all_results.json."""
    results = []
    base = Path(result_dir)

    # Try all_results.json first
    ar = base / "all_results.json"
    if ar.exists():
        try:
            data = json.loads(ar.read_text())
            for r in data:
                r["_source"] = str(ar)
            return data
        except Exception:
            pass

    # Walk per-bug directories
    for rf in sorted(base.rglob("result.json")):
        try:
            data = json.loads(rf.read_text())
            data["_source"] = str(rf)
            # Infer baseline from path: .../baseline/result.json
            if rf.parent.name not in ("outputs",):
                data.setdefault("baseline", rf.parent.name)
            results.append(data)
        except (json.JSONDecodeError, OSError):
            pass
    return results


def load_sweagent_results(result_dir: str) -> List[dict]:
    """Load from sweagent output: {dir}/*_all_results.json or per-instance."""
    base = Path(result_dir)
    for f in base.glob("*_all_results.json"):
        try:
            data = json.loads(f.read_text())
            for r in data:
                r["_source"] = str(f)
                r["_framework"] = "sweagent"
            return data
        except Exception:
            pass
    for f in base.glob("*_final_results.json"):
        try:
            data = json.loads(f.read_text())
            for r in data:
                r["_source"] = str(f)
                r["_framework"] = "sweagent"
            return data
        except Exception:
            pass
    return []


# ═══════════════════════════════════════════════════════════════════
#  Failure categorization
# ═══════════════════════════════════════════════════════════════════

def categorize_failure(r: dict) -> str:
    status = r.get("status") or r.get("final_status", "")
    if status in ("repaired", "plausible"):
        return "SUCCESS"

    # single_shot_thought style: attempt_summaries
    summaries = r.get("attempt_summaries", [])
    if summaries:
        cats = Counter()
        for s in summaries:
            st = s.get("status", "")
            rc = s.get("reason_code", st)
            if "EMPTY" in rc:
                cats["EMPTY_PATCH"] += 1
            elif "APPLY" in rc or "HUNK" in rc:
                cats["PATCH_APPLY_FAIL"] += 1
            elif "BUILD" in rc or "COMPILE" in rc.upper():
                cats["COMPILE_FAIL"] += 1
            elif "TRIGGER" in rc or "FUNCTIONALITY" in rc:
                cats["TRIGGER_TEST_FAIL"] += 1
            elif "REGRESSION" in rc or "NEW_FAILURE" in rc:
                cats["REGRESSION"] += 1
            elif "BUDGET" in rc or "TIMEOUT" in rc:
                cats["BUDGET_EXCEEDED"] += 1
            elif "REPAIRED" in rc:
                pass  # skip success entries
            else:
                cats[f"OTHER:{rc}"] += 1
        if cats:
            return cats.most_common(1)[0][0]

    # sweagent style
    err = r.get("error_category", "")
    if "no_patch" in status:
        return "EMPTY_PATCH"
    if "compile" in status:
        return "COMPILE_FAIL"
    if "trigger" in status:
        return "TRIGGER_TEST_FAIL"
    if "regression" in status:
        return "REGRESSION"
    if "timeout" in status or "timeout" in err:
        return "TIMEOUT"
    if "infra" in err:
        return "INFRA_ERROR"

    return f"UNREPAIRED:{status}"


# ═══════════════════════════════════════════════════════════════════
#  pass@k computation from attempt_summaries
# ═══════════════════════════════════════════════════════════════════

def compute_pass_at_k(results: List[dict], k_values: List[int] = None) -> dict:
    """Compute pass@k: fraction of bugs repaired within first k attempts.

    Uses attempt_summaries to determine which attempt succeeded.
    """
    if k_values is None:
        k_values = [1, 3, 5, 10, 15, 20, 25, 30]

    total = len(results)
    if total == 0:
        return {f"pass@{k}": 0.0 for k in k_values}

    # For each bug, find the first successful attempt
    first_success = {}
    for r in results:
        bug = r.get("bug", r.get("instance_id", ""))
        summaries = r.get("attempt_summaries", [])
        for s in summaries:
            if s.get("status") == "REPAIRED":
                attempt = s.get("attempt", 999)
                if bug not in first_success or attempt < first_success[bug]:
                    first_success[bug] = attempt
        # Also check top-level status
        if r.get("status") == "repaired" and bug not in first_success:
            first_success[bug] = r.get("attempts_used", 1)

    pass_at_k = {}
    for k in k_values:
        if k > max(k_values):
            continue
        repaired_in_k = sum(1 for att in first_success.values() if att <= k)
        pass_at_k[f"pass@{k}"] = round(repaired_in_k / total * 100, 1)

    return pass_at_k


# ═══════════════════════════════════════════════════════════════════
#  Analysis
# ═══════════════════════════════════════════════════════════════════

def analyze(results: List[dict], label: str = "") -> dict:
    total = len(results)
    if total == 0:
        return {"label": label, "total": 0}

    successes = [r for r in results if categorize_failure(r) == "SUCCESS"]
    failure_cats = Counter(categorize_failure(r) for r in results if categorize_failure(r) != "SUCCESS")

    # Attempt stats
    attempts = [r.get("attempts_used", 0) for r in results if r.get("attempts_used")]
    llm_calls = [r.get("llm", {}).get("calls", 0) for r in results if r.get("llm", {}).get("calls")]
    tokens = [r.get("llm", {}).get("total_tokens", 0) for r in results if r.get("llm", {}).get("total_tokens")]
    times = [r.get("time_sec", 0) or r.get("wall_time_s", 0) for r in results]
    times = [t for t in times if t > 0]

    # FL mode breakdown
    fl_modes = Counter(r.get("fl_mode", "unknown") for r in results)

    # pass@k
    pass_k = compute_pass_at_k(results)

    return {
        "label": label, "total": total,
        "repaired": len(successes),
        "repair_rate": f"{len(successes)/total*100:.1f}%",
        "failure_breakdown": dict(failure_cats.most_common()),
        "fl_modes": dict(fl_modes),
        "pass_at_k": pass_k,
        "attempts_median": _med(attempts),
        "llm_calls_median": _med(llm_calls),
        "tokens_median": _med(tokens),
        "time_median_s": round(_med(times), 1) if times else 0,
        "repaired_bugs": sorted(r.get("bug", r.get("instance_id", "")) for r in successes),
    }


def compare_two(results_a, results_b, label_a, label_b) -> dict:
    a = analyze(results_a, label_a)
    b = analyze(results_b, label_b)
    bugs_a = set(a["repaired_bugs"])
    bugs_b = set(b["repaired_bugs"])
    return {
        "comparison": {label_a: a, label_b: b},
        "overlap": {
            "both_repaired": sorted(bugs_a & bugs_b),
            "only_a": sorted(bugs_a - bugs_b),
            "only_b": sorted(bugs_b - bugs_a),
        },
        "jaccard": round(len(bugs_a & bugs_b) / max(len(bugs_a | bugs_b), 1), 3),
    }


def cross_compare(parent_dir: str) -> dict:
    base = Path(parent_dir)
    experiments = {}
    for d in sorted(base.iterdir()):
        if d.is_dir() and not d.name.startswith("."):
            results = load_single_shot_results(str(d))
            if results:
                experiments[d.name] = results
    if not experiments:
        # Try loading sweagent-style
        for d in sorted(base.iterdir()):
            if d.is_dir():
                results = load_sweagent_results(str(d))
                if results:
                    experiments[d.name] = results

    analyses = {name: analyze(results, name) for name, results in experiments.items()}
    names = sorted(experiments.keys())
    pairwise = {}
    for i, na in enumerate(names):
        for nb in names[i+1:]:
            pairwise[f"{na} vs {nb}"] = compare_two(
                experiments[na], experiments[nb], na, nb)
    return {"individual": analyses, "pairwise": pairwise}


def failure_report(results: List[dict], label: str = "") -> str:
    lines = [f"# Failure Root Cause: {label}", ""]
    by_cat = defaultdict(list)
    for r in results:
        cat = categorize_failure(r)
        if cat != "SUCCESS":
            bug = r.get("bug", r.get("instance_id", "?"))
            by_cat[cat].append(bug)

    if not by_cat:
        return "All bugs repaired!\n"

    total_fail = sum(len(v) for v in by_cat.values())
    lines.append(f"Total failures: {total_fail}\n")

    for cat, bugs in sorted(by_cat.items(), key=lambda x: -len(x[1])):
        pct = len(bugs) / max(total_fail, 1) * 100
        lines.append(f"## {cat} — {len(bugs)} ({pct:.0f}%)")
        for b in bugs[:15]:
            lines.append(f"  - {b}")
        if len(bugs) > 15:
            lines.append(f"  ... and {len(bugs)-15} more")
        lines.append("")
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════
#  Pretty printers
# ═══════════════════════════════════════════════════════════════════

def _med(lst):
    if not lst:
        return 0
    s = sorted(lst)
    return s[len(s)//2]


def print_analysis(a: dict):
    print(f"\n{'='*60}")
    print(f"  {a.get('label', '')}")
    print(f"{'='*60}")
    print(f"  Total: {a['total']}  |  Repaired: {a['repaired']} ({a['repair_rate']})")

    if a.get("pass_at_k"):
        vals = " | ".join(f"{k}={v}%" for k, v in a["pass_at_k"].items())
        print(f"  {vals}")

    if a.get("failure_breakdown"):
        print(f"\n  Failures:")
        for cat, cnt in sorted(a["failure_breakdown"].items(), key=lambda x: -x[1]):
            bar = "█" * min(cnt, 30)
            print(f"    {cat:<25s} {cnt:>3d} {bar}")

    if a.get("time_median_s"):
        print(f"\n  Time(med): {a['time_median_s']}s | LLM calls(med): {a.get('llm_calls_median',0)} | Tokens(med): {a.get('tokens_median',0)}")
    print(f"{'='*60}")


def print_comparison(c: dict):
    labels = list(c["comparison"].keys())
    print(f"\n{'='*70}")
    print(f"  COMPARE: {labels[0]} vs {labels[1]}")
    for label, stats in c["comparison"].items():
        print(f"  {label}: {stats['repaired']}/{stats['total']} ({stats['repair_rate']})")
    o = c["overlap"]
    print(f"  Both: {len(o['both_repaired'])} | Only-A: {len(o['only_a'])} | Only-B: {len(o['only_b'])} | Jaccard: {c['jaccard']}")
    if o["only_a"]:
        print(f"  Unique to {labels[0]}: {', '.join(o['only_a'][:8])}")
    if o["only_b"]:
        print(f"  Unique to {labels[1]}: {', '.join(o['only_b'][:8])}")
    print(f"{'='*70}")


# ═══════════════════════════════════════════════════════════════════
#  CLI
# ═══════════════════════════════════════════════════════════════════

def main():
    ap = argparse.ArgumentParser(description="Analyze APR experiment results")
    ap.add_argument("--dir", help="Single experiment directory")
    ap.add_argument("--cross-compare", help="Parent dir with multiple experiments")
    ap.add_argument("--compare-sweagent", action="store_true")
    ap.add_argument("--single-shot-dir", help="single_shot_thought results")
    ap.add_argument("--sweagent-dir", help="sweagent results")
    ap.add_argument("--pass-at-k", help="Compute pass@k curves from a directory")
    ap.add_argument("--output", default="", help="Save JSON output")
    ap.add_argument("--report", default="", help="Save markdown report")
    args = ap.parse_args()

    if args.pass_at_k:
        results = load_single_shot_results(args.pass_at_k)
        if not results:
            results = load_sweagent_results(args.pass_at_k)
        pak = compute_pass_at_k(results, [1,2,3,5,10,15,20,25,30])
        print(f"\npass@k for {args.pass_at_k} ({len(results)} bugs):")
        for k, v in pak.items():
            bar = "█" * int(v / 3)
            print(f"  {k:>8s}: {v:>5.1f}% {bar}")
        if args.output:
            Path(args.output).write_text(json.dumps(pak, indent=2))

    elif args.cross_compare:
        result = cross_compare(args.cross_compare)
        for a in result.get("individual", {}).values():
            print_analysis(a)
        for c in result.get("pairwise", {}).values():
            print_comparison(c)
        if args.output:
            Path(args.output).write_text(json.dumps(result, indent=2, default=str))

    elif args.compare_sweagent and args.single_shot_dir and args.sweagent_dir:
        ra = load_single_shot_results(args.single_shot_dir)
        rb = load_sweagent_results(args.sweagent_dir)
        comp = compare_two(ra, rb, f"single_shot ({Path(args.single_shot_dir).name})",
                           f"sweagent ({Path(args.sweagent_dir).name})")
        print_comparison(comp)
        report = failure_report(ra, "single_shot") + "\n---\n\n" + failure_report(rb, "sweagent")
        if args.report:
            Path(args.report).write_text(report)
        else:
            print(report)
        if args.output:
            Path(args.output).write_text(json.dumps(comp, indent=2, default=str))

    elif args.dir:
        results = load_single_shot_results(args.dir)
        if not results:
            results = load_sweagent_results(args.dir)
        a = analyze(results, args.dir)
        print_analysis(a)
        report = failure_report(results, args.dir)
        if args.report:
            Path(args.report).write_text(report)
        else:
            print(report)
        if args.output:
            Path(args.output).write_text(json.dumps(a, indent=2, default=str))

    else:
        ap.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
