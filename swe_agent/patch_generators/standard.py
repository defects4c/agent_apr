# swe_agent/patch_generators/standard.py
"""
Standard (direct) patch generator — zero-scaffold control baseline.
Paper: Used as control in Wei et al. NeurIPS 2022.
Strategy: one call, no reasoning scaffold, direct patch request.
Budget: 1 LLM call per attempt (cheapest possible).
"""
from pathlib import Path
from .base import PatchGenerator, PatchResult
from ._shared import (build_fail_context, build_location_context,
                       extract_search_replace, PATCH_SYSTEM)

USER_TEMPLATE = """{fail_context}

## Suspicious location(s)
{location_context}

Output the patch that fixes this bug using EXACTLY this format (no markdown fences):

FILE: path/to/File.java
SEARCH: <exact lines>
REPLACE: <corrected lines>
"""


class StandardPatchGenerator(PatchGenerator):

    def generate_patch(
        self,
        bug_id, workdir, failing_info, trigger_tests,
        localization_hits, attempt_index, out_dir, llm_client,
    ) -> PatchResult:

        prompt = USER_TEMPLATE.format(
            fail_context=build_fail_context(bug_id, trigger_tests, failing_info),
            location_context=build_location_context(localization_hits, workdir),
        )
        response = llm_client.chat(
            [{"role": "system", "content": PATCH_SYSTEM},
             {"role": "user",   "content": prompt}],
            purpose="standard_patch_gen", attempt=attempt_index,
            out_dir=out_dir, max_tokens=1200,
        )

        if not response:
            return PatchResult(diff_text="", metadata={
                "strategy": "standard", "raw_response": "", "reason": "empty_response"
            })

        diff_text = extract_search_replace(response)
        return PatchResult(
            diff_text=diff_text,
            metadata={"strategy": "standard", "raw_response": response},
        )
