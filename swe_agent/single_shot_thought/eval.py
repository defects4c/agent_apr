# swe_agent/eval.py
"""
Batch evaluation with parallel support, FL mode selection, and structured output.

CLI:
  python -m single_shot_thought.eval --bugs bugs_114.txt --baseline cot --fl-mode oracle
  python -m single_shot_thought.eval --bugs bugs.txt --baseline react tot --parallel 4
"""
import argparse
import json
import csv
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from pathlib import Path
from .runner import run_bug, GENERATORS, set_verbose_flags
from .llm_client import Colors, colorize
from . import config


def load_bug_list(path: str) -> list:
    bugs = []
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        sep = "_" if "_" in line else "-"
        project, bug_id = line.split(sep, 1)
        bugs.append((project.strip(), bug_id.strip()))
    return bugs


_results_lock = threading.Lock()


def main():
    p = argparse.ArgumentParser(description="Batch APR evaluation")
    p.add_argument("--bugs", required=True, help="Bug list file")
    p.add_argument("--baseline", nargs="+", default=["cot"],
                   choices=list(GENERATORS.keys()))
    p.add_argument("--out", default="outputs")
    p.add_argument("--fl-mode", default=None, choices=["oracle", "stack", "llm"],
                   help="FL mode for all baselines")
    p.add_argument("--max-attempts", type=int, default=None,
                   help="Override pass@k (MAX_ATTEMPTS_PER_BUG)")
    p.add_argument("--parallel", "-j", type=int, default=1,
                   help="Parallel workers (different bugs run concurrently)")
    p.add_argument("--llm_verbose", action="store_true")
    p.add_argument("--patch_verbose", action="store_true")
    args = p.parse_args()

    set_verbose_flags(args.llm_verbose, args.patch_verbose)
    bugs = load_bug_list(args.bugs)

    k = args.max_attempts or config.MAX_ATTEMPTS_PER_BUG
    fl = args.fl_mode or config.FL_MODE
    model_short = config.GPT_MODEL.replace("/", "_").replace(":", "_")
    bug_scope = Path(args.bugs).stem

    # Build structured output directory
    out_base = Path(args.out)
    all_results = []

    print(f"Bugs: {len(bugs)} | Baselines: {args.baseline} | FL={fl} | k={k} | parallel={args.parallel}")

    for baseline in args.baseline:
        run_name = f"{model_short}_k{k}_{baseline}_{fl}_{bug_scope}"
        out_dir = out_base / run_name
        out_dir.mkdir(parents=True, exist_ok=True)

        # Save args
        (out_dir / "args.json").write_text(json.dumps({
            "model": config.GPT_MODEL, "baseline": baseline,
            "fl_mode": fl, "max_attempts": k,
            "bugs_file": args.bugs, "parallel": args.parallel,
            "timestamp": datetime.now().isoformat(),
        }, indent=2))

        results_for_baseline = []
        results_file = out_dir / "all_results.json"
        total = len(bugs)
        completed = [None] * total

        def _run_one(idx_bug):
            idx, (project, bug_id) = idx_bug
            bug_out = out_dir / f"{project}-{bug_id}"

            # Resume: skip if result exists
            result_path = bug_out / baseline / "result.json"
            if result_path.exists():
                try:
                    existing = json.loads(result_path.read_text())
                    if existing.get("status") in ("repaired", "unrepaired", "error"):
                        print(f"  [{baseline}] {project}-{bug_id}: SKIP (resume: {existing['status']})")
                        return idx, existing
                except Exception:
                    pass

            print(f"  [{baseline}] {project}-{bug_id} [{idx+1}/{total}] ...", flush=True)
            try:
                result = run_bug(project, bug_id, baseline, bug_out,
                                 llm_verbose=args.llm_verbose,
                                 patch_verbose=args.patch_verbose,
                                 fl_mode=fl, max_attempts=k)
            except Exception as e:
                result = {"bug": f"{project}_{bug_id}", "baseline": baseline,
                          "status": "error", "notes": str(e)}
                (bug_out / baseline).mkdir(parents=True, exist_ok=True)
                (bug_out / baseline / "result.json").write_text(json.dumps(result, indent=2))

            sc = Colors.GREEN if result["status"] == "repaired" else Colors.RED
            print(colorize(f"  → {project}-{bug_id}: {result['status']}", sc))

            # Incremental save
            with _results_lock:
                completed[idx] = result
                done = [r for r in completed if r is not None]
                results_file.write_text(json.dumps(done, indent=2, default=str))
                repaired = sum(1 for r in done if r.get("status") == "repaired")
                print(f"  ── Progress: {len(done)}/{total}, repaired={repaired} ──")

            return idx, result

        if args.parallel <= 1:
            for idx, bug in enumerate(bugs):
                _, result = _run_one((idx, bug))
                results_for_baseline.append(result)
        else:
            with ThreadPoolExecutor(max_workers=args.parallel) as pool:
                futures = {pool.submit(_run_one, (i, b)): i for i, b in enumerate(bugs)}
                for future in as_completed(futures):
                    idx, result = future.result()
                    completed[idx] = result
            results_for_baseline = [r for r in completed if r is not None]

        all_results.extend(results_for_baseline)

        # Write per-baseline summary
        _write_baseline_summary(results_for_baseline, baseline, out_dir)

    # Write cross-baseline summary
    _write_summary(all_results, out_base)
    _write_report(all_results, out_base)
    print(f"\nReport: {out_base}/report.md")


