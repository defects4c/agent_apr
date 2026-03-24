# swe_agent/defects4j.py
"""
Defects4J wrapper — ALL commands go through the Docker web API.
No local defects4j installation needed. No D4J_HOME.
"""
import logging
import os
import re
import requests
from pathlib import Path
from .config import (D4J_URL, D4J_LOCAL_WORKSPACE, D4J_CONTAINER_WORKSPACE,
                     D4J_REQUEST_TIMEOUT, REPOS_DIR)

logger = logging.getLogger("single_shot")


# ═══════════════════════════════════════════════════════════════════
#  Low-level web API calls
# ═══════════════════════════════════════════════════════════════════

def _d4j_exec(args: list, cwd: str = None) -> tuple:
    """Run `defects4j <args>` inside Docker via /api/exec."""
    cwd = cwd or D4J_CONTAINER_WORKSPACE
    try:
        r = requests.post(f"{D4J_URL}/api/exec",
                          json={"args": args, "cwd": cwd},
                          timeout=D4J_REQUEST_TIMEOUT)
        d = r.json()
        return d.get("returncode", 1), d.get("stdout", ""), d.get("stderr", "")
    except Exception as e:
        logger.error("d4j_exec failed: %s", e)
        return 1, "", str(e)


def _d4j_shell(cmd: str, cwd: str = None) -> tuple:
    """Run a shell command inside Docker via /api/exec-shell."""
    cwd = cwd or D4J_CONTAINER_WORKSPACE
    try:
        r = requests.post(f"{D4J_URL}/api/exec-shell",
                          json={"cmd": cmd, "cwd": cwd},
                          timeout=D4J_REQUEST_TIMEOUT)
        d = r.json()
        return d.get("returncode", 1), d.get("stdout", ""), d.get("stderr", "")
    except Exception as e:
        logger.error("d4j_shell failed: %s", e)
        return 1, "", str(e)


def health_check() -> bool:
    try:
        return requests.get(f"{D4J_URL}/health", timeout=5).status_code == 200
    except Exception:
        return False


def _cdir(workdir: Path = None) -> str:
    """Container path for a workdir."""
    if workdir:
        return f"{D4J_CONTAINER_WORKSPACE}/{workdir.name}"
    return D4J_CONTAINER_WORKSPACE


# ═══════════════════════════════════════════════════════════════════
#  Core operations
# ═══════════════════════════════════════════════════════════════════

def checkout(project: str, bug_id, version: str, workdir: Path,
             log_path: Path = None):
    """Checkout via web API."""
    cdir = f"{D4J_CONTAINER_WORKSPACE}/{project}-{bug_id}"
    # Clean stale
    _d4j_shell(f"rm -rf {cdir} 2>/dev/null; true")
    rc, stdout, stderr = _d4j_exec(
        ["checkout", "-p", project, "-v", f"{bug_id}{version}", "-w", cdir])
    if log_path:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(stdout + "\n---STDERR---\n" + stderr)
    if rc != 0:
        raise RuntimeError(f"Checkout failed (rc={rc}): {stderr[:300]}")
    workdir.mkdir(parents=True, exist_ok=True)


def compile(workdir: Path, project: str, log_path: Path = None):
    """Returns (success, log)."""
    rc, stdout, stderr = _d4j_shell(f"cd {_cdir(workdir)} && defects4j compile")
    log = stdout + stderr
    if log_path:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(log)
    return rc == 0 and "BUILD FAILED" not in log, log


def test(workdir: Path, project: str, log_path: Path = None):
    """Returns (failing_count, failing_tests, log)."""
    rc, stdout, stderr = _d4j_shell(f"cd {_cdir(workdir)} && defects4j test")
    log = stdout + stderr
    if log_path:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(log)
    return _parse_test_output(log)


def test_specific(workdir: Path, project: str, test_names: list,
                  log_path: Path = None):
    """Run specific tests."""
    tests_arg = f" -t {','.join(test_names)}" if test_names else ""
    rc, stdout, stderr = _d4j_shell(
        f"cd {_cdir(workdir)} && defects4j test{tests_arg}")
    log = stdout + stderr
    if log_path:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(log)
    return _parse_test_output(log)


def _parse_test_output(log: str):
    count = 0
    tests = []
    for line in log.splitlines():
        if line.startswith("Failing tests:"):
            try:
                count = int(line.split(":")[-1].strip())
            except ValueError:
                pass
        elif line.strip().startswith("- "):
            tests.append(line.strip()[2:])
    return count, tests, log


