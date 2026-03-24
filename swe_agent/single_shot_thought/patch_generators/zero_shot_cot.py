# swe_agent/patch_generators/zero_shot_cot.py
"""
Zero-Shot Chain-of-Thought patch generator.
Paper: Kojima et al., NeurIPS 2022 (arXiv:2205.11916)
Strategy:
  Call 1 — "Let's think step by step" elicits a reasoning chain (Stage 1).
  Call 2 — reasoning chain fed back to extract only the patch (Stage 2).
Budget: 2 LLM calls per attempt.
Note: "Let's think step by step" belongs to Kojima et al., NOT Wei et al.
      Wei et al. introduced few-shot CoT with demonstrations (see few_shot_cot.py).
"""
from pathlib import Path
from .base import PatchGenerator, PatchResult
from ._shared import (build_fail_context, build_location_context,
                       extract_search_replace, PATCH_SYSTEM)

STAGE1_TEMPLATE = """{fail_context}

## Suspicious location(s)
{location_context}

Let's think step by step about the root cause of this bug and how to fix it."""

STAGE2_TEMPLATE = """{fail_context}

## Your reasoning
{reasoning}

Now output the patch based on your reasoning above using EXACTLY this format (no markdown fences):

FILE: path/to/File.java
SEARCH: <exact lines>
REPLACE: <corrected lines>
"""


class ZeroShotCoTPatchGenerator(PatchGenerator):

    def generate_patch(
        self,
        bug_id, workdir, failing_info, trigger_tests,
        localization_hits, attempt_index, out_dir, llm_client,
    ) -> PatchResult:

        fail_ctx = build_fail_context(bug_id, trigger_tests, failing_info)
        loc_ctx  = build_location_context(localization_hits, workdir)

        # ── Call 1: elicit reasoning chain (Stage 1) ──────────────────────
        reasoning = llm_client.chat(
            [{"role": "user", "content": STAGE1_TEMPLATE.format(
                fail_context=fail_ctx, location_context=loc_ctx,
            )}],
            purpose="zscot_stage1", attempt=attempt_index,
            out_dir=out_dir, max_tokens=800,
        )

        # ── Call 2: extract structured patch (Stage 2) ────────────────────
        response = llm_client.chat(
            [{"role": "system", "content": PATCH_SYSTEM},
             {"role": "user",   "content": STAGE2_TEMPLATE.format(
                 fail_context=fail_ctx, reasoning=reasoning or "",
             )}],
            purpose="zscot_stage2", attempt=attempt_index,
            out_dir=out_dir, max_tokens=1200,
        )

        if not response:
            return PatchResult(diff_text="", metadata={
                "strategy": "zero_shot_cot", "raw_response": "",
                "reasoning": reasoning or "", "reason": "empty_response"
            })

        diff_text = extract_search_replace(response)
        return PatchResult(
            diff_text=diff_text,
            metadata={"strategy": "zero_shot_cot", "reasoning": reasoning or "",
                      "raw_response": response},
        )
