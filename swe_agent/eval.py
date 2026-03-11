# swe_agent/eval.py
"""
CLI:
  python -m swe_agent.eval --bugs benchmarks/defects4j_small.txt \
    --baseline agentless swe_agent openclaw --out outputs
"""
import argparse
import json
import csv
from pathlib import Path
from datetime import date
from .runner import run_bug, GENERATORS, set_verbose_flags
from .llm_client import Colors, colorize


def load_bug_list(path: str) -> list[tuple[str, str]]:
    bugs = []
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # accept both "Lang_1" and "Lang-1"
        sep = "_" if "_" in line else "-"
        project, bug_id = line.split(sep, 1)
        bugs.append((project, bug_id))
    return bugs


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--bugs", required=True)
    p.add_argument("--baseline", nargs="+", default=["agentless"],
                   choices=list(GENERATORS.keys()))
    p.add_argument("--out", default="outputs")
    p.add_argument("--llm_verbose", action="store_true",
                   help="Show LLM prompts and responses with colors")
    p.add_argument("--patch_verbose", action="store_true",
                   help="Show patch diffs and verification status with colors")
    args = p.parse_args()

    # Set global verbose flags
    set_verbose_flags(args.llm_verbose, args.patch_verbose)

    bugs = load_bug_list(args.bugs)
    out_dir = Path(args.out)
    all_results: list[dict] = []

    # Print header if verbose
    if args.llm_verbose or args.patch_verbose:
        print(colorize("\n" + "=" * 70, Colors.BOLD + Colors.CYAN))
        print(colorize(" [Defects4J Agent-Based APR Evaluation]", Colors.BOLD + Colors.CYAN))
        print(colorize("=" * 70, Colors.CYAN))
        print(colorize(f"  Bugs: {len(bugs)} | Baselines: {len(args.baseline)}", Colors.DIM))
        print(colorize("=" * 70, Colors.CYAN) + "\n")

    for project, bug_id in bugs:
        for baseline in args.baseline:
            bug_out = out_dir / f"{project}-{bug_id}"

            if args.llm_verbose or args.patch_verbose:
                print(colorize(f"\n[{baseline}] {project}-{bug_id}", Colors.BOLD + Colors.MAGENTA))
                print(colorize("-" * 50, Colors.DIM))
            else:
                print(f"  [{baseline}] {project}-{bug_id} ...", end=" ", flush=True)

            try:
                result = run_bug(project, bug_id, baseline, bug_out,
                                 llm_verbose=args.llm_verbose, patch_verbose=args.patch_verbose)
            except Exception as e:
                result = {
                    "bug": f"{project}_{bug_id}",
                    "baseline": baseline,
                    "status": "error",
                    "notes": str(e)
                }
                (bug_out / baseline).mkdir(parents=True, exist_ok=True)
                (bug_out / baseline / "result.json").write_text(json.dumps(result, indent=2))

            if args.llm_verbose or args.patch_verbose:
                status_color = Colors.GREEN if result["status"] == "repaired" else Colors.RED if result["status"] == "error" else Colors.YELLOW
                print(colorize(f"→ Result: {result['status']}", status_color))
            else:
                print(result["status"])

            all_results.append(result)

    _write_summary(all_results, out_dir)
    _write_report(all_results, out_dir)
    _write_result_md(all_results, out_dir)
    print(f"\nReport: {out_dir}/report.md")
    print(f"Result summary: {out_dir.parent}/result.md")


def _aggregate(results: list[dict], baseline: str | None = None) -> dict:
    subset = [r for r in results if (baseline is None or r.get("baseline") == baseline)]
    total = len(subset)
    repaired = sum(1 for r in subset if r.get("status") == "repaired")
    func_fixed = sum(
        1 for r in subset
        if r.get("status") == "repaired" or
        any(
            a.get("status") not in ("BUILD_FAILED", "PATCH_APPLY_FAILED")
            for a in r.get("attempt_summaries", [])
        )
    )

    llm_calls_list = [r.get("llm", {}).get("calls", 0) for r in subset]
    tokens_list = [r.get("llm", {}).get("total_tokens", 0) for r in subset]
    verify_list = [r.get("verification_time_sec", {}).get("total", 0) for r in subset]

    def median(lst):
        s = sorted(lst)
        n = len(s)
        return round(s[n // 2], 1) if n else 0

    return {
        "baseline": baseline or "all",
        "total": total,
        "repaired": repaired,
        "repair_rate": f"{repaired / total * 100:.1f}%" if total else "0%",
        "llm_calls_median": median(llm_calls_list),
        "tokens_median": median(tokens_list),
        "verify_time_median": median(verify_list),
    }


def _write_summary(results: list[dict], out_dir: Path):
    out_dir.mkdir(parents=True, exist_ok=True)

    # JSON
    baselines = sorted({r.get("baseline", "") for r in results})
    summary = {b: _aggregate(results, b) for b in baselines}
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2))

    # CSV
    with open(out_dir / "summary.csv", "w", newline="") as f:
        w = csv.DictWriter(
            f, fieldnames=["bug", "baseline", "status", "attempts_used",
                           "llm_calls", "total_tokens", "verify_time_sec"]
        )
        w.writeheader()
        for r in results:
            w.writerow({
                "bug": r.get("bug", ""),
                "baseline": r.get("baseline", ""),
                "status": r.get("status", ""),
                "attempts_used": r.get("attempts_used", 0),
                "llm_calls": r.get("llm", {}).get("calls", 0),
                "total_tokens": r.get("llm", {}).get("total_tokens", 0),
                "verify_time_sec": r.get("verification_time_sec", {}).get("total", 0),
            })


