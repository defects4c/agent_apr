# swe_agent/patch_generators/pot.py
"""
Program of Thought (PoT) patch generator.
Paper: Chen et al. TMLR 2023 (arXiv:2211.12588)
Strategy: Model writes executable code (Python pseudocode) to reason about the fix,
  then translates the reasoning into a Java patch.
Budget: 2 LLM calls per attempt.
  Call 1: Write Python pseudocode that simulates the correct behavior
  Call 2: Translate the pseudocode logic into a Java patch
"""
from pathlib import Path
from .base import PatchGenerator, PatchResult
from ._shared import (build_fail_context, build_location_context,
                       extract_search_replace, PATCH_SYSTEM)

POT_STAGE1 = """{fail_context}

## Suspicious location(s)
{location_context}

## Task
Write Python pseudocode that correctly implements the intended behavior.
This pseudocode will help reason about what the Java code SHOULD do.

Format:
```python
# Describe the correct logic in executable Python
def correct_implementation(...):
    ...
```

Then explain how this differs from the buggy Java code."""

POT_STAGE2 = """{fail_context}

## Suspicious location(s)
{location_context}

## Python pseudocode showing correct behavior
{pseudocode}

## Task
Based on the Python pseudocode above, generate a Java patch that fixes the buggy code.
The patch should make the Java code behave like the Python pseudocode.

Output the patch using EXACTLY this format (no markdown fences):

FILE: path/to/File.java
SEARCH: <exact lines>
REPLACE: <corrected lines>
"""


class PoTPatchGenerator(PatchGenerator):

    def generate_patch(
        self,
        bug_id, workdir, failing_info, trigger_tests,
        localization_hits, attempt_index, out_dir, llm_client,
    ) -> PatchResult:

        fail_ctx = build_fail_context(bug_id, trigger_tests, failing_info)
        loc_ctx = build_location_context(localization_hits, workdir)

        # ── Call 1: Write Python pseudocode ─────────────────────────────────
        pseudocode = llm_client.chat(
            [{"role": "user", "content": POT_STAGE1.format(
                fail_context=fail_ctx, location_context=loc_ctx,
            )}],
            purpose="pot_stage1", attempt=attempt_index,
            out_dir=out_dir, max_tokens=800,
        )

        if not pseudocode:
            return PatchResult(diff_text="", metadata={
                "strategy": "pot", "raw_response": "", "reason": "empty_response"
            })

        # ── Call 2: Generate Java patch from pseudocode ─────────────────────
        response = llm_client.chat(
            [{"role": "system", "content": PATCH_SYSTEM},
             {"role": "user", "content": POT_STAGE2.format(
                 fail_context=fail_ctx, location_context=loc_ctx,
                 pseudocode=pseudocode,
             )}],
            purpose="pot_stage2", attempt=attempt_index,
            out_dir=out_dir, max_tokens=1200,
        )

        if not response:
            return PatchResult(diff_text="", metadata={
                "strategy": "pot", "raw_responses": [pseudocode],
                "reason": "empty_response_stage2"
            })

        diff_text = extract_search_replace(response)
        return PatchResult(
            diff_text=diff_text,
            metadata={
                "strategy": "pot",
                "pseudocode": pseudocode,
                "raw_response": response,
            },
        )
