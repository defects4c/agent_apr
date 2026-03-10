# swe_agent/tasks/automated_program_repair.py
from .fault_localization import FaultLocalization
from .base import Result
import subprocess
import os


class AutomatedProgramRepair(FaultLocalization):

    TASK_TEMPLATE = """Given following failed test case, fix the code responsible for the failure. If there are multiple faults, find and fix them.
Failed Test: {test}
The test looks like:

```java
{test_snippets}
```

It failed with the following error message and call stack:

```
{failing_traces}
```

<output>Provide the method name in the format 'package.ClassName.methodName' that you think is responsible for the failure. You also need to edit the code to fix the fault.</output>"""

    def __init__(self, logdir, **kwargs):
        super().__init__(logdir=logdir, split=kwargs.pop("split", "test"),
                         _type="patch", **kwargs)

    def validate(self, proposed_patch: str, idx: int) -> Result:
        """
        Checkout buggy version → apply patch → run D4J tests → parse result.
        Returns Result with test_result in {"PASS", "FAIL", "ERROR"}.
        """
        bug_name = self.bug_names[idx]
        project, bug_id = bug_name.split("_", 1)

        # apply + test via defects4j bash bridge
        result = self._run_bash("validate_patch", project, bug_id, proposed_patch)

        if result.returncode != 0:
            reason = self._extract_error_reason(result.stderr)
            return Result("apr", test_result="ERROR", result_reason=reason,
                          proposed_patch=proposed_patch)

        if "Failing tests: 0" in result.stdout:
            return Result("apr", test_result="PASS", result_reason="all tests passed",
                          proposed_patch=proposed_patch)

        reason = self._run_bash("get_test_error", project, bug_id).stdout
        return Result("apr", test_result="FAIL", result_reason=reason,
                      proposed_patch=proposed_patch)

    def report(self, results: list) -> dict:
        counts = {"correct": 0, "incorrect": 0, "error": 0}
        for r in results:
            if r.test_result == "PASS":   counts["correct"]   += 1
            elif r.test_result == "FAIL": counts["incorrect"] += 1
            else:                          counts["error"]     += 1
        total = len(results)
        counts["repair_rate"] = counts["correct"] / total if total else 0.0
        return counts

    @staticmethod
    def _extract_error_reason(stderr: str) -> str:
        if "error: " in stderr:
            s = stderr[stderr.find("error: "):]
            return s[:s.find("\n")] if "\n" in s else s
        if "BUILD FAILED" in stderr:
            lines = stderr.split("\n")
            i = next((j for j, l in enumerate(lines) if "BUILD FAILED" in l), None)
            return lines[i + 1].strip() if i is not None and i + 1 < len(lines) else "BUILD FAILED"
        return "Test timed out after 600 seconds"

    def _run_bash(self, function, project, bug_id, extra_arg1=None, extra_arg2=None):
        """Run bash command via defects4j.sh script."""
        from ..config import D4J_HOME, REPOS_DIR, JDK_MAP
        project_location = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        work_dir = os.path.join(REPOS_DIR, f"{project}-{bug_id}")
        java_home = JDK_MAP.get(project, "/usr")

        script_path = os.path.join(project_location, "tasks/utils/defects4j.sh")
        cmd = ['bash', script_path, function, str(project), str(bug_id),
               str(work_dir), str(java_home), str(D4J_HOME),
               str(extra_arg1) if extra_arg1 else "", str(extra_arg2) if extra_arg2 else ""]

        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                universal_newlines=True)
        if len(result.stdout) > 0 and result.stdout[-1] == "\n":
            result.stdout = result.stdout[:-1]
        return result
