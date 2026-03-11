# swe_agent/patch_generators/cot.py
"""
Chain-of-Thought patch generator.
Paper: Wei et al. (NeurIPS 2022), Kojima et al. (NeurIPS 2022)
Strategy: one call — step-by-step reasoning scaffold before the patch output.
Budget: 1 LLM call per attempt (cheapest structured reasoning baseline).
"""
from pathlib import Path
from .base import PatchGenerator, PatchResult
from ._shared import (build_fail_context, build_location_context,
                       extract_search_replace, PATCH_SYSTEM)
from .agentless import apply_search_replace_directly, search_replace_to_diff


USER_TEMPLATE = """{fail_context}

## Suspicious location(s)
{location_context}

## Task
Reason step by step before writing the patch.

Step 1 — Root cause: explain what is wrong and why the test fails.
Step 2 — Fix strategy: describe the minimal code change that corrects the bug.
Step 3 — Implementation: write the patch.

Then output the patch using EXACTLY this format (no markdown fences):

FILE: path/to/File.java
SEARCH: <exact lines>
REPLACE: <corrected lines>
"""


class CoTPatchGenerator(PatchGenerator):

    def generate_patch(
        self,
        bug_id, workdir, failing_info, trigger_tests,
        localization_hits, attempt_index, out_dir, llm_client,
    ) -> PatchResult:

        fail_ctx = build_fail_context(bug_id, trigger_tests, failing_info)
        loc_ctx = build_location_context(localization_hits, workdir)

        prompt = USER_TEMPLATE.format(
            fail_context=fail_ctx,
            location_context=loc_ctx,
        )
        messages = [
            {"role": "system", "content": PATCH_SYSTEM},
            {"role": "user", "content": prompt}
        ]

        response = llm_client.chat(
            messages, purpose="cot_patch_gen", attempt=attempt_index,
            out_dir=out_dir, max_tokens=2000
        )

        if not response:
            return PatchResult(diff_text="", metadata={
                "strategy": "cot", "raw_response": "", "reason": "empty_response"
            })

        # Strategy 1: Try to apply search-replace directly
        success, result = apply_search_replace_directly(response, workdir)
        if success:
            from ..apply_patch import rollback
            rollback(workdir)
            return PatchResult(diff_text=result, metadata={
                "strategy": "cot", "raw_response": response, "method": "direct_apply"
            })

        # Strategy 2: Extract and try again
        extracted = extract_search_replace(response)
        if extracted:
            success2, result2 = apply_search_replace_directly(extracted, workdir)
            if success2:
                from ..apply_patch import rollback
                rollback(workdir)
                return PatchResult(diff_text=result2, metadata={
                    "strategy": "cot", "raw_response": response, "method": "extract_then_apply"
                })

        # Strategy 3: Convert to diff format
        diff_text = search_replace_to_diff(response, workdir)
        if diff_text:
            return PatchResult(diff_text=diff_text, metadata={
                "strategy": "cot", "raw_response": response, "method": "convert_to_diff"
            })

        # Strategy 4: Try to use raw response as diff if it looks like a diff
        if "---" in response and "+++" in response:
            return PatchResult(diff_text=response, metadata={
                "strategy": "cot", "raw_response": response, "method": "raw_diff"
            })

        return PatchResult(diff_text="", metadata={
            "strategy": "cot", "raw_response": response, "reason": "patch_extraction_failed"
        })
