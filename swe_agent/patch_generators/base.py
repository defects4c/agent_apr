# swe_agent/patch_generators/base.py
from abc import ABC, abstractmethod
from pathlib import Path
from dataclasses import dataclass


@dataclass
class PatchResult:
    diff_text: str          # unified diff string; "" means generation failed
    metadata:  dict         # prompt_sha256, model, localization targets, etc.


class PatchGenerator(ABC):
    """Common interface all five baselines implement."""

    @abstractmethod
    def generate_patch(
        self,
        bug_id:            str,
        workdir:           Path,
        failing_info:      dict,   # {test_name: {error_message, stack_trace}}
        trigger_tests:     list[str],
        localization_hits: list,   # list[LocalizationHit]
        attempt_index:     int,
        out_dir:           Path,
        llm_client,                # LLMClient
    ) -> PatchResult:
        """
        Generate a patch for the given bug.

        Args:
            bug_id: Bug identifier (e.g., "Lang_1")
            workdir: Working directory with checked out code
            failing_info: Dictionary of test failure information
            trigger_tests: List of trigger test names
            localization_hits: List of suspicious code locations
            attempt_index: Current attempt number (1-indexed)
            out_dir: Output directory for logs
            llm_client: LLMClient instance for making LLM calls

        Returns:
            PatchResult with diff_text and metadata
        """
        pass