def _write_report(results: list[dict], out_dir: Path):
    baselines = sorted({r.get("baseline", "") for r in results})
    aggs = {b: _aggregate(results, b) for b in baselines}

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
        subset = [r for r in results if r.get("baseline") == baseline]
        repaired = [r for r in subset if r.get("status") == "repaired"]
        failed = [r for r in subset if r.get("status") != "repaired"]

        lines += ["", f"## Repaired — {baseline}"]
        for r in repaired:
            att = r.get("attempts_used", "?")
            t = r.get("time_sec", "?")
            lines.append(f"- {r['bug']} (attempt {att}, {t}s)")

        lines += [f"## Unrepaired / Errors — {baseline}"]
        for r in failed:
            last = (r.get("attempt_summaries") or [{}])[-1]
            rc = last.get("reason_code", r.get("notes", ""))
            lines.append(f"- {r['bug']} — {rc}")

    (out_dir / "report.md").write_text("\n".join(lines))


def _write_result_md(results: list[dict], out_dir: Path):
    """
    Write the final result report to swe_agent/result.md as specified in readme.md.
    Contains: success rate, cost in token, cost in time, and failure case group analysis.
    """
    import os
    result_md_path = Path(__file__).parent / "result.md"

    baselines = sorted({r.get("baseline", "") for r in results})
    aggs = {b: _aggregate(results, b) for b in baselines}

    # Calculate overall stats
    total_bugs = len({(r.get("bug"), r.get("baseline")) for r in results})
    total_repaired = sum(1 for r in results if r.get("status") == "repaired")
    overall_rate = f"{total_repaired / len(results) * 100:.1f}%" if results else "0%"

    # Calculate totals
    total_tokens = sum(r.get("llm", {}).get("total_tokens", 0) for r in results)
    total_time = sum(r.get("time_sec", 0) for r in results)
    total_llm_calls = sum(r.get("llm", {}).get("calls", 0) for r in results)

    lines = [
        "# Defects4J Agent-Based APR Results",
        "",
        "## Overall Summary",
        "",
        f"- **Total bugs attempted**: {len(results)}",
        f"- **Total repaired**: {total_repaired}",
        f"- **Overall repair rate**: {overall_rate}",
        f"- **Total tokens used**: {total_tokens:,}",
        f"- **Total time (sec)**: {total_time:.1f}",
        f"- **Total LLM calls**: {total_llm_calls}",
        "",
        "## Per-Baseline Performance",
        "",
        "| Baseline | Bugs | Repaired | Rate | Tokens (med) | Time (med) | LLM Calls (med) |",
        "|:---------|-----:|---------:|-----:|-------------:|-----------:|----------------:|",
    ]

    for b, ag in aggs.items():
        # Get time median for this baseline
        baseline_results = [r for r in results if r.get("baseline") == b]
        time_list = [r.get("time_sec", 0) for r in baseline_results]
        time_median = sorted(time_list)[len(time_list) // 2] if time_list else 0

        lines.append(
            f"| {b} | {ag['total']} | {ag['repaired']} | {ag['repair_rate']} "
            f"| {ag['tokens_median']:,} | {time_median:.1f}s | {ag['llm_calls_median']} |"
        )

    # Failure case analysis by reason code
    lines += ["", "## Failure Case Analysis", ""]

    failure_reasons = {}
    for r in results:
        if r.get("status") != "repaired":
            last = (r.get("attempt_summaries") or [{}])[-1]
            reason = last.get("reason_code", r.get("notes", "unknown"))
            baseline = r.get("baseline", "unknown")
            key = (baseline, reason)
            failure_reasons[key] = failure_reasons.get(key, []) + [r.get("bug")]

    if failure_reasons:
        lines += ["### Failures by Reason", ""]
        for (baseline, reason), bugs in sorted(failure_reasons.items()):
            lines.append(f"#### {baseline} - {reason}")
            lines.append(f"- Count: {len(bugs)}")
            lines.append(f"- Bugs: {', '.join(bugs)}")
            lines.append("")
    else:
        lines += ["All bugs repaired successfully!", ""]

    # Detailed per-bug results
    lines += ["", "## Detailed Results", ""]

    for baseline in baselines:
        lines += [f"### {baseline}", ""]
        baseline_results = [r for r in results if r.get("baseline") == baseline]
        for r in baseline_results:
            status_icon = "✓" if r.get("status") == "repaired" else "✗"
            lines.append(
                f"- {status_icon} **{r.get('bug')}**: {r.get('status')} "
                f"(tokens: {r.get('llm', {}).get('total_tokens', 0)}, "
                f"time: {r.get('time_sec', 0):.1f}s, "
                f"calls: {r.get('llm', {}).get('calls', 0)})"
            )
        lines.append("")

    result_md_path.write_text("\n".join(lines))


if __name__ == "__main__":
    main()
