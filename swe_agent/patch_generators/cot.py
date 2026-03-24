# swe_agent/patch_generators/cot.py
"""
Structured Chain-of-Thought patch generator.
Strategy: one call — explicit step-by-step scaffold (root cause → fix → patch).
Budget: 1 LLM call per attempt.

NOTE: This is a CUSTOM structured reasoning scaffold, distinct from:
  - Zero-Shot CoT (Kojima et al. NeurIPS 2022): "Let's think step by step"
  - Few-Shot CoT (Wei et al. NeurIPS 2022): hand-written demonstrations
The Step 1/2/3 template is an APR-specific prompt design, not from a paper.
"""
from pathlib import Path
from .base import PatchGenerator, PatchResult
from ._shared import (build_fail_context, build_location_context,
                       extract_search_replace, PATCH_SYSTEM)


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

        prompt = USER_TEMPLATE.format(
            fail_context=build_fail_context(bug_id, trigger_tests, failing_info),
            location_context=build_location_context(localization_hits, workdir),
        )
        response = llm_client.chat(
            [{"role": "system", "content": PATCH_SYSTEM},
             {"role": "user", "content": prompt}],
            purpose="cot_patch_gen", attempt=attempt_index,
            out_dir=out_dir, max_tokens=2000,
        )
        diff_text = extract_search_replace(response) if response else ""
        return PatchResult(diff_text=diff_text, metadata={
            "strategy": "cot", "raw_response": response or "",
            "format": "search_replace",
        })
