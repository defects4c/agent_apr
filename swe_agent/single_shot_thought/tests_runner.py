# swe_agent/tests_runner.py
"""Test runner using the Docker web API."""
from pathlib import Path
from typing import Tuple, List
from . import defects4j as d4j


def get_trigger_tests(workdir: Path, project: str) -> List[str]:
    return d4j.export_trigger_tests(workdir, project)


def run_functionality_tests(
    workdir: Path, trigger_tests: List[str], project: str,
    log_path: Path = None,
) -> Tuple[int, List[str], str]:
    """Run trigger tests. Returns (failing_count, failing_tests, log)."""
    return d4j.test_specific(workdir, project, trigger_tests, log_path)


def run_regression_tests(
    workdir: Path, project: str, log_path: Path = None,
) -> Tuple[int, List[str], str]:
    """Run full test suite. Returns (failing_count, failing_tests, log)."""
    return d4j.test(workdir, project, log_path)
