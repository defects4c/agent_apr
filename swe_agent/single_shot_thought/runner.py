# swe_agent/runner.py
"""
CLI: python -m single_shot_thought runner --project Chart --bug 1 --baseline react
"""
import argparse
import json
import os
import time
from pathlib import Path
from . import config, defects4j as d4j, reason
from .llm_client import LLMClient, BudgetExceededError, Colors, colorize
from .budget import BudgetManager
from .trace import TraceWriter
from .localize import localize
from .apply_patch import apply_patch, init_git_baseline, rollback
from .tests_runner import run_functionality_tests, run_regression_tests, get_trigger_tests
from .patch_generators.cot import CoTPatchGenerator
from .patch_generators.reflexion import ReflexionPatchGenerator
from .patch_generators.tot import ToTPatchGenerator
from .patch_generators.self_consistency import SelfConsistencyPatchGenerator
from .patch_generators.got import GoTPatchGenerator
from .patch_generators.standard import StandardPatchGenerator
from .patch_generators.zero_shot_cot import ZeroShotCoTPatchGenerator
from .patch_generators.few_shot_cot import FewShotCoTPatchGenerator
from .patch_generators.react import ReActPatchGenerator
from .patch_generators.pot import PoTPatchGenerator
from .patch_generators.function_calling import FunctionCallingPatchGenerator

GENERATORS = {
    "standard": StandardPatchGenerator,
    "cot": CoTPatchGenerator,
    "zero_shot_cot": ZeroShotCoTPatchGenerator,
    "few_shot_cot": FewShotCoTPatchGenerator,
    "react": ReActPatchGenerator,
    "reflexion": ReflexionPatchGenerator,
    "tot": ToTPatchGenerator,
    "self_consistency": SelfConsistencyPatchGenerator,
    "got": GoTPatchGenerator,
    "pot": PoTPatchGenerator,
    "function_calling": FunctionCallingPatchGenerator,
}

_VERBOSE_LLM = False
_VERBOSE_PATCH = False


def set_verbose_flags(llm_verbose=False, patch_verbose=False):
    global _VERBOSE_LLM, _VERBOSE_PATCH
    _VERBOSE_LLM = llm_verbose
    _VERBOSE_PATCH = patch_verbose


def _v(msg, color=Colors.DIM):
    if _VERBOSE_PATCH:
        print(colorize(msg, color))


def _vstatus(phase, status, details=""):
    if not _VERBOSE_PATCH:
        return
    icon = colorize("✓", Colors.GREEN) if status in ("OK", "PASS") else colorize("✗", Colors.RED)
    print(f"  {icon} {phase}: {status}")
    if details:
        print(colorize(f"    → {details[:200]}", Colors.DIM))


def _vpatch(diff_text, status="GENERATED"):
    if not _VERBOSE_PATCH or not diff_text:
        return
    sc = Colors.GREEN if "OK" in status or "FINAL" in status else Colors.YELLOW
    print(colorize(f"\n  ── PATCH ({status}, {len(diff_text)} chars) ──", sc))
    lines = diff_text.splitlines()
    for line in lines[:30]:
        if line.startswith("+"):
            print(colorize(f"  {line}", Colors.GREEN))
        elif line.startswith("-"):
            print(colorize(f"  {line}", Colors.RED))
        elif line.startswith("FILE:") or line.startswith("SEARCH:") or line.startswith("REPLACE:"):
            print(colorize(f"  {line}", Colors.CYAN))
        else:
            print(f"  {line}")
    if len(lines) > 30:
        print(colorize(f"  ... ({len(lines) - 30} more lines)", Colors.DIM))
    print(colorize("  ── END PATCH ──", sc))