def _aggregate(results, baseline=None):
    subset = [r for r in results if (baseline is None or r.get("baseline") == baseline)]
    total = len(subset)
    repaired = sum(1 for r in subset if r.get("status") == "repaired")
    llm_calls = [r.get("llm", {}).get("calls", 0) for r in subset]
    tokens = [r.get("llm", {}).get("total_tokens", 0) for r in subset]
    times = [r.get("time_sec", 0) for r in subset if r.get("time_sec", 0) > 0]

    def med(lst):
        s = sorted(lst)
        return round(s[len(s)//2], 1) if s else 0

    return {
        "baseline": baseline or "all", "total": total, "repaired": repaired,
        "repair_rate": f"{repaired/total*100:.1f}%" if total else "0%",
        "llm_calls_median": med(llm_calls),
        "tokens_median": med(tokens),
        "time_median": med(times),
    }


def _write_baseline_summary(results, baseline, out_dir):
    ag = _aggregate(results, baseline)
    (out_dir / "summary.json").write_text(json.dumps(ag, indent=2))

    with open(out_dir / "summary.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["bug", "status", "attempts", "llm_calls", "tokens", "time_sec"])
        w.writeheader()
        for r in results:
            w.writerow({
                "bug": r.get("bug", ""), "status": r.get("status", ""),
                "attempts": r.get("attempts_used", 0),
                "llm_calls": r.get("llm", {}).get("calls", 0),
                "tokens": r.get("llm", {}).get("total_tokens", 0),
                "time_sec": r.get("time_sec", 0),
            })


def _write_summary(results, out_dir):
    baselines = sorted({r.get("baseline", "") for r in results})
    summary = {b: _aggregate(results, b) for b in baselines}
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2))


def _write_report(results, out_dir):
    baselines = sorted({r.get("baseline", "") for r in results})
    aggs = {b: _aggregate(results, b) for b in baselines}
    lines = [
        "# APR Experiment Report", f"\nDate: {date.today().isoformat()}", "",
        "## Summary", "",
        "| Baseline | Total | Repaired | Rate | LLM calls (med) | Tokens (med) | Time (med) |",
        "|:---------|------:|---------:|-----:|----------------:|-------------:|-----------:|",
    ]
    for b, ag in aggs.items():
        lines.append(f"| {b} | {ag['total']} | {ag['repaired']} | {ag['repair_rate']} "
                     f"| {ag['llm_calls_median']} | {ag['tokens_median']} | {ag['time_median']}s |")

    for bl in baselines:
        subset = [r for r in results if r.get("baseline") == bl]
        repaired = [r for r in subset if r.get("status") == "repaired"]
        failed = [r for r in subset if r.get("status") != "repaired"]
        lines += ["", f"## {bl} — Repaired ({len(repaired)})"]
        for r in repaired:
            lines.append(f"- {r['bug']} (attempt {r.get('attempts_used','?')}, {r.get('time_sec','?')}s)")
        lines += [f"## {bl} — Failed ({len(failed)})"]
        for r in failed:
            last = (r.get("attempt_summaries") or [{}])[-1]
            rc = last.get("reason_code", last.get("status", r.get("notes", "")))
            lines.append(f"- {r['bug']} — {rc}")

    (out_dir / "report.md").write_text("\n".join(lines))


if __name__ == "__main__":
    main()
