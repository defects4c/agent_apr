# swe_agent/patch_generators/tot.py
"""
Tree of Thoughts patch generator.
Paper: Yao et al. (NeurIPS 2023)
Strategy: Generate multiple candidate patches, evaluate them, select the best.
Budget: n_samples + 1 calls per attempt (candidates + judge).
"""
from pathlib import Path
from .base import PatchGenerator, PatchResult
from ._shared import (build_fail_context, build_location_context, PATCH_SYSTEM, extract_search_replace)
from .agentless import apply_search_replace_directly, search_replace_to_diff
from ..config import MAX_LLM_CALLS_PER_ATTEMPT


CANDIDATE_SYSTEM = """You are generating candidate patches for a Java bug.
Generate a minimal, targeted fix. Each candidate should try a different approach.

Output format:
FILE: path/to/File.java
SEARCH: <exact lines>
REPLACE: <corrected lines>
"""

CANDIDATE_USER = """## Bug: {bug_id}
## Failing: {fail_context}
## Locations: {location_context}

Generate {n_candidates} different patch candidates.
Each should explore a different fix strategy.

Candidate {candidate_num}:
"""

JUDGE_SYSTEM = """You are evaluating patch candidates for a Java bug.
Rank candidates by likelihood of fixing the bug without breaking other tests.

Consider:
1. Does the fix address the root cause?
2. Is the fix minimal and targeted?
3. Could it introduce regressions?

Output the best candidate number (1, 2, or 3) and a brief explanation.
"""

JUDGE_USER = """## Bug: {bug_id}
## Failing tests: {fail_context}

## Candidate Patches

{candidates}

## Task
Which candidate is most likely to fix the bug?
Output: "Best candidate: N" where N is 1, 2, or 3.
"""


class ToTPatchGenerator(PatchGenerator):

    def __init__(self):
        self.n_candidates = 3  # Number of candidates to generate

    def generate_patch(
        self,
        bug_id, workdir, failing_info, trigger_tests,
        localization_hits, attempt_index, out_dir, llm_client,
    ) -> PatchResult:

        fail_ctx = build_fail_context(bug_id, trigger_tests, failing_info)
        loc_ctx = build_location_context(localization_hits, workdir)

        # Limit candidates based on budget
        n_candidates = min(self.n_candidates, MAX_LLM_CALLS_PER_ATTEMPT - 1)

        candidates = []

        # Generate multiple candidates
        for i in range(n_candidates):
            prompt = CANDIDATE_USER.format(
                bug_id=bug_id,
                fail_context=fail_ctx,
                location_context=loc_ctx,
                n_candidates=n_candidates,
                candidate_num=i + 1,
            )

            messages = [
                {"role": "system", "content": CANDIDATE_SYSTEM},
                {"role": "user", "content": prompt}
            ]

            response = llm_client.chat(
                messages, purpose=f"tot_candidate_{i+1}", attempt=attempt_index,
                out_dir=out_dir, max_tokens=1000
            )

            if response:
                candidates.append(response)

        if not candidates:
            return PatchResult(diff_text="", metadata={
                "strategy": "tot", "candidates": [], "judge_reason": "no candidates"
            })

        # Judge candidates
        candidates_text = "\n\n".join(
            f"### Candidate {i+1}\n{c}" for i, c in enumerate(candidates)
        )

        judge_prompt = JUDGE_USER.format(
            bug_id=bug_id,
            fail_context=fail_ctx,
            candidates=candidates_text,
        )

        messages = [
            {"role": "system", "content": JUDGE_SYSTEM},
            {"role": "user", "content": judge_prompt}
        ]

        judge_response = llm_client.chat(
            messages, purpose="tot_judge", attempt=attempt_index,
            out_dir=out_dir, max_tokens=500
        )

        # Parse judge's choice
        best_idx = 0  # Default to first
        if judge_response:
            import re
            match = re.search(r"Best candidate:\s*(\d+)", judge_response)
            if match:
                best_idx = int(match.group(1)) - 1
                best_idx = max(0, min(best_idx, len(candidates) - 1))

        best_candidate = candidates[best_idx] if candidates else ""

        if not best_candidate:
            return PatchResult(diff_text="", metadata={
                "strategy": "tot", "candidates": 0, "reason": "no_best_candidate"
            })

        # Strategy 1: Try to apply best candidate directly
        success, result = apply_search_replace_directly(best_candidate, workdir)
        if success:
            from ..apply_patch import rollback
            rollback(workdir)
            return PatchResult(diff_text=result, metadata={
                "strategy": "tot",
                "candidates": len(candidates),
                "best_candidate": best_idx + 1,
                "judge_response": judge_response or "",
                "method": "direct_apply"
            })

        # Strategy 2: Extract and try again
        extracted = extract_search_replace(best_candidate)
        if extracted:
            success2, result2 = apply_search_replace_directly(extracted, workdir)
            if success2:
                from ..apply_patch import rollback
                rollback(workdir)
                return PatchResult(diff_text=result2, metadata={
                    "strategy": "tot",
                    "candidates": len(candidates),
                    "best_candidate": best_idx + 1,
                    "judge_response": judge_response or "",
                    "method": "extract_then_apply"
                })

        # Strategy 3: Convert to diff format
        diff_text = search_replace_to_diff(best_candidate, workdir)
        if diff_text:
            return PatchResult(diff_text=diff_text, metadata={
                "strategy": "tot",
                "candidates": len(candidates),
                "best_candidate": best_idx + 1,
                "judge_response": judge_response or "",
                "method": "convert_to_diff"
            })

        return PatchResult(diff_text="", metadata={
            "strategy": "tot",
            "candidates": len(candidates),
            "judge_response": judge_response or "",
            "reason": "patch_extraction_failed"
        })
