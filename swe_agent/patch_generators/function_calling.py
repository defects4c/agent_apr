# swe_agent/patch_generators/function_calling.py
"""
Function Calling patch generator.
Paper: OpenAI API (2023) - structured tool-use via JSON schemas.
Strategy: Use structured function calling to get a well-structured patch response.
  The model is prompted to output a JSON object with patch information.
Budget: 1 LLM call per attempt.

Note: This uses JSON schema prompting to elicit structured output,
  then parses the JSON to extract the patch.
"""
import json
from pathlib import Path
from .base import PatchGenerator, PatchResult
from ._shared import build_fail_context, build_location_context, PATCH_SYSTEM

FUNCTION_CALL_SYSTEM = """You are a program repair assistant that outputs structured JSON responses.

You must respond with a JSON object in this exact format:
{
  "file": "path/to/File.java",
  "search": "exact code to find",
  "replace": "corrected code",
  "explanation": "brief explanation of the fix"
}

Do not include any text outside the JSON object."""

FUNCTION_CALL_USER = """{fail_context}

## Suspicious location(s)
{location_context}

## Task
Generate a patch to fix the bug. Output your response as a JSON object.

The patch should:
1. Specify the exact file path
2. Provide the exact lines to search for (must match the source exactly)
3. Provide the corrected lines to replace them with
4. Briefly explain what the fix does

Respond with ONLY the JSON object, no other text."""


class FunctionCallingPatchGenerator(PatchGenerator):

    def generate_patch(
        self,
        bug_id, workdir, failing_info, trigger_tests,
        localization_hits, attempt_index, out_dir, llm_client,
    ) -> PatchResult:

        fail_ctx = build_fail_context(bug_id, trigger_tests, failing_info)
        loc_ctx = build_location_context(localization_hits, workdir)

        prompt = FUNCTION_CALL_USER.format(
            fail_context=fail_ctx,
            location_context=loc_ctx,
        )

        response = llm_client.chat(
            [{"role": "system", "content": FUNCTION_CALL_SYSTEM},
             {"role": "user", "content": prompt}],
            purpose="function_calling_patch_gen", attempt=attempt_index,
            out_dir=out_dir, max_tokens=1500,
        )

        if not response:
            return PatchResult(diff_text="", metadata={
                "strategy": "function_calling", "raw_response": "",
                "reason": "empty_response"
            })

        # Try to parse JSON from response
        patch_json = self._extract_json(response)
        if not patch_json:
            return PatchResult(diff_text="", metadata={
                "strategy": "function_calling", "raw_response": response,
                "reason": "json_parse_failed"
            })

        # Convert JSON to SEARCH/REPLACE format
        try:
            file_path = patch_json.get("file", "")
            search = patch_json.get("search", "")
            replace = patch_json.get("replace", "")
            explanation = patch_json.get("explanation", "")

            if not file_path or not search or not replace:
                return PatchResult(diff_text="", metadata={
                    "strategy": "function_calling", "raw_response": response,
                    "reason": "missing_fields_in_json"
                })

            # Build unified diff
            diff_text = self._json_to_diff(file_path, search, replace, workdir)

            return PatchResult(
                diff_text=diff_text,
                metadata={
                    "strategy": "function_calling",
                    "json_response": patch_json,
                    "raw_response": response,
                    "explanation": explanation,
                },
            )
        except Exception as e:
            return PatchResult(diff_text="", metadata={
                "strategy": "function_calling", "raw_response": response,
                "reason": f"diff_generation_failed: {str(e)}"
            })

    def _extract_json(self, text: str) -> dict | None:
        """Extract JSON object from text, handling markdown code blocks."""
        text = text.strip()

        # Try to find JSON between { and }
        start = text.find("{")
        end = text.rfind("}") + 1
        if start == -1 or end == 0:
            return None

        json_str = text[start:end]

        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            # Try to fix common issues
            try:
                # Remove trailing commas
                import re
                json_str = re.sub(r',\s*}', '}', json_str)
                json_str = re.sub(r',\s*]', ']', json_str)
                return json.loads(json_str)
            except:
                return None

    def _json_to_diff(self, file_path: str, search: str, replace: str,
                       workdir: Path) -> str:
        """Convert JSON patch to unified diff format."""
        import os

        full_path = workdir / file_path
        if not full_path.exists():
            # Try to find the file
            for root, dirs, files in os.walk(workdir):
                if os.path.basename(file_path) in files:
                    full_path = os.path.join(root, os.path.basename(file_path))
                    break

        if not full_path.exists():
            return ""

        original_content = full_path.read_text()
        search_lines = search.rstrip().split('\n')
        replace_lines = replace.rstrip().split('\n')

        # Find the line numbers
        original_lines = original_content.split('\n')
        start_line = -1
        for i in range(len(original_lines) - len(search_lines) + 1):
            if original_lines[i:i + len(search_lines)] == search_lines:
                start_line = i
                break

        if start_line == -1:
            return ""

        # Generate unified diff
        import difflib
        diff = difflib.unified_diff(
            original_lines,
            original_lines[:start_line] + replace_lines + original_lines[start_line + len(search_lines):],
            fromfile=f"a/{file_path}",
            tofile=f"b/{file_path}",
            lineterm='',
        )
        return '\n'.join(diff)
