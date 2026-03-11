# swe_agent/runner.py
"""
CLI: python -m swe_agent.runner --project Lang --bug 1 --baseline agentless --out outputs
"""
import argparse
import json
import os
import time
from pathlib import Path
from . import config, defects4j as d4j, reason
from .llm_client import LLMClient, BudgetExceededError
from .budget import BudgetManager
from .trace import TraceWriter
from .localize import localize
from .apply_patch import apply_patch, init_git_baseline, rollback
from .tests_runner import run_functionality_tests, run_regression_tests, get_trigger_tests
from .patch_generators.agentless import AgentlessPatchGenerator
from .patch_generators.swe_agent import SWEAgentPatchGenerator
from .patch_generators.openhands import OpenHandsPatchGenerator
from .patch_generators.openclaw import OpenClawPatchGenerator
from .patch_generators.claude_code import ClaudeCodePatchGenerator
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
    # Original agent baselines
    "agentless": AgentlessPatchGenerator,
    "swe_agent": SWEAgentPatchGenerator,
    "openhands": OpenHandsPatchGenerator,
    "openclaw": OpenClawPatchGenerator,
    "claude_code": ClaudeCodePatchGenerator,
    # Prompting-strategy baselines
    "cot": CoTPatchGenerator,
    "reflexion": ReflexionPatchGenerator,
    "tot": ToTPatchGenerator,
    "self_consistency": SelfConsistencyPatchGenerator,
    "got": GoTPatchGenerator,
    "standard": StandardPatchGenerator,
    "zero_shot_cot": ZeroShotCoTPatchGenerator,
    "few_shot_cot": FewShotCoTPatchGenerator,
    "react": ReActPatchGenerator,
    "pot": PoTPatchGenerator,
    "function_calling": FunctionCallingPatchGenerator,
}