def run_bug(project: str, bug_id: str, baseline: str, out_dir: Path,
            llm_verbose=False, patch_verbose=False,
            fl_mode: str = None, max_attempts: int = None) -> dict:
    bug_name = f"{project}_{bug_id}"
    workdir = Path(config.REPOS_DIR) / f"{project}-{bug_id}"
    out_dir = out_dir / baseline
    out_dir.mkdir(parents=True, exist_ok=True)

    effective_max = max_attempts or config.MAX_ATTEMPTS_PER_BUG
    effective_fl = fl_mode or config.FL_MODE

    trace = TraceWriter(out_dir / "trace.jsonl")
    llm = LLMClient(baseline, bug_name, verbose=llm_verbose)
    budget = BudgetManager()
    gen = GENERATORS[baseline]()

    t_start = time.monotonic()
    result = {
        "bug": bug_name, "baseline": baseline,
        "status": "unrepaired", "attempts_used": 0,
        "attempt_summaries": [],
        "fl_mode": effective_fl, "max_attempts": effective_max,
    }

    _v(f"\n [REPAIR] {bug_name} | {baseline} | FL={effective_fl} | k={effective_max}",
       Colors.BOLD + Colors.CYAN)

    # ── 1. Health check ──
    if not d4j.health_check():
        msg = f"Cannot reach defects4j webapp at {config.D4J_URL}"
        _vstatus("health_check", "FAIL", msg)
        result["status"] = "error"
        result["notes"] = msg
        _write_result(result, out_dir)
        return result

    # ── 2. Checkout ──
    try:
        d4j.checkout(project, bug_id, "b", workdir,
                     log_path=out_dir / "logs" / "checkout.log")
        _vstatus("checkout", "OK")
    except Exception as e:
        _vstatus("checkout", "FAIL", str(e))
        result["status"] = "error"
        result["notes"] = str(e)
        _write_result(result, out_dir)
        return result

    # ── 3. Pre-patch tests ──
    n_before, failing_before, test_before_log = d4j.test(
        workdir, project, log_path=out_dir / "logs" / "test_before.log")
    if n_before == 0:
        _v("  Tests already passing — skipping", Colors.GREEN)
        result["notes"] = "already passing"
        _write_result(result, out_dir)
        return result
    _v(f"  {n_before} failing test(s)", Colors.YELLOW)

    result["failing_count_before"] = n_before
    result["failing_tests_before"] = failing_before

    trigger_tests = get_trigger_tests(workdir, project)
    init_git_baseline(workdir)

    # ── 4. Get failure info (from Docker container, NOT local files) ──
    fail_info = d4j.get_fail_info_from_container(workdir, project)
    if fail_info:
        _v(f"  Failure info: {len(fail_info)} test(s) with traces", Colors.CYAN)
    else:
        _v("  ⚠ No failure info (failing_tests file missing in container)", Colors.YELLOW)

    # ── 5. Build test log for stack-trace FL ──
    # Get test log with stack traces from Docker
    test_log = d4j.get_test_log_with_traces(workdir, project)
    if "\tat" not in test_log:
        # Supplement with fail_info if we have it
        for tc_sig, info in fail_info.items():
            test_log += f"--- {tc_sig}\n"
            test_log += (info or {}).get("error_message", "") + "\n"
            test_log += (info or {}).get("stack_trace", "") + "\n"

    # ── 6. Localize ──
    bug_info_dir = os.path.join(config.D4J_FOLDER, bug_name)
    loc_hits = localize(workdir, project, test_log, bug_info_dir,
                        fl_mode=effective_fl, bug_id=str(bug_id))
    if loc_hits:
        _v(f"  FL[{effective_fl}]: {len(loc_hits)} location(s)", Colors.CYAN)
        for h in loc_hits:
            _v(f"    {h.filepath} L{h.start_line}-{h.end_line} (conf={h.confidence:.2f})",
               Colors.DIM)
    else:
        _v(f"  ⚠ FL[{effective_fl}]: no locations found", Colors.RED)

    # ── 7. Attempt loop ──
    v_time = {"apply_patch": 0.0, "compile": 0.0, "func_test": 0.0, "reg_test": 0.0}

    for attempt in range(1, effective_max + 1):
        result["attempts_used"] = attempt
        _v(f"\n  [ATTEMPT {attempt}/{effective_max}]", Colors.BOLD + Colors.YELLOW)

        # a) Generate patch
        try:
            patch_result = gen.generate_patch(
                bug_name, workdir, fail_info, trigger_tests, loc_hits,
                attempt, out_dir, llm)
        except BudgetExceededError as e:
            _v(f"  Budget exceeded: {e}", Colors.RED)
            trace.log({"bug": bug_name, "baseline": baseline, "attempt": attempt,
                       "phase": "patch_gen", "status": "FAIL", "reason_code": reason.TIMEOUT})
            break

        _save_attempt(patch_result, attempt, out_dir)

        if not patch_result.diff_text:
            _vstatus("patch_gen", "FAIL", "Empty patch (no diff produced)")
            result["attempt_summaries"].append({"attempt": attempt, "status": "EMPTY_DIFF"})
            if baseline == "reflexion" and hasattr(gen, "update_feedback"):
                gen.update_feedback("empty_patch", "No patch generated.")
            continue

        _vpatch(patch_result.diff_text, "GENERATED")

        # b) Budget check
        try:
            budget.check_patch(patch_result.diff_text)
        except Exception as e:
            _vstatus("budget_check", "FAIL", str(e))
            result["attempt_summaries"].append(
                {"attempt": attempt, "status": "BUDGET_FAIL", "reason_code": reason.PATCH_SCOPE_VIOLATION})
            continue

        # c) Apply patch
        t0 = time.monotonic()
        ok, err = apply_patch(patch_result.diff_text, workdir)
        v_time["apply_patch"] += time.monotonic() - t0
        if not ok:
            _vstatus("apply_patch", "FAIL", err)
            result["attempt_summaries"].append(
                {"attempt": attempt, "status": reason.PATCH_APPLY_FAILED,
                 "reason_code": reason.PATCH_APPLY_HUNK_FAILED, "detail": err[:200]})
            if baseline == "reflexion" and hasattr(gen, "update_feedback"):
                gen.update_feedback("patch_apply_failed", f"Apply failed: {err[:200]}")
            rollback(workdir)
            continue
        _vstatus("apply_patch", "OK")

        # d) Compile
        t0 = time.monotonic()
        ok, build_log = d4j.compile(
            workdir, project, log_path=out_dir / "logs" / f"attempt_{attempt:03d}_compile.log")
        v_time["compile"] += time.monotonic() - t0
        if not ok:
            rc = reason.parse_build_reason(build_log)
            _vstatus("compile", "FAIL", f"{rc}: {build_log[-200:]}")
            result["attempt_summaries"].append(
                {"attempt": attempt, "status": reason.BUILD_FAILED, "reason_code": rc,
                 "detail": build_log[-300:]})
            if baseline == "reflexion" and hasattr(gen, "update_feedback"):
                gen.update_feedback("build_failed", f"Compile failed: {build_log[:200]}")
            rollback(workdir)
            continue
        _vstatus("compile", "OK")

        # e) Functionality tests
        t0 = time.monotonic()
        n_func, still_failing, _ = run_functionality_tests(
            workdir, trigger_tests, project,
            log_path=out_dir / "logs" / f"attempt_{attempt:03d}_func.log")
        v_time["func_test"] += time.monotonic() - t0
        if n_func > 0:
            _vstatus("func_test", "FAIL", f"{n_func} test(s) still failing: {still_failing[:3]}")
            result["attempt_summaries"].append(
                {"attempt": attempt, "status": reason.FUNCTIONALITY_FAILED,
                 "reason_code": reason.TRIGGER_TEST_STILL_FAILING,
                 "detail": str(still_failing[:3])})
            if baseline == "reflexion" and hasattr(gen, "update_feedback"):
                gen.update_feedback("func_failed", f"Tests failing: {still_failing[:3]}")
            rollback(workdir)
            continue
        _vstatus("func_test", "PASS")

        # f) Regression tests
        t0 = time.monotonic()
        n_reg, reg_failing, _ = run_regression_tests(
            workdir, project,
            log_path=out_dir / "logs" / f"attempt_{attempt:03d}_reg.log")
        v_time["reg_test"] += time.monotonic() - t0
        new_failures = set(reg_failing) - set(failing_before)
        if new_failures:
            _vstatus("reg_test", "FAIL", f"{len(new_failures)} new failure(s): {list(new_failures)[:3]}")
            result["attempt_summaries"].append(
                {"attempt": attempt, "status": reason.REGRESSION_FAILED,
                 "reason_code": reason.NEW_FAILURES_INTRODUCED,
                 "detail": str(list(new_failures)[:3])})
            if baseline == "reflexion" and hasattr(gen, "update_feedback"):
                gen.update_feedback("regression", f"New failures: {list(new_failures)[:3]}")
            rollback(workdir)
            continue
        _vstatus("reg_test", "PASS")

        # g) REPAIRED ✓
        (out_dir / "patch.diff").write_text(patch_result.diff_text)
        trace.log({"bug": bug_name, "baseline": baseline, "attempt": attempt,
                   "phase": "reg_test", "status": "OK", "reason_code": reason.REPAIRED})
        result["status"] = "repaired"
        result["failing_count_after"] = 0
        result["attempt_summaries"].append({"attempt": attempt, "status": reason.REPAIRED})
        _v(f"\n  ★ REPAIRED at attempt {attempt}!", Colors.BOLD + Colors.GREEN)
        _vpatch(patch_result.diff_text, "FINAL")
        break

    # ── Finalize ──
    v_time["total"] = sum(v_time.values())
    result["time_sec"] = round(time.monotonic() - t_start, 1)
    result["llm"] = llm.summary()
    result["verification_time_sec"] = {k: round(v, 2) for k, v in v_time.items()}
    result["constraints"] = {
        "max_attempts": effective_max,
        "max_llm_calls_per_bug": config.MAX_LLM_CALLS_PER_BUG,
        "max_tokens_per_bug": config.MAX_TOKENS_PER_BUG,
        "fl_mode": effective_fl,
    }
    _write_result(result, out_dir)
    return result


