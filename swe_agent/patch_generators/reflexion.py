# swe_agent/patch_generators/reflexion.py
"""
Reflexion patch generator.
Paper: Shinn et al. (NeurIPS 2023)
Strategy: LLM reflects on previous failures and uses accumulated feedback.
Budget: 1-2 LLM calls per attempt (reflection + patch generation).
Stateful: maintains reflection history across attempts.
"""
from pathlib import Path
from .base import PatchGenerator, PatchResult
from ._shared import (build_fail_context, build_location_context, PATCH_SYSTEM, extract_search_replace)
from .agentless import apply_search_replace_directly, search_replace_to_diff


REFLECTION_SYSTEM = """You are an automated program repair system that learns from failures.

After each failed attempt, you will receive feedback about what went wrong.
Use this feedback to refine your approach.

Output format — use EXACTLY this structure:

FILE: path/to/File.java
SEARCH: <exact source lines to replace>
REPLACE: <corrected lines>

Rules:
- SEARCH must match the source exactly including whitespace
- Include 5-10 lines of context around changes
- Fix only the buggy logic"""


REFLECTION_USER = """## Bug: {bug_id}

## Previous attempts and failures
{reflection_history}

## Current failing information
{fail_context}

## Suspicious locations
{location_context}

## Task
Based on the previous failures, generate a new patch that avoids the same mistakes.

{reflection_prompt}

Output the patch:

FILE: path/to/File.java
SEARCH: <exact lines>
REPLACE: <corrected lines>
"""


class ReflexionPatchGenerator(PatchGenerator):
    """Stateful generator that maintains reflection history."""

    def __init__(self):
        self.reflection_history = []

    def generate_patch(
        self,
        bug_id, workdir, failing_info, trigger_tests,
        localization_hits, attempt_index, out_dir, llm_client,
    ) -> PatchResult:

        fail_ctx = build_fail_context(bug_id, trigger_tests, failing_info)
        loc_ctx = build_location_context(localization_hits, workdir)

        # Build reflection history
        if self.reflection_history:
            reflection_history = "\n\n".join(
                f"Attempt {i+1}: {r['status']}\nFeedback: {r['feedback']}"
                for i, r in enumerate(self.reflection_history)
            )
            reflection_prompt = (
                "Analyze what went wrong in previous attempts. "
                "Consider: Was the location wrong? Was the fix too aggressive? "
                "Did you introduce new bugs? Generate a better patch."
            )
        else:
            reflection_history = "No previous attempts (first attempt)."
            reflection_prompt = (
                "Analyze the failing test and stack trace carefully. "
                "Identify the root cause and generate a minimal fix."
            )

        prompt = REFLECTION_USER.format(
            bug_id=bug_id,
            reflection_history=reflection_history,
            fail_context=fail_ctx,
            location_context=loc_ctx,
            reflection_prompt=reflection_prompt,
        )

        messages = [
            {"role": "system", "content": REFLECTION_SYSTEM},
            {"role": "user", "content": prompt}
        ]

        response = llm_client.chat(
            messages, purpose="reflexion_patch_gen", attempt=attempt_index,
            out_dir=out_dir, max_tokens=2000
        )

        # Store this attempt's result for future reflection
        # The runner will call update_feedback after validation
        self.current_response = response or ""

        if not response:
            return PatchResult(diff_text="", metadata={
                "strategy": "reflexion", "raw_response": "", "reason": "empty_response"
            })

        # Strategy 1: Try to apply search-replace directly
        success, result = apply_search_replace_directly(response, workdir)
        if success:
            from ..apply_patch import rollback
            rollback(workdir)
            return PatchResult(diff_text=result, metadata={
                "strategy": "reflexion", "raw_response": response, "method": "direct_apply"
            })

        # Strategy 2: Extract and try again
        extracted = extract_search_replace(response)
        if extracted:
            success2, result2 = apply_search_replace_directly(extracted, workdir)
            if success2:
                from ..apply_patch import rollback
                rollback(workdir)
                return PatchResult(diff_text=result2, metadata={
                    "strategy": "reflexion", "raw_response": response, "method": "extract_then_apply"
                })

        # Strategy 3: Convert to diff format
        diff_text = search_replace_to_diff(response, workdir)
        if diff_text:
            return PatchResult(diff_text=diff_text, metadata={
                "strategy": "reflexion", "raw_response": response, "method": "convert_to_diff"
            })

        return PatchResult(diff_text="", metadata={
            "strategy": "reflexion", "raw_response": response, "reason": "patch_extraction_failed"
        })

    def update_feedback(self, status: str, feedback: str):
        """Called by runner after each attempt to record feedback."""
        self.reflection_history.append({"status": status, "feedback": feedback})

    def reset(self):
        """Reset reflection history for a new bug."""
        self.reflection_history = []
