# swe_agent/patch_generators/self_consistency.py
"""
Self-Consistency patch generator.
Paper: Wang et al. (ICLR 2023)
Strategy: Generate N independent samples with temperature>0, select most common.
Budget: n_samples + 1 calls per attempt (samples + aggregator).

CRITICAL: temperature MUST be >0 (e.g. 0.7) for diverse samples.
With temperature=0 all N samples are identical, defeating the purpose.
"""
from pathlib import Path
from collections import Counter
from .base import PatchGenerator, PatchResult
from ._shared import (build_fail_context, build_location_context, PATCH_SYSTEM,
                       extract_search_replace)
from ..config import MAX_LLM_CALLS_PER_ATTEMPT

SAMPLE_TEMPERATURE = 0.7  # Wang et al. use 0.5-0.7

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
Output the final patch:
"""


class SelfConsistencyPatchGenerator(PatchGenerator):

    def __init__(self):
        self.n_samples = 3

    def generate_patch(
        self,
        bug_id, workdir, failing_info, trigger_tests,
        localization_hits, attempt_index, out_dir, llm_client,
    ) -> PatchResult:

        fail_ctx = build_fail_context(bug_id, trigger_tests, failing_info)
        loc_ctx = build_location_context(localization_hits, workdir)
        n_samples = min(self.n_samples, MAX_LLM_CALLS_PER_ATTEMPT - 1)

        samples = []
        for i in range(n_samples):
            response = llm_client.chat(
                [{"role": "system", "content": SAMPLE_SYSTEM},
                 {"role": "user", "content": SAMPLE_USER.format(
                     bug_id=bug_id, fail_context=fail_ctx, location_context=loc_ctx)}],
                purpose=f"sc_sample_{i+1}", attempt=attempt_index,
                out_dir=out_dir, max_tokens=1200,
                temperature=SAMPLE_TEMPERATURE,  # CRITICAL: diverse sampling
            )
            if response:
                samples.append(response)

        if not samples:
            return PatchResult(diff_text="", metadata={
                "strategy": "self_consistency", "reason": "no_samples"})

        # If only 1 sample or all identical, use it directly
        if len(samples) == 1 or len(set(s.strip() for s in samples)) == 1:
            diff_text = extract_search_replace(samples[0])
            return PatchResult(diff_text=diff_text, metadata={
                "strategy": "self_consistency", "n_samples": len(samples),
                "unique_samples": 1, "raw_samples": samples,
            })

        # Aggregate via LLM judge
        samples_text = "\n\n---\n\n".join(
            f"### Sample {i+1}:\n{s}" for i, s in enumerate(samples))

        aggregate = llm_client.chat(
            [{"role": "system", "content": AGGREGATE_SYSTEM},
             {"role": "user", "content": AGGREGATE_USER.format(
                 bug_id=bug_id, n_samples=len(samples), samples=samples_text)}],
            purpose="sc_aggregate", attempt=attempt_index,
            out_dir=out_dir, max_tokens=1200,
            temperature=0.0,  # deterministic aggregation
        )

        if not aggregate:
            diff_text = extract_search_replace(samples[0])
            return PatchResult(diff_text=diff_text, metadata={
                "strategy": "self_consistency", "n_samples": len(samples),
                "fallback": "first_sample",
            })

        diff_text = extract_search_replace(aggregate)
        return PatchResult(diff_text=diff_text, metadata={
            "strategy": "self_consistency", "n_samples": len(samples),
            "unique_samples": len(set(s.strip() for s in samples)),
            "sample_temperature": SAMPLE_TEMPERATURE,
            "raw_aggregate": aggregate,
        })
