# swe_agent/patch_generators/self_consistency.py
"""
Self-Consistency patch generator.
Paper: Wang et al. (ICLR 2023)
Strategy: Generate multiple independent samples, select the most common answer.
Budget: n_samples + 1 calls per attempt (samples + aggregator/judge).
"""
from pathlib import Path
from collections import Counter
from .base import PatchGenerator, PatchResult
from ._shared import (build_fail_context, build_location_context, PATCH_SYSTEM, extract_search_replace)
from .agentless import apply_search_replace_directly, search_replace_to_diff
from ..config import MAX_LLM_CALLS_PER_ATTEMPT


SAMPLE_SYSTEM = """You are fixing a Java bug. Analyze the failing test and stack trace.
Output a minimal fix using this format:

FILE: path/to/File.java
SEARCH: <exact lines>
REPLACE: <corrected lines>
"""

SAMPLE_USER = """## Bug: {bug_id}
## Failing tests: {fail_context}
## Locations: {location_context}

Generate a minimal patch to fix this bug.
"""

AGGREGATE_SYSTEM = """You are aggregating multiple patch attempts for a Java bug.
Find the common pattern across samples and produce the final patch.

Look for:
1. Same file location mentioned
2. Same type of fix (condition change, null check, etc.)
3. Similar search/replace patterns

Output the consolidated patch:

FILE: path/to/File.java
SEARCH: <exact lines>
REPLACE: <corrected lines>
"""

AGGREGATE_USER = """## Bug: {bug_id}

## Samples ({n_samples} independent attempts)

{samples}

## Task
Based on the samples above, identify the most consistent fix pattern.
If samples agree on the location and type of fix, output that fix.
If samples disagree significantly, choose the most common pattern.

Output the final patch:
"""


class SelfConsistencyPatchGenerator(PatchGenerator):

    def __init__(self):
        self.n_samples = 3  # Number of independent samples

    def generate_patch(
        self,
        bug_id, workdir, failing_info, trigger_tests,
        localization_hits, attempt_index, out_dir, llm_client,
    ) -> PatchResult:

        fail_ctx = build_fail_context(bug_id, trigger_tests, failing_info)
        loc_ctx = build_location_context(localization_hits, workdir)

        # Limit samples based on budget
        n_samples = min(self.n_samples, MAX_LLM_CALLS_PER_ATTEMPT - 1)

        samples = []

        # Generate multiple independent samples
        for i in range(n_samples):
            prompt = SAMPLE_USER.format(
                bug_id=bug_id,
                fail_context=fail_ctx,
                location_context=loc_ctx,
            )

            messages = [
                {"role": "system", "content": SAMPLE_SYSTEM},
                {"role": "user", "content": prompt}
            ]

            response = llm_client.chat(
                messages, purpose=f"self_consistency_sample_{i+1}",
                attempt=attempt_index, out_dir=out_dir, max_tokens=1000
            )

            if response:
                samples.append(response)

        if not samples:
            return PatchResult(diff_text="", metadata={
                "strategy": "self_consistency", "samples": 0, "reason": "no samples"
            })

        # Try to extract file locations and find consensus
        file_counter = Counter()
        for sample in samples:
            import re
            match = re.search(r'FILE:\s*(\S+)', sample)
            if match:
                file_counter[match.group(1)] += 1

        # Find most common file
        if file_counter:
            most_common_file, count = file_counter.most_common(1)[0]
            consensus_ratio = count / len(samples)
        else:
            most_common_file = ""
            consensus_ratio = 0

        # Aggregate samples
        samples_text = "\n\n".join(
            f"### Sample {i+1}\n{s}" for i, s in enumerate(samples)
        )

        agg_prompt = AGGREGATE_USER.format(
            bug_id=bug_id,
            n_samples=len(samples),
            samples=samples_text,
        )

        messages = [
            {"role": "system", "content": AGGREGATE_SYSTEM},
            {"role": "user", "content": agg_prompt}
        ]

        agg_response = llm_client.chat(
            messages, purpose="self_consistency_aggregate",
            attempt=attempt_index, out_dir=out_dir, max_tokens=1500
        )

        if not agg_response:
            return PatchResult(diff_text="", metadata={
                "strategy": "self_consistency",
                "n_samples": len(samples),
                "consensus_file": most_common_file,
                "consensus_ratio": round(consensus_ratio, 2),
                "reason": "no_aggregate_response"
            })

        # Strategy 1: Try to apply aggregated result directly
        success, result = apply_search_replace_directly(agg_response, workdir)
        if success:
            from ..apply_patch import rollback
            rollback(workdir)
            return PatchResult(diff_text=result, metadata={
                "strategy": "self_consistency",
                "n_samples": len(samples),
                "consensus_file": most_common_file,
                "consensus_ratio": round(consensus_ratio, 2),
                "aggregate_response": agg_response,
                "method": "direct_apply"
            })

        # Strategy 2: Extract and try again
        extracted = extract_search_replace(agg_response)
        if extracted:
            success2, result2 = apply_search_replace_directly(extracted, workdir)
            if success2:
                from ..apply_patch import rollback
                rollback(workdir)
                return PatchResult(diff_text=result2, metadata={
                    "strategy": "self_consistency",
                    "n_samples": len(samples),
                    "consensus_file": most_common_file,
                    "consensus_ratio": round(consensus_ratio, 2),
                    "aggregate_response": agg_response,
                    "method": "extract_then_apply"
                })

        # Strategy 3: Convert to diff format
        diff_text = search_replace_to_diff(agg_response, workdir)
        if diff_text:
            return PatchResult(diff_text=diff_text, metadata={
                "strategy": "self_consistency",
                "n_samples": len(samples),
                "consensus_file": most_common_file,
                "consensus_ratio": round(consensus_ratio, 2),
                "aggregate_response": agg_response,
                "method": "convert_to_diff"
            })

        return PatchResult(diff_text="", metadata={
            "strategy": "self_consistency",
            "n_samples": len(samples),
            "consensus_file": most_common_file,
            "consensus_ratio": round(consensus_ratio, 2),
            "reason": "patch_extraction_failed"
        })
