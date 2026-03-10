# swe_agent/tests_runner.py
"""
Test runner module.
Handles running functionality and regression tests.
Supports both local and Docker-based execution.
"""
import subprocess
import os
from pathlib import Path
from typing import Tuple, List

from . import defects4j as d4j
from .config import TIMEOUT_FUNC_TEST, TIMEOUT_REG_TEST

# Docker configuration
D4J_DOCKER_CONTAINER = os.environ.get("D4J_DOCKER_CONTAINER", "defects4j-multi")
D4J_DOCKER_WORKSPACE = "/workspace"
D4J_USE_DOCKER = os.environ.get("D4J_USE_DOCKER", "true").lower() == "true"


def _run_docker_cmd(cmd_in_container: str, timeout: int = 300) -> subprocess.CompletedProcess:
    """Run a command inside the Docker container."""
    docker_cmd = [
        "docker", "exec", "-t", D4J_DOCKER_CONTAINER,
        "bash", "-c", cmd_in_container
    ]
    return subprocess.run(
        docker_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
        timeout=timeout
    )


def get_trigger_tests(workdir: Path, project: str) -> List[str]:
    """Get the list of trigger tests (tests that originally failed)."""
    return d4j.export_trigger_tests(workdir, project)


def run_functionality_tests(
    workdir: Path,
    trigger_tests: List[str],
    project: str,
    log_path: Path | None = None
) -> Tuple[int, List[str], str]:
    """
    Run the trigger tests to check if the bug is fixed.
    Returns (failing_count, failing_test_names, full_log).
    """
    if D4J_USE_DOCKER:
        # Use Docker-based test execution
        docker_workdir = f"{D4J_DOCKER_WORKSPACE}/{workdir.name}"
        test_cmd = f"cd {docker_workdir} && defects4j test"
        if trigger_tests:
            test_cmd += f" -t {','.join(trigger_tests)}"

        result = _run_docker_cmd(test_cmd, TIMEOUT_FUNC_TEST)
        log = result.stdout + result.stderr
    else:
        # Use local test execution
        test_cmd = ["defects4j", "test", "-w", str(workdir)]
        if trigger_tests:
            test_cmd.extend(["-t", ",".join(trigger_tests)])

        result = subprocess.run(
            test_cmd,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_FUNC_TEST,
            env=_test_env(project)
        )
        log = result.stdout + result.stderr

    if log_path:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(log)

    # Parse failing tests
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


def run_regression_tests(
    workdir: Path,
    project: str,
    log_path: Path | None = None
) -> Tuple[int, List[str], str]:
    """
    Run full regression test suite to check for new failures.
    Returns (failing_count, failing_test_names, full_log).
    """
    if D4J_USE_DOCKER:
        docker_workdir = f"{D4J_DOCKER_WORKSPACE}/{workdir.name}"
        test_cmd = f"cd {docker_workdir} && defects4j test"
        result = _run_docker_cmd(test_cmd, TIMEOUT_REG_TEST)
        log = result.stdout + result.stderr
    else:
        result = subprocess.run(
            ["defects4j", "test", "-w", str(workdir)],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_REG_TEST,
            env=_test_env(project)
        )
        log = result.stdout + result.stderr

    if log_path:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(log)

    # Parse failing tests
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


def _test_env(project: str) -> dict:
    """Build test environment with correct JAVA_HOME."""
    from .config import D4J_HOME, JDK_MAP
    java_home = JDK_MAP.get(project, os.environ.get("JAVA_HOME", "/usr"))
    return {**os.environ, "JAVA_HOME": java_home,
            "PATH": f"{D4J_HOME}/framework/bin:{os.environ['PATH']}"}
