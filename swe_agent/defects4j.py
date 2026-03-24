# swe_agent/defects4j.py
"""
Defects4J wrapper using the persistent Docker web API.
All commands execute inside the Docker container via HTTP POST.
Compatible with sweagent_selfcontainedqwen's webapp.py.
"""
import logging
import os
import re
import requests
from pathlib import Path
from .config import (D4J_URL, D4J_LOCAL_WORKSPACE, D4J_CONTAINER_WORKSPACE,
                     D4J_REQUEST_TIMEOUT, REPOS_DIR, TIMEOUT_COMPILE, TIMEOUT_REG_TEST)

logger = logging.getLogger("single_shot")


def _health_check() -> bool:
    try:
        r = requests.get(f"{D4J_URL}/health", timeout=5)
        return r.status_code == 200
    except Exception:
        return False


def _d4j_exec(args: list, cwd: str = None) -> tuple:
    """Run a defects4j subcommand via the web API."""
    cwd = cwd or D4J_CONTAINER_WORKSPACE
    try:
        r = requests.post(f"{D4J_URL}/api/exec",
                          json={"args": args, "cwd": cwd},
                          timeout=D4J_REQUEST_TIMEOUT)
        data = r.json()
        return data.get("returncode", 1), data.get("stdout", ""), data.get("stderr", "")
    except Exception as e:
        logger.error("d4j_exec failed: %s", e)
        return 1, "", str(e)


def _d4j_shell(cmd: str, cwd: str = None) -> tuple:
    """Run a shell command inside Docker via the web API."""
    cwd = cwd or D4J_CONTAINER_WORKSPACE
    try:
        r = requests.post(f"{D4J_URL}/api/exec-shell",
                          json={"cmd": cmd, "cwd": cwd},
                          timeout=D4J_REQUEST_TIMEOUT)
        data = r.json()
        return data.get("returncode", 1), data.get("stdout", ""), data.get("stderr", "")
    except Exception as e:
        logger.error("d4j_shell failed: %s", e)
        return 1, "", str(e)


def _container_dir(project: str, bug_id) -> str:
    return f"{D4J_CONTAINER_WORKSPACE}/{project}-{bug_id}"


def _host_dir(project: str, bug_id) -> str:
    return os.path.join(os.path.abspath(D4J_LOCAL_WORKSPACE), f"{project}-{bug_id}")


def checkout(project: str, bug_id, version: str, workdir: Path,
             log_path: Path = None):
    """Checkout buggy version via web API."""
    cdir = _container_dir(project, bug_id)
    # Clean stale checkout
    _d4j_shell(f"rm -rf {cdir} 2>/dev/null; true")
    rc, stdout, stderr = _d4j_exec(
        ["checkout", "-p", project, "-v", f"{bug_id}{version}", "-w", cdir])
    if log_path:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(stdout + "\n---STDERR---\n" + stderr)
    if rc != 0:
        raise RuntimeError(f"Checkout failed: {stderr[:300]}")
    # Ensure host workdir points to volume-mounted path
    workdir.mkdir(parents=True, exist_ok=True)


def compile(workdir: Path, project: str, log_path: Path = None):
    """Returns (success: bool, log: str)."""
    cdir = _container_dir(project, workdir.name.split("-")[-1] if "-" in workdir.name else "")
    # Use workdir.name as the project dir name
    cdir = f"{D4J_CONTAINER_WORKSPACE}/{workdir.name}"
    rc, stdout, stderr = _d4j_shell(f"cd {cdir} && defects4j compile")
    log = stdout + stderr
    if log_path:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(log)
    return rc == 0 and "BUILD FAILED" not in log, log


def test(workdir: Path, project: str,
         log_path: Path = None) -> tuple:
    """Returns (failing_count, failing_test_names, full_log)."""
    cdir = f"{D4J_CONTAINER_WORKSPACE}/{workdir.name}"
    rc, stdout, stderr = _d4j_shell(f"cd {cdir} && defects4j test")
    log = stdout + stderr
    if log_path:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(log)
    failing_count = 0
    failing_tests = []
    for line in log.splitlines():
        if line.startswith("Failing tests:"):
            try:
                failing_count = int(line.split(":")[-1].strip())
            except ValueError:
                pass
        elif line.strip().startswith("- "):
            failing_tests.append(line.strip()[2:])
    return failing_count, failing_tests, log


def test_specific(workdir: Path, project: str, test_names: list,
                  log_path: Path = None) -> tuple:
    """Run specific tests. Returns (failing_count, failing_tests, log)."""
    cdir = f"{D4J_CONTAINER_WORKSPACE}/{workdir.name}"
    if test_names:
        tests_arg = " -t " + ",".join(test_names)
    else:
        tests_arg = ""
    rc, stdout, stderr = _d4j_shell(f"cd {cdir} && defects4j test{tests_arg}")
    log = stdout + stderr
    if log_path:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(log)
    failing_count = 0
    failing_tests = []
    for line in log.splitlines():
        if line.startswith("Failing tests:"):
            try:
                failing_count = int(line.split(":")[-1].strip())
            except ValueError:
                pass
        elif line.strip().startswith("- "):
            failing_tests.append(line.strip()[2:])
    return failing_count, failing_tests, log


def export_trigger_tests(workdir: Path, project: str) -> list:
    cdir = f"{D4J_CONTAINER_WORKSPACE}/{workdir.name}"
    rc, stdout, _ = _d4j_shell(f"cd {cdir} && defects4j export -p tests.trigger")
    if rc == 0 and stdout.strip():
        return [l.strip() for l in stdout.splitlines() if l.strip()]
    return []


def get_modified_classes(workdir: Path, project: str) -> list:
    cdir = f"{D4J_CONTAINER_WORKSPACE}/{workdir.name}"
    rc, stdout, _ = _d4j_shell(f"cd {cdir} && defects4j export -p classes.modified")
    return [l.strip() for l in stdout.splitlines() if l.strip()] if rc == 0 else []


def shell(cmd: str, workdir: Path = None) -> tuple:
    """Run arbitrary shell command inside Docker. For ReAct agent."""
    cdir = f"{D4J_CONTAINER_WORKSPACE}/{workdir.name}" if workdir else D4J_CONTAINER_WORKSPACE
    return _d4j_shell(cmd, cwd=cdir)


def get_bug_info(project: str, bug_id) -> str:
    """Get defects4j info output."""
    rc, stdout, stderr = _d4j_exec(["info", "-p", project, "-b", str(bug_id)])
    return stdout if rc == 0 else f"Error: {stderr}"


def run_bash(function: str, project: str, bug_id: str,
             extra_arg1=None, extra_arg2=None):
    """Legacy bridge — runs via shell API."""
    cdir = _container_dir(project, bug_id)
    cmd = f"cd {cdir} && defects4j {function}"
    if extra_arg1:
        cmd += f" {extra_arg1}"
    if extra_arg2:
        cmd += f" {extra_arg2}"
    rc, stdout, stderr = _d4j_shell(cmd)
    class Result:
        pass
    r = Result()
    r.returncode = rc
    r.stdout = stdout
    r.stderr = stderr
    return r
