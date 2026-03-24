# swe_agent/runner.py
"""
CLI: python -m single_shot_thought.runner --project Lang --bug 1 --baseline cot
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
# Prompting-strategy baselines
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

_VERBOSE_LLM = True 
_VERBOSE_PATCH = True 


def set_verbose_flags(llm_verbose=False, patch_verbose=False):
    global _VERBOSE_LLM, _VERBOSE_PATCH
    _VERBOSE_LLM = llm_verbose
    _VERBOSE_PATCH = patch_verbose


def _print_patch_diff(diff_text, status):
    if not _VERBOSE_PATCH:
        return
    print("\n" + "=" * 70)
    sc = Colors.GREEN if status == "OK" else Colors.RED
    print(colorize(f" [PATCH] Status: {status}", Colors.BOLD + sc))
    for line in diff_text.splitlines()[:50]:
        if line.startswith("+"): print(colorize(line, Colors.GREEN))
        elif line.startswith("-"): print(colorize(line, Colors.RED))
        elif line.startswith("@@"): print(colorize(line, Colors.YELLOW))
        else: print(line)
    print("=" * 70)


def _print_status(phase, status, details=""):
    if not _VERBOSE_PATCH:
        return
    icons = {"OK": "✓", "PASS": "✓", "FAIL": "✗", "ERROR": "✗"}
    icon = icons.get(status, "?")
    sc = Colors.GREEN if status in ("OK", "PASS") else Colors.RED
    print(f"  {colorize(icon, sc)} {phase}: {status}")
    if details:
        print(colorize(f"    → {details[:100]}", Colors.DIM))


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
        "fl_mode": effective_fl,
        "max_attempts": effective_max,
    }

    if patch_verbose:
        print(colorize(f"\n [REPAIR] {bug_name} | {baseline} | FL={effective_fl} | k={effective_max}",
                        Colors.BOLD + Colors.CYAN))

    # 1. Checkout
    try:
        d4j.checkout(project, bug_id, "b", workdir,
                     log_path=out_dir / "logs" / "checkout.log")
        if patch_verbose:
            _print_status("checkout", "OK")
    except Exception as e:
        result["status"] = "error"
        result["notes"] = str(e)
        _write_result(result, out_dir)
        return result

    # 2. Pre-patch tests
    n_before, failing_before, _ = d4j.test(
        workdir, project, log_path=out_dir / "logs" / "test_before.log")
    if n_before == 0:
        result["notes"] = "already passing"
        _write_result(result, out_dir)
        return result

    if patch_verbose:
        print(colorize(f"  {n_before} failing test(s)", Colors.YELLOW))

    result["failing_count_before"] = n_before
    result["failing_tests_before"] = failing_before

    trigger_tests = get_trigger_tests(workdir, project)
    init_git_baseline(workdir)
    fail_info = _load_fail_info(bug_name)

    # Build test log for localization
    test_log_path = out_dir / "logs" / "test_before.log"
    test_log = ""
    if test_log_path.exists():
        test_log = test_log_path.read_text()
    if "\tat" not in test_log:
        for tc_sig, info in fail_info.items():
            test_log += f"--- {tc_sig}\n"
            test_log += (info or {}).get("error_message", "") + "\n"
            test_log += (info or {}).get("stack_trace", "") + "\n"

    # Localize with selected FL mode
    bug_info_dir = os.path.join(config.D4J_FOLDER, bug_name)
    loc_hits = localize(workdir, project, test_log, bug_info_dir,
                        fl_mode=effective_fl, bug_id=str(bug_id))

    if patch_verbose and loc_hits:
        print(colorize(f"  FL[{effective_fl}]: {len(loc_hits)} location(s)", Colors.CYAN))

    # 3. Attempt loop
    v_time = {"apply_patch": 0.0, "compile": 0.0, "func_test": 0.0, "reg_test": 0.0}

    for attempt in range(1, effective_max + 1):
        result["attempts_used"] = attempt
        attempt_status = {}

        if patch_verbose:
            print(colorize(f"\n  [ATTEMPT {attempt}/{effective_max}]", Colors.BOLD + Colors.YELLOW))

        # a) Generate patch
        try:
            patch_result = gen.generate_patch(
                bug_name, workdir, fail_info, trigger_tests, loc_hits,
                attempt, out_dir, llm)
        except BudgetExceededError as e:
            trace.log({"bug": bug_name, "baseline": baseline, "attempt": attempt,
                       "phase": "patch_gen", "status": "FAIL",
                       "reason_code": reason.TIMEOUT, "reason": str(e)})
            if patch_verbose:
                print(colorize(f"  Budget exceeded: {e}", Colors.RED))
            break

        _save_attempt(patch_result, attempt, out_dir)
        if not patch_result.diff_text:
            result["attempt_summaries"].append(
                {"attempt": attempt, "status": "EMPTY_DIFF"})
            if baseline == "reflexion" and hasattr(gen, "update_feedback"):
                gen.update_feedback("empty_patch", "No patch generated.")
            continue

        if patch_verbose:
            _print_patch_diff(patch_result.diff_text, "GENERATED")

        # b) Budget check
        try:
            budget.check_patch(patch_result.diff_text)
        except Exception as e:
            rc = reason.PATCH_SCOPE_VIOLATION
            result["attempt_summaries"].append(
                {"attempt": attempt, "status": "BUDGET_FAIL", "reason_code": rc})
            continue

        # c) Apply patch
        t0 = time.monotonic()
        ok, err = apply_patch(patch_result.diff_text, workdir)
        v_time["apply_patch"] += time.monotonic() - t0
        if not ok:
            result["attempt_summaries"].append(
                {"attempt": attempt, "status": reason.PATCH_APPLY_FAILED,
                 "reason_code": reason.PATCH_APPLY_HUNK_FAILED})
            if baseline == "reflexion" and hasattr(gen, "update_feedback"):
                gen.update_feedback("patch_apply_failed", f"Apply failed: {err[:200]}")
            rollback(workdir)
            continue
        if patch_verbose:
            _print_status("apply_patch", "OK")

        # d) Compile
        t0 = time.monotonic()
        ok, build_log = d4j.compile(
            workdir, project, log_path=out_dir / "logs" / f"attempt_{attempt:03d}_compile.log")
        v_time["compile"] += time.monotonic() - t0
        if not ok:
            rc = reason.parse_build_reason(build_log)
            result["attempt_summaries"].append(
                {"attempt": attempt, "status": reason.BUILD_FAILED, "reason_code": rc})
            if baseline == "reflexion" and hasattr(gen, "update_feedback"):
                gen.update_feedback("build_failed", f"Compile failed: {build_log[:200]}")
            if patch_verbose:
                _print_status("compile", "FAIL", rc)
            rollback(workdir)
            continue
        if patch_verbose:
            _print_status("compile", "OK")

        # e) Functionality gate
        t0 = time.monotonic()
        n_func, still_failing, _ = run_functionality_tests(
            workdir, trigger_tests, project,
            log_path=out_dir / "logs" / f"attempt_{attempt:03d}_func.log")
        v_time["func_test"] += time.monotonic() - t0
        if n_func > 0:
            result["attempt_summaries"].append(
                {"attempt": attempt, "status": reason.FUNCTIONALITY_FAILED,
                 "reason_code": reason.TRIGGER_TEST_STILL_FAILING})
            if baseline == "reflexion" and hasattr(gen, "update_feedback"):
                gen.update_feedback("func_failed", f"Tests failing: {still_failing[:3]}")
            if patch_verbose:
                _print_status("func_test", "FAIL", f"{n_func} failing")
            rollback(workdir)
            continue
        if patch_verbose:
            _print_status("func_test", "PASS")

        # f) Regression gate
        t0 = time.monotonic()
        n_reg, reg_failing, _ = run_regression_tests(
            workdir, project,
            log_path=out_dir / "logs" / f"attempt_{attempt:03d}_reg.log")
        v_time["reg_test"] += time.monotonic() - t0
        new_failures = set(reg_failing) - set(failing_before)
        if new_failures:
            result["attempt_summaries"].append(
                {"attempt": attempt, "status": reason.REGRESSION_FAILED,
                 "reason_code": reason.NEW_FAILURES_INTRODUCED})
            if baseline == "reflexion" and hasattr(gen, "update_feedback"):
                gen.update_feedback("regression", f"New failures: {list(new_failures)[:3]}")
            if patch_verbose:
                _print_status("reg_test", "FAIL", f"{len(new_failures)} new")
            rollback(workdir)
            continue
        if patch_verbose:
            _print_status("reg_test", "PASS")

        # g) REPAIRED
        (out_dir / "patch.diff").write_text(patch_result.diff_text)
        trace.log({"bug": bug_name, "baseline": baseline, "attempt": attempt,
                   "phase": "reg_test", "status": "OK", "reason_code": reason.REPAIRED})
        result["status"] = "repaired"
        result["failing_count_after"] = 0
        result["attempt_summaries"].append(
            {"attempt": attempt, "status": reason.REPAIRED})
        if patch_verbose:
            print(colorize("\n  ★ REPAIRED!", Colors.BOLD + Colors.GREEN))
        break

    # 4. Finalize
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


# ── Helpers ──────────────────────────────────────────────────────────────────

def _save_attempt(patch_result, attempt, out_dir):
    att_dir = out_dir / "attempts"
    att_dir.mkdir(exist_ok=True)
    diff_text = patch_result.diff_text or ""
    (att_dir / f"{attempt:03d}.patch.diff").write_text(diff_text)
    (att_dir / f"{attempt:03d}.meta.json").write_text(
        json.dumps(patch_result.metadata, indent=2, default=str))


def _write_result(result, out_dir):
    (out_dir / "result.json").write_text(json.dumps(result, indent=2, default=str))


def _event(bug, baseline, attempt, phase, status, reason_code, reason_msg="", metrics=None):
    e = {"bug": bug, "baseline": baseline, "attempt": attempt,
         "phase": phase, "status": status, "reason_code": reason_code}
    if metrics:
        e["metrics"] = metrics
    return e


def _load_fail_info(bug_name):
    from .config import D4J_FOLDER
    bug_dir = os.path.join(D4J_FOLDER, bug_name)
    fail_info = {}
    tc_sig = None
    failing_tests_path = os.path.join(bug_dir, "failing_tests")
    if not os.path.exists(failing_tests_path):
        return {}
    with open(failing_tests_path) as f:
        for line in f:
            if line.startswith("--- "):
                tc_name = line.split()[-1]
                tc_sig = tc_name.replace("::", ".") + "()"
                fail_info[tc_sig] = {"error_message": "", "stack_trace": ""}
            elif tc_sig:
                key = "stack_trace" if line.startswith("\tat") else "error_message"
                fail_info[tc_sig][key] += line
    return fail_info


# ── CLI ──────────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--project", required=True)
    p.add_argument("--bug", required=True)
    p.add_argument("--baseline", default="cot", choices=list(GENERATORS.keys()))
    p.add_argument("--out", default="outputs")
    p.add_argument("--fl-mode", default=None, choices=["oracle", "stack", "llm"],
                   help="FL mode: oracle (ground truth), stack (traces), llm (defects4j info)")
    p.add_argument("--max-attempts", type=int, default=None,
                   help="Override MAX_ATTEMPTS_PER_BUG (pass@k = this value)")
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
