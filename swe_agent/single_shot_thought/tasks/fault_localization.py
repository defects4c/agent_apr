# swe_agent/tasks/fault_localization.py
from .base import BaseTask, Result
import re


class FaultLocalization(BaseTask):

    RANGE_REGEX = r"\(line (?P<beginline>\d+),col (?P<begincol>\d+)\)-\(line (?P<endline>\d+),col (?P<endcol>\d+)\)"
    _MAX_REPETITION_IN_STACK = 5

    TASK_TEMPLATE = """Given following failed test case, localize which method in the codebase is responsible for the failure.
Failed Test: {test}
The test looks like:

```java
{test_snippets}
```

It failed with the following error message and call stack:

```
{failing_traces}
```

<output>Provide the method name in the format 'package.ClassName.methodName' that you think is responsible for the failure. No need to call editor to fix the fault.</output>"""

    def __init__(self, logdir, split, max_repetitions=3, max_num_tests=2, **kwargs):
        self.max_repetitions = max_repetitions
        self.max_num_tests   = max_num_tests
        super().__init__(logdir, split, _type="pred", **kwargs)

    def construct_prompt(self, idx: int) -> str:
        bug_name = self.bug_names[idx]
        fail_info = self._load_fail_info(bug_name)
        sigs = [s for s in self.failing_test_signatures(fail_info)
                if self.get_test_snippet(s, bug_name) is not None][:self.max_num_tests]
        snippets = "\n\n".join(self.get_test_snippet(s, bug_name).rstrip() for s in sigs)
        traces   = "\n\n".join(self.get_fail_info(s, bug_name, minimize=False).rstrip() for s in sigs)
        return self.TASK_TEMPLATE.format(test=sigs, test_snippets=snippets, failing_traces=traces)

    def get_fail_info(self, tc_signature: str, bug_name: str,
                      minimize: bool = False) -> str:
        """Returns error_message + stack_trace. If minimize=True, cleans both."""
        fi = self._load_fail_info(bug_name)[tc_signature]
        msg   = fi["error_message"].rstrip()
        stack = fi["stack_trace"].rstrip()
        if minimize:
            msg   = "\n".join(msg.splitlines()[:5])
            stack = self._clean_stack_trace(stack)
        return msg + "\n" + stack

    def get_test_snippet(self, signature: str, bug_name: str) -> str | None:
        """
        Retrieves and annotates test snippet with error location.
        Keeps the annotation logic from the reference implementation.
        Returns None if test case not found.
        """
        return super().get_test_snippet(signature, bug_name)

    def _clean_stack_trace(self, stack_trace: str) -> str:
        """Remove junit.framework frames and compress repeated subsequences."""
        raw_stack = stack_trace.splitlines()
        cleaned_stack = []
        for line in raw_stack:
            if 'sun.reflect.NativeMethodAccessorImpl.invoke0' in line:
                break
            if 'junit.framework' not in line:
                cleaned_stack.append(line)

        # Compress repeated subsequences
        from hyperagent.tasks.utils.bl import sequence_utils
        repeated_subseq = sequence_utils.repeated_subsequences(
            cleaned_stack, min_repetition=self._MAX_REPETITION_IN_STACK + 1)
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
                cleaned_stack, min_repetition=self._MAX_REPETITION_IN_STACK+1)

        return "\n".join(cleaned_stack)

    def failing_test_signatures(self, fail_info: dict) -> list[str]:
        return list(fail_info.keys())

    def report(self, results: list) -> dict:
        correct = sum(1 for r in results if r.kwargs.get("correct", False))
        total = len(results)
        return {"correct": correct, "total": total, "accuracy": correct / total if total else 0.0}
