# swe_agent/patch_generators/react.py
"""
ReAct (Reason + Act) patch generator.
Paper: Yao et al. ICLR 2023 (arXiv:2210.03629)
Strategy: Interleave reasoning (Thought) with actions (Action/Observation).
  - Thought: Reason about the current state and what to do next
  - Action: Perform an action (read code, search, etc.)
  - Observation: Observe the result
  - Repeat until patch is generated
Budget: Up to 3 LLM calls per attempt (Thought→Action→Observation cycle).

For APR, we simplify to:
  Call 1: Thought + Action (read code context)
  Call 2: Thought + Action (analyze bug)
  Call 3: Generate final patch
"""
from pathlib import Path
from .base import PatchGenerator, PatchResult
from ._shared import (build_fail_context, build_location_context,
                       extract_search_replace, PATCH_SYSTEM)

REACT_SYSTEM = """You are a program repair assistant using the ReAct framework.
You will interleave reasoning (Thought) with actions (Action) and observations (Observation).

Available actions:
- READ_CODE: Read the suspicious code location
- ANALYZE_BUG: Analyze the failure information
- GENERATE_PATCH: Generate the final patch

Format your response as:
Thought: <your reasoning about what to do>
Action: <action name>
<If Action is GENERATE_PATCH, output the patch in this format:>
FILE: path/to/File.java
SEARCH: <exact lines>
REPLACE: <corrected lines>
"""

REACT_STAGE1 = """{fail_context}

## Suspicious location(s)
{location_context}

Begin the ReAct cycle. Start by reading and understanding the code."""

REACT_STAGE2 = """{fail_context}

## Suspicious location(s)
{location_context}

## Previous Thought and Action
{previous_response}

Now continue the ReAct cycle. Analyze the bug and plan the fix."""

REACT_FINAL = """{fail_context}

## Suspicious location(s)
{location_context}

## Previous reasoning steps
{history}

Now generate the final patch using EXACTLY this format (no markdown fences):

FILE: path/to/File.java
SEARCH: <exact lines>
REPLACE: <corrected lines>
"""


class ReActPatchGenerator(PatchGenerator):

    def generate_patch(
        self,
        bug_id, workdir, failing_info, trigger_tests,
        localization_hits, attempt_index, out_dir, llm_client,
    ) -> PatchResult:

        fail_ctx = build_fail_context(bug_id, trigger_tests, failing_info)
        loc_ctx = build_location_context(localization_hits, workdir)

        # ── Call 1: Initial reasoning + code understanding ──────────────────
        response1 = llm_client.chat(
            [{"role": "system", "content": REACT_SYSTEM},
             {"role": "user", "content": REACT_STAGE1.format(
                 fail_context=fail_ctx, location_context=loc_ctx,
             )}],
            purpose="react_stage1", attempt=attempt_index,
            out_dir=out_dir, max_tokens=600,
        )

        if not response1:
            return PatchResult(diff_text="", metadata={
                "strategy": "react", "raw_response": "", "reason": "empty_response_stage1"
            })

        # ── Call 2: Deeper analysis ─────────────────────────────────────────
        response2 = llm_client.chat(
            [{"role": "system", "content": REACT_SYSTEM},
             {"role": "user", "content": REACT_STAGE2.format(
                 fail_context=fail_ctx, location_context=loc_ctx,
                 previous_response=response1,
             )}],
            purpose="react_stage2", attempt=attempt_index,
            out_dir=out_dir, max_tokens=600,
        )

        if not response2:
            return PatchResult(diff_text="", metadata={
                "strategy": "react", "raw_response": response1, "reason": "empty_response_stage2"
            })

        # ── Call 3: Generate final patch ────────────────────────────────────
        history = f"Stage 1:\n{response1}\n\nStage 2:\n{response2}"
        response3 = llm_client.chat(
            [{"role": "system", "content": PATCH_SYSTEM},
             {"role": "user", "content": REACT_FINAL.format(
                 fail_context=fail_ctx, location_context=loc_ctx,
                 history=history,
             )}],
            purpose="react_stage3", attempt=attempt_index,
            out_dir=out_dir, max_tokens=1200,
        )

        if not response3:
            return PatchResult(diff_text="", metadata={
                "strategy": "react", "raw_responses": [response1, response2],
                "reason": "empty_response_stage3"
            })

        diff_text = extract_search_replace(response3)
        return PatchResult(
            diff_text=diff_text,
            metadata={
                "strategy": "react",
                "raw_responses": [response1, response2, response3],
                "history": history,
            },
        )