def run_bug(project: str, bug_id: str, baseline: str, out_dir: Path) -> dict:
    bug_name = f"{project}_{bug_id}"
    workdir = Path(config.REPOS_DIR) / f"{project}-{bug_id}"
    out_dir = out_dir / baseline
    out_dir.mkdir(parents=True, exist_ok=True)

    trace = TraceWriter(out_dir / "trace.jsonl")
    llm = LLMClient(baseline, bug_name)
    budget = BudgetManager()
    gen = GENERATORS[baseline]()

    t_start = time.monotonic()
    result = {
        "bug": bug_name, "baseline": baseline,
        "status": "unrepaired", "attempts_used": 0,
        "attempt_summaries": [],
    }

    # ── 1. Checkout ──────────────────────────────────────────────────────────
    try:
        d4j.checkout(project, bug_id, "b", workdir,
                     log_path=out_dir / "logs" / "checkout.log")
    except Exception as e:
        result["status"] = "error"
        result["notes"] = str(e)
        _write_result(result, out_dir)
        return result

    # ── 2. Pre-patch baseline ────────────────────────────────────────────────
    n_before, failing_before, _ = d4j.test(
        workdir, project, log_path=out_dir / "logs" / "test_before.log")
    if n_before == 0:
        result["notes"] = "already passing"
        _write_result(result, out_dir)
        return result

    result["failing_count_before"] = n_before
    result["failing_tests_before"] = failing_before

    trigger_tests = get_trigger_tests(workdir, project)
    init_git_baseline(workdir)
    fail_info = _load_fail_info(bug_name)

    # Get test log for localization - use fail_info if log file is missing or lacks stack traces
    test_log_path = out_dir / "logs" / "test_before.log"
    if test_log_path.exists() and test_log_path.stat().st_size > 0:
        test_log = test_log_path.read_text()
        # Check if log contains stack traces (look for "\tat" pattern)
        if "\tat" not in test_log:
            # Fallback: construct log from fail_info which has full stack traces
            test_log = ""
            for tc_sig, info in fail_info.items():
                test_log += f"--- {tc_sig}\n"
                test_log += info.get("error_message", "") + "\n"
                test_log += info.get("stack_trace", "") + "\n"
    else:
        # Fallback: construct log from fail_info
        test_log = ""
        for tc_sig, info in fail_info.items():
            test_log += f"--- {tc_sig}\n"
            test_log += info.get("error_message", "") + "\n"
            test_log += info.get("stack_trace", "") + "\n"

    # Pass bug_info_dir to localize for snippet.json enrichment
    bug_info_dir = os.path.join(config.D4J_FOLDER, bug_name)
    loc_hits = localize(workdir, project, test_log, bug_info_dir)

    # ── 3. Attempt loop ───────────────────────────────────────────────────────
    v_time = {"apply_patch": 0.0, "compile": 0.0, "func_test": 0.0, "reg_test": 0.0}

    for attempt in range(1, config.MAX_ATTEMPTS_PER_BUG + 1):
        result["attempts_used"] = attempt
        attempt_status = {}

        # a) Generate patch
        try:
            patch_result = gen.generate_patch(
                bug_name, workdir, fail_info, trigger_tests, loc_hits,
                attempt, out_dir, llm)
        except BudgetExceededError as e:
            trace.log({
                "bug": bug_name, "baseline": baseline, "attempt": attempt,
                "phase": "patch_gen", "status": "FAIL",
                "reason_code": reason.TIMEOUT, "reason": str(e)
            })
            break

        _save_attempt(patch_result, attempt, out_dir)
        if not patch_result.diff_text:
            attempt_status = {
                "attempt": attempt,
                "status": "PATCH_GENERATED",
                "reason_code": "EMPTY_DIFF"
            }
            result["attempt_summaries"].append(attempt_status)
            # Provide feedback to reflexion
            if baseline == "reflexion" and hasattr(gen, "update_feedback"):
                gen.update_feedback("empty_patch", "No patch was generated. Try a different approach.")
            continue

        # b) Budget / safety check
        try:
            budget.check_patch(patch_result.diff_text)
        except Exception as e:
            rc = reason.PATCH_SCOPE_VIOLATION
            trace.log(_event(bug_name, baseline, attempt, "apply_patch", "FAIL", rc, str(e)))
            attempt_status = {
                "attempt": attempt,
                "status": reason.PATCH_APPLY_FAILED,
                "reason_code": rc
            }
            result["attempt_summaries"].append(attempt_status)
            continue

        # c) Apply patch
        t0 = time.monotonic()
        ok, err = apply_patch(patch_result.diff_text, workdir)
        v_time["apply_patch"] += time.monotonic() - t0
        if not ok:
            rc = reason.PATCH_APPLY_HUNK_FAILED
            trace.log(_event(bug_name, baseline, attempt, "apply_patch", "FAIL", rc, err))
            attempt_status = {
                "attempt": attempt,
                "status": reason.PATCH_APPLY_FAILED,
                "reason_code": rc
            }
            result["attempt_summaries"].append(attempt_status)
            # Provide feedback to reflexion
            if baseline == "reflexion" and hasattr(gen, "update_feedback"):
                gen.update_feedback("patch_apply_failed", f"Patch failed to apply: {err[:200]}")
            rollback(workdir)
            continue

        # d) Compile
        t0 = time.monotonic()
        ok, build_log = d4j.compile(
            workdir, project,
            log_path=out_dir / "logs" / f"attempt_{attempt:03d}_compile.log"
        )
        v_time["compile"] += time.monotonic() - t0
        if not ok:
            rc = reason.parse_build_reason(build_log)
            trace.log(_event(bug_name, baseline, attempt, "compile", "FAIL", rc))
            attempt_status = {
                "attempt": attempt,
                "status": reason.BUILD_FAILED,
                "reason_code": rc
            }
            result["attempt_summaries"].append(attempt_status)
            # Provide feedback to reflexion
            if baseline == "reflexion" and hasattr(gen, "update_feedback"):
                gen.update_feedback("build_failed", f"Compilation failed: {build_log[:200]}")
            rollback(workdir)
            continue

        # e) Functionality gate
        t0 = time.monotonic()
        n_func, still_failing, _ = run_functionality_tests(
            workdir, trigger_tests, project,
            log_path=out_dir / "logs" / f"attempt_{attempt:03d}_func_test.log"
        )
        v_time["func_test"] += time.monotonic() - t0
        if n_func > 0:
            rc = reason.TRIGGER_TEST_STILL_FAILING
            trace.log(_event(
                bug_name, baseline, attempt, "func_test", "FAIL", rc,
                metrics={"failing_count": n_func, "failing_tests": still_failing}
            ))
            attempt_status = {
                "attempt": attempt,
                "status": reason.FUNCTIONALITY_FAILED,
                "reason_code": rc
            }
            result["attempt_summaries"].append(attempt_status)
            # Provide feedback to reflexion
            if baseline == "reflexion" and hasattr(gen, "update_feedback"):
                gen.update_feedback("functionality_failed", f"Trigger tests still failing: {still_failing[:3]}")
            rollback(workdir)
            continue

        # f) Regression gate
        t0 = time.monotonic()
        n_reg, reg_failing, _ = run_regression_tests(
            workdir, project,
            log_path=out_dir / "logs" / f"attempt_{attempt:03d}_reg_test.log"
        )
        v_time["reg_test"] += time.monotonic() - t0
        new_failures = set(reg_failing) - set(failing_before)
        if new_failures:
            rc = reason.NEW_FAILURES_INTRODUCED
            trace.log(_event(
                bug_name, baseline, attempt, "reg_test", "FAIL", rc,
                metrics={"failing_count": n_reg, "failing_tests": reg_failing}
            ))
            attempt_status = {
                "attempt": attempt,
                "status": reason.REGRESSION_FAILED,
                "reason_code": rc
            }
            result["attempt_summaries"].append(attempt_status)
            # Provide feedback to reflexion
            if baseline == "reflexion" and hasattr(gen, "update_feedback"):
                gen.update_feedback("regression_failed", f"New test failures: {list(new_failures)[:3]}")
            rollback(workdir)
            continue

        # g) REPAIRED ✓
        (out_dir / "patch.diff").write_text(patch_result.diff_text)
        trace.log(_event(bug_name, baseline, attempt, "reg_test", "OK", reason.REPAIRED))
        result["status"] = "repaired"
        result["failing_count_after"] = 0
        attempt_status = {"attempt": attempt, "status": reason.REPAIRED}
        result["attempt_summaries"].append(attempt_status)
        break

    # ── 4. Finalize result.json ───────────────────────────────────────────────
    v_time["total"] = sum(v_time.values())
    result["time_sec"] = round(time.monotonic() - t_start, 1)
    result["llm"] = llm.summary()
    result["verification_time_sec"] = {k: round(v, 2) for k, v in v_time.items()}
    result["constraints"] = {
        "max_attempts": config.MAX_ATTEMPTS_PER_BUG,
        "max_llm_calls_per_bug": config.MAX_LLM_CALLS_PER_BUG,
        "max_tokens_per_bug": config.MAX_TOKENS_PER_BUG,
        "max_patch_lines": config.MAX_PATCH_LINES,
        "max_files_changed": config.MAX_FILES_CHANGED,
    }
    result["artifacts"] = {"trace": "trace.jsonl", "llm_calls": "llm_calls.jsonl"}
    if result["status"] == "repaired":
        result["artifacts"]["final_patch"] = "patch.diff"

    _write_result(result, out_dir)
    return result