def _save_attempt(patch_result, attempt, out_dir):
    att_dir = out_dir / "attempts"
    att_dir.mkdir(exist_ok=True)
    (att_dir / f"{attempt:03d}.patch.diff").write_text(patch_result.diff_text or "")
    (att_dir / f"{attempt:03d}.meta.json").write_text(
        json.dumps(patch_result.metadata, indent=2, default=str))


def _write_result(result, out_dir):
    (out_dir / "result.json").write_text(json.dumps(result, indent=2, default=str))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--project", required=True)
    p.add_argument("--bug", required=True)
    p.add_argument("--baseline", default="cot", choices=list(GENERATORS.keys()))
    p.add_argument("--out", default="outputs")
    p.add_argument("--fl-mode", default=None, choices=["oracle", "stack", "llm"])
    p.add_argument("--max-attempts", type=int, default=None)
    p.add_argument("--llm_verbose", action="store_true")
    p.add_argument("--patch_verbose", action="store_true")
    args = p.parse_args()

    set_verbose_flags(args.llm_verbose, args.patch_verbose)
    result = run_bug(args.project, args.bug, args.baseline,
                     Path(args.out) / f"{args.project}-{args.bug}",
                     llm_verbose=args.llm_verbose, patch_verbose=args.patch_verbose,
                     fl_mode=args.fl_mode, max_attempts=args.max_attempts)
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
