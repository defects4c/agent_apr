# swe_agent/tasks/base.py
import os
import sys
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

# Add parent directory to path for config import
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import D4J_FOLDER

from .utils.bl import sequence_utils


@dataclass
class Result:
    task:         str
    test_result:  str = ""        # "PASS" | "FAIL" | "ERROR"
    result_reason: str = ""
    proposed_patch: str = ""
    patch_diff:   str = ""
    kwargs:       dict = field(default_factory=dict)

    def __post_init__(self):
        # merge extra kwargs into .kwargs for backward compat
        if "correct" in self.kwargs:
            self.test_result = "PASS" if self.kwargs["correct"] else "FAIL"


class BaseTask:
    """
    Mirrors HyperAgent BaseTask.
    Subclasses override: setup(), construct_prompt(idx), run(system, idx), validate(...)
    """
    BUG_INFO_DIR: str = D4J_FOLDER

    def __init__(self, logdir: str, split: str, _type: str = "pred", **kwargs):
        self.logdir    = Path(logdir)
        self.split     = split
        self._type     = _type
        self.logdir.mkdir(parents=True, exist_ok=True)
        self.setup()

    def setup(self):
        self.bug_names = sorted(os.listdir(self.BUG_INFO_DIR))

    def __len__(self):
        return len(self.bug_names)

    def bug_dir(self, bug_name: str) -> Path:
        return Path(self.BUG_INFO_DIR) / bug_name

    # ── Shared data loaders ────────────────────────────────────────────────

    def _load_fail_info(self, bug_name: str) -> dict:
        """
        Parses data/defects4j/<bug_name>/failing_tests
        Returns: {tc_signature: {error_message, stack_trace}}

        Format (from D4J):
          --- TestClass::testMethod
          ExceptionType: message
          \tat frame1
          \tat frame2
        """
        fail_info = {}
        tc_signature = None
        with open(self.bug_dir(bug_name) / "failing_tests") as f:
            for line in f:
                if line.startswith("--- "):
                    tc_name = line.split()[-1]
                    tc_signature = tc_name.replace("::", ".") + "()"
                    fail_info[tc_signature] = {"error_message": "", "stack_trace": ""}
                elif tc_signature:
                    key = "stack_trace" if line.startswith("\tat") else "error_message"
                    fail_info[tc_signature][key] += line
        return fail_info

    def _load_test_lists(self, bug_name: str) -> list[dict]:
        with open(self.bug_dir(bug_name) / "test_snippet.json") as f:
            return json.load(f)

    def _load_snippet_data(self, bug_name: str) -> list[dict]:
        with open(self.bug_dir(bug_name) / "snippet.json") as f:
            return json.load(f)

    def failing_test_signatures(self, fail_info: dict) -> list[str]:
        return list(fail_info.keys())

    def get_test_snippet(self, signature: str, bug_name: str) -> Optional[str]:
        """
        Retrieves and annotates test snippet with error location.
        Returns None if test case not found.
        """
        def _get_error_location(signature, fail_info):
            """
            Extracts the line number from the provided failure information related to a test case.
            """
            method_name = self._get_method_name(signature, simple_name=False)
            for line in fail_info.splitlines():
                if not line.startswith("\tat"):
                    continue
                m = re.match(r"\tat (.*)\(.*:(\d+)\)", line)
                if m is None or m.group(1) != method_name:
                    continue
                return int(m.group(2))
            return None

        parents = list()
        matching_test_case = None
        test_class_name = self._drop_base_name(
            self._get_method_name(signature, simple_name=False))
        _test_lists = self._load_test_lists(bug_name)

        for test_case in _test_lists:
            if signature == test_case["signature"]:
                matching_test_case = test_case
                break
            if self._get_method_name(signature) == self._get_method_name(test_case["signature"]):
                if test_class_name in test_case.get("child_classes", []):
                    parents.append((len(test_case["child_classes"]), test_case))

        if matching_test_case is None:
            if parents:
                matching_test_case = sorted(parents)[0][1]
            else:
                return None

        test_case = matching_test_case
        snippet = test_case["snippet"]
        begin_lineno = int(test_case["begin_line"])

        RANGE_REGEX = r"\(line (?P<beginline>\d+),col (?P<begincol>\d+)\)-\(line (?P<endline>\d+),col (?P<endcol>\d+)\)"

        if signature in self._load_fail_info(bug_name):
            error_lineno = _get_error_location(
                test_case["signature"],
                self.get_fail_info(signature, bug_name, minimize=False))
            annotate_error_location = error_lineno is not None
        else:
            annotate_error_location = False

        if annotate_error_location:
            assertion_line_numbers = []
            snippet_raw_lines = snippet.splitlines()

            for child_range in test_case.get("child_ranges", []):
                m = re.match(RANGE_REGEX, child_range)
                if m:
                    range_info = m.groupdict()
                    child_begin_lineno = int(range_info["beginline"])
                    child_end_lineno = int(range_info["endline"])
                    range_statement = "\n".join(
                        snippet_raw_lines[child_begin_lineno-begin_lineno:child_end_lineno-begin_lineno+1]
                    )
                    if child_begin_lineno <= error_lineno <= child_end_lineno:
                        error_end_lineno = child_end_lineno
                    if (range_statement.lstrip().startswith('assert') and
                        child_end_lineno < error_lineno):
                        assertion_line_numbers += list(range(child_begin_lineno, child_end_lineno+1))
                    last_lineno = child_end_lineno

            snippet_lines = snippet_raw_lines[:error_end_lineno-begin_lineno+1]
            line_numbers = [lineno
                            for lineno in range(begin_lineno, begin_lineno + len(snippet_lines))
                            if lineno not in assertion_line_numbers]
            removed_count = len(assertion_line_numbers)
            snippet_lines = [snippet_lines[lineno-begin_lineno] for lineno in line_numbers]

            error_index = error_lineno-begin_lineno-removed_count
            if 0 <= error_index < len(snippet_lines):
                snippet_lines[error_index] = snippet_lines[error_index] + " // error occurred here"

            snippet_lines += snippet_raw_lines[last_lineno-begin_lineno+1:]
            line_numbers += list(range(last_lineno+1, len(snippet_raw_lines)+begin_lineno))
        else:
            snippet_lines = snippet.splitlines()
            line_numbers = range(begin_lineno, begin_lineno + len(snippet_lines))

        snippet_lines = sequence_utils.concat_strings(
            line_numbers, snippet_lines, sep=" : ", align=True)

        return "\n".join(snippet_lines)

    def get_fail_info(self, tc_signature: str, bug_name: str,
                      minimize: bool = False, verbose: bool = False) -> str:
        """Returns error_message + stack_trace. If minimize=True, cleans both."""
        def _clean_error_message(error_message, max_lines=5, verbose=False):
            return "\n".join(error_message.splitlines()[:max_lines])

        def _clean_stack_trace(stack_trace, verbose=False, max_repetition=5):
            raw_stack = stack_trace.splitlines()
            cleaned_stack = []
            for line in raw_stack:
                if 'sun.reflect.NativeMethodAccessorImpl.invoke0' in line:
                    break
                if not ('junit.framework' in line):
                    cleaned_stack.append(line)

            repeated_subseq = sequence_utils.repeated_subsequences(
                cleaned_stack, min_repetition=max_repetition + 1)
            while repeated_subseq:
                maxlen_subseq = repeated_subseq[0]
                reduced_stack = cleaned_stack[:maxlen_subseq["start"]]
                reduced_stack += maxlen_subseq['subsequence']
                reduced_stack += [f'... (same pattern repeats {maxlen_subseq["num_repetition"]-2} more times) ...']
                reduced_stack += maxlen_subseq['subsequence']
                if maxlen_subseq["end"]+1 < len(cleaned_stack):
                    reduced_stack += cleaned_stack[maxlen_subseq["end"]+1:]
                cleaned_stack = reduced_stack
                repeated_subseq = sequence_utils.repeated_subsequences(
                    cleaned_stack, min_repetition=max_repetition+1)

            return "\n".join(cleaned_stack)

        _fail_info = self._load_fail_info(bug_name)
        error_message = _fail_info[tc_signature]["error_message"].rstrip()
        stack_trace = _fail_info[tc_signature]["stack_trace"].rstrip()

        if minimize:
            error_message = _clean_error_message(error_message, verbose=verbose)
            stack_trace = _clean_stack_trace(stack_trace, verbose=verbose)

        return error_message + "\n" + stack_trace

    @staticmethod
    def _get_method_name(signature: str, simple_name: bool = True) -> str:
        """Extract method name from signature."""
        if simple_name:
            return signature.split(".")[-1]
        return signature.rstrip("()")

    @staticmethod
    def _drop_base_name(class_name: str) -> str:
        """Drop the base name (last component) from a dotted class name."""
        parts = class_name.split(".")
        return ".".join(parts[:-1]) if len(parts) > 1 else ""

    @classmethod
    def _load_fail_info_static(cls, bug_name: str) -> dict:
        """
        Static version of _load_fail_info for use in runner.py.
        Parses data/defects4j/<bug_name>/failing_tests
        """
        fail_info = {}
        tc_signature = None
        bug_dir = Path(cls.BUG_INFO_DIR) / bug_name
        with open(bug_dir / "failing_tests") as f:
            for line in f:
                if line.startswith("--- "):
                    tc_name = line.split()[-1]
                    tc_signature = tc_name.replace("::", ".") + "()"
                    fail_info[tc_signature] = {"error_message": "", "stack_trace": ""}
                elif tc_signature:
                    key = "stack_trace" if line.startswith("\tat") else "error_message"
                    fail_info[tc_signature][key] += line
        return fail_info