# ── Helpers ──────────────────────────────────────────────────────────────────

def _event(bug, baseline, attempt, phase, status, reason_code, reason_msg="", metrics=None):
    e = {
        "bug": bug, "baseline": baseline, "attempt": attempt,
        "phase": phase, "status": status,
        "reason_code": reason_code, "reason": reason_msg
    }
    if metrics:
        e["metrics"] = metrics
    return e


def _save_attempt(patch_result, attempt: int, out_dir: Path):
    att_dir = out_dir / "attempts"
    att_dir.mkdir(exist_ok=True)
    diff_text = patch_result.diff_text or ""
    (att_dir / f"{attempt:03d}.patch.diff").write_text(diff_text)
    (att_dir / f"{attempt:03d}.meta.json").write_text(
        json.dumps(patch_result.metadata, indent=2)
    )


def _write_result(result: dict, out_dir: Path):
    (out_dir / "result.json").write_text(json.dumps(result, indent=2))


def _load_fail_info(bug_name: str) -> dict:
    """Load failure info from the data folder using absolute path."""
    from .tasks.base import BaseTask
    from .config import D4J_FOLDER
    import os

    # Use absolute path based on D4J_FOLDER config
    bug_dir = os.path.join(D4J_FOLDER, bug_name)
    fail_info = {}
    tc_signature = None

    failing_tests_path = os.path.join(bug_dir, "failing_tests")
    if not os.path.exists(failing_tests_path):
        # Fallback to static method which uses relative path
        return BaseTask._load_fail_info_static(bug_name)

    with open(failing_tests_path) as f:
        for line in f:
            if line.startswith("--- "):
                tc_name = line.split()[-1]
                tc_signature = tc_name.replace("::", ".") + "()"
                fail_info[tc_signature] = {"error_message": "", "stack_trace": ""}
            elif tc_signature:
                key = "stack_trace" if line.startswith("\tat") else "error_message"
                fail_info[tc_signature][key] += line
    return fail_info


# ── CLI ──────────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--project", required=True)
    p.add_argument("--bug", required=True)
    p.add_argument("--baseline", default="agentless",
                   choices=list(GENERATORS.keys()))
    p.add_argument("--out", default="outputs")
    args = p.parse_args()
    result = run_bug(args.project, args.bug, args.baseline,
                     Path(args.out) / f"{args.project}-{args.bug}")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