# ═══════════════════════════════════════════════════════════════════
#  Info retrieval from Docker container
# ═══════════════════════════════════════════════════════════════════

def export_trigger_tests(workdir: Path, project: str) -> list:
    rc, stdout, _ = _d4j_shell(
        f"cd {_cdir(workdir)} && defects4j export -p tests.trigger")
    if rc == 0 and stdout.strip():
        return [l.strip() for l in stdout.splitlines() if l.strip()]
    return []


def get_modified_classes(workdir: Path, project: str) -> list:
    rc, stdout, _ = _d4j_shell(
        f"cd {_cdir(workdir)} && defects4j export -p classes.modified")
    return [l.strip() for l in stdout.splitlines() if l.strip()] if rc == 0 else []


def get_bug_info(project: str, bug_id) -> str:
    """Get `defects4j info` output from Docker."""
    rc, stdout, stderr = _d4j_exec(["info", "-p", project, "-b", str(bug_id)])
    return stdout if rc == 0 else f"Error: {stderr}"


def get_fail_info_from_container(workdir: Path, project: str) -> dict:
    """Get failing test info from the Docker container.

    Reads the failing_tests file inside the checked-out project.
    This is the ONLY reliable way when no local D4J_FOLDER exists.
    Returns: {test_signature: {error_message, stack_trace}}
    """
    cdir = _cdir(workdir)
    # Try the standard failing_tests file
    rc, stdout, _ = _d4j_shell(f"cat {cdir}/failing_tests 2>/dev/null || true")
    if rc == 0 and stdout.strip():
        return _parse_failing_tests(stdout)

    # Fallback: run tests and parse the output for stack traces
    logger.info("  No failing_tests file — running tests to get stack traces")
    rc, stdout, stderr = _d4j_shell(f"cd {cdir} && defects4j test 2>&1")
    full_log = stdout + stderr
    return _parse_test_log_for_traces(full_log)


def get_test_log_with_traces(workdir: Path, project: str) -> str:
    """Get test output with stack traces from Docker.

    Runs `defects4j test` and returns full output including stack traces.
    """
    cdir = _cdir(workdir)

    # First try the failing_tests file (has structured error info)
    rc, stdout, _ = _d4j_shell(f"cat {cdir}/failing_tests 2>/dev/null || true")
    if rc == 0 and stdout.strip() and "\tat" in stdout:
        return stdout

    # Run tests to get fresh output with traces
    rc, stdout, stderr = _d4j_shell(f"cd {cdir} && defects4j test 2>&1")
    return stdout + stderr


def _parse_failing_tests(content: str) -> dict:
    """Parse the failing_tests file format:
    --- test.Class::method
    error message
    \tat stack.trace.Line
    """
    fail_info = {}
    tc_sig = None
    for line in content.splitlines():
        if line.startswith("--- "):
            tc_name = line.split()[-1]
            tc_sig = tc_name.replace("::", ".") + "()"
            fail_info[tc_sig] = {"error_message": "", "stack_trace": ""}
        elif tc_sig:
            if line.startswith("\tat") or line.startswith("  at "):
                fail_info[tc_sig]["stack_trace"] += line + "\n"
            elif line.strip():
                fail_info[tc_sig]["error_message"] += line + "\n"
    return fail_info


def _parse_test_log_for_traces(log: str) -> dict:
    """Parse test output for error messages and stack traces."""
    fail_info = {}
    lines = log.splitlines()
    for i, line in enumerate(lines):
        if line.strip().startswith("- ") and "::" in line:
            tc_name = line.strip()[2:].strip()
            tc_sig = tc_name.replace("::", ".") + "()"
            error_msg = ""
            stack = ""
            for j in range(i + 1, min(i + 30, len(lines))):
                if lines[j].strip().startswith("- "):
                    break
                if lines[j].startswith("\tat") or lines[j].startswith("  at "):
                    stack += lines[j] + "\n"
                elif lines[j].strip():
                    error_msg += lines[j] + "\n"
            fail_info[tc_sig] = {"error_message": error_msg, "stack_trace": stack}
    return fail_info


# ═══════════════════════════════════════════════════════════════════
#  Shell access for ReAct agent
# ═══════════════════════════════════════════════════════════════════

def shell(cmd: str, workdir: Path = None) -> tuple:
    """Run arbitrary command in Docker. Used by ReAct."""
    return _d4j_shell(cmd, cwd=_cdir(workdir))
