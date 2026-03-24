# swe_agent/patch_generators/agentless.py
"""
Strategy:
  Call 1 -> generate search-replace patch from failing info + snippets
  Call 2 (optional) -> fix build error if compile fails (counted within MAX_LLM_CALLS_PER_ATTEMPT)

Uses SEARCH/REPLACE format instead of unified diffs for better reliability.
"""
import re
import subprocess
from pathlib import Path
from .base import PatchGenerator, PatchResult
from ..config import MAX_LLM_CALLS_PER_ATTEMPT


SYSTEM = """You are an automated program repair system fixing Java bugs.
Output ONLY a search-replace patch. No markdown fences. No explanations.

Use this EXACT format:

FILE: path/to/File.java
SEARCH: exact code to find (must match source exactly including whitespace)
REPLACE: new code to replace with

RULES:
1. SEARCH must match the source code EXACTLY - character for character, including ALL whitespace and indentation
2. Include enough context (5-10 lines before and after the change) to make the search unique
3. Only change the buggy logic - do not modify unrelated code
4. PRESERVE INDENTATION: Copy ALL leading spaces/tabs from the source - do not strip them

CRITICAL - Understanding Stack Traces:
When you see a stack trace like:
  at HelperClass.helperMethod(Helper.java:100)
  at CallerClass.callerMethod(Caller.java:50)

The bug is typically in Caller.java at line 50, NOT in Helper.java!
- The caller is calling the wrong helper, or with wrong conditions
- Fix the CALLER's condition/logic, not the helper method

For hex number parsing bugs specifically:
- If Integer.decode() fails on hex values like "80000000", the issue is the threshold check
- 8 hex digits can exceed Integer.MAX_VALUE (2147483647)
- The fix is usually changing "> 8" to ">= 8" or similar threshold adjustment

INDENTATION: Match the exact indentation style (tabs or spaces) used in the source file.
Every line in SEARCH must have the same leading whitespace as the source file."""

USER_TEMPLATE = """## Bug: {bug_id}

## Failing tests
{trigger_test_names}

## Failure messages and stack traces
{stack_trace_excerpt}

## Source code at suspicious locations (WITH LINE NUMBERS)
{locations}

## Task
Produce a search-replace patch that makes all failing tests pass without breaking other tests.

ANALYSIS STEPS:
1. Look at the FIRST project frame in the stack trace (closest to the test) - this is where the bug likely is
2. The exception is thrown deeper in the stack, but the BUG is where the wrong call is made
3. Look at the condition/logic at that line - what check is missing or incorrect?

CRITICAL REQUIREMENTS:
1. SEARCH must match the source code EXACTLY - copy the exact lines from the source code above
2. REPLACE should contain the fixed code with the same indentation
3. Include 5-10 lines of context before and after the change to ensure unique match

COMMON BUG PATTERNS:
- Off-by-one errors: "> 8" should be ">= 8" or vice versa
- Missing null checks before method calls
- Wrong method called for edge cases (e.g., calling createInteger when value needs Long)
- Incorrect boundary conditions in if statements

Output ONLY the patch in this format:

FILE: src/main/java/org/example/Foo.java
SEARCH:
    if (x > 10) {{
        return smallValue;
    }}
    return largeValue;
REPLACE:
    if (x >= 10) {{
        return smallValue;
    }}
    return largeValue;"""


def build_location_block(hits, workdir: Path) -> str:
    """Build location blocks with actual source code from workdir."""
    from ._shared import _find_source_file
    blocks = []
    for h in hits:
        # Use the shared file finder to resolve the path
        file_path = _find_source_file(workdir, h.filepath)

        if file_path is not None:
            try:
                content = file_path.read_text().splitlines()
                # Get the actual lines (adjust for 0-indexed)
                start_idx = max(0, h.start_line - 1)
                end_idx = min(len(content), h.end_line + 1)
                snippet_lines = content[start_idx:end_idx]

                # Format with line numbers
                numbered_snippet = '\n'.join(
                    f"{h.start_line + i}: {line}"
                    for i, line in enumerate(snippet_lines)
                )

                blocks.append(
                    f"### {h.filepath} lines {h.start_line}-{h.end_line} "
                    f"(confidence {h.confidence:.2f}, method: {h.method_name})\n"
                    f"```\n{numbered_snippet}\n```"
                )
            except Exception:
                blocks.append(f"### {h.filepath} (could not read)")
        else:
            blocks.append(f"### {h.filepath} (file not found)")

    return "\n\n".join(blocks)


def search_replace_to_diff(search_replace_text: str, workdir: Path) -> str:
    """
    Convert search-replace format to unified diff.

    Input format:
    FILE: path/to/File.java
    SEARCH: ...
    REPLACE: ...

    Output: unified diff string
    """
    diff_lines = []

    # Parse search-replace blocks - match until next FILE: or end of string
    pattern = r'FILE:\s*(\S+)\s*SEARCH:\s*(.*?)\s*REPLACE:\s*(.*?)(?=\nFILE:|\Z)'
    matches = re.findall(pattern, search_replace_text, re.DOTALL)

    for filepath, search_text, replace_text in matches:
        # Clean up search and replace - strip only trailing whitespace to preserve indentation
        search_text = search_text.rstrip()
        replace_text = replace_text.rstrip()

        # Find the file in workdir
        full_path = workdir / filepath
        if not full_path.exists():
            # Try to find the file
            for p in workdir.rglob(filepath.split('/')[-1]):
                if p.is_file():
                    full_path = p
                    filepath = str(p.relative_to(workdir))
                    break
            else:
                continue

        try:
            content = full_path.read_text()
            search_lines = search_text.split('\n')
            replace_lines = replace_text.split('\n')

            # Find the search text in the file
            content_lines = content.split('\n')

            # Try to find exact match
            start_idx = -1
            for i in range(len(content_lines) - len(search_lines) + 1):
                match = True
                for j, s_line in enumerate(search_lines):
                    if content_lines[i + j] != s_line:
                        match = False
                        break
                if match:
                    start_idx = i
                    break

            if start_idx == -1:
                # Try fuzzy match (strip whitespace for comparison)
                search_stripped = [l.strip() for l in search_lines]
                for i in range(len(content_lines) - len(search_lines) + 1):
                    match = True
                    for j, s_line in enumerate(search_stripped):
                        if content_lines[i + j].strip() != s_line:
                            match = False
                            break
                    if match:
                        start_idx = i
                        break

            if start_idx == -1:
                # Try with smaller context - find core lines
                min_lines = min(3, len(search_lines))
                for chunk_start in range(len(search_lines) - min_lines + 1):
                    chunk = search_lines[chunk_start:chunk_start + min_lines]
                    chunk_stripped = [l.strip() for l in chunk]
                    for i in range(len(content_lines) - min_lines + 1):
                        match = all(content_lines[i + j].strip() == chunk_stripped[j] for j in range(min_lines))
                        if match:
                            # Found a partial match - try to expand
                            start_idx = i - chunk_start
                            if start_idx >= 0:
                                break
                    if start_idx >= 0:
                        break

            if start_idx == -1:
                continue  # Cannot find match, skip this block

            # Generate unified diff
            end_idx = start_idx + len(search_lines)

            # Calculate line numbers (1-indexed)
            old_start = start_idx + 1
            new_start = start_idx + 1

            # Build hunk with context
            context_before = max(0, start_idx - 3)
            context_after = min(len(content_lines), end_idx + 3)

            # Add file header first
            diff_lines.append(f"--- a/{filepath}")
            diff_lines.append(f"+++ b/{filepath}")

            # Hunk header
            diff_lines.append(f"@@ -{old_start},{len(search_lines)} +{new_start},{len(replace_lines)} @@")

            # Context before
            for i in range(context_before, start_idx):
                diff_lines.append(f" {content_lines[i]}")

            # Removed lines
            for line in search_lines:
                diff_lines.append(f"-{line}")

            # Added lines
            for line in replace_lines:
                diff_lines.append(f"+{line}")

            # Context after
            for i in range(end_idx, context_after):
                diff_lines.append(f" {content_lines[i]}")

        except Exception:
            continue

    return '\n'.join(diff_lines) if diff_lines else ""


def apply_search_replace_directly(search_replace_text: str, workdir: Path) -> tuple[bool, str]:
    """
    Apply search-replace patch directly without converting to unified diff.
    This is more reliable than git apply for search-replace format.

    Returns (success, error_message or patch_diff)
    """
    pattern = r'FILE:\s*(\S+)\s*SEARCH:\s*(.*?)\s*REPLACE:\s*(.*?)(?=\nFILE:|\Z)'
    matches = re.findall(pattern, search_replace_text, re.DOTALL)

    if not matches:
        return False, "No valid SEARCH/REPLACE blocks found"

    all_diffs = []

    for filepath, search_text, replace_text in matches:
        search_text = search_text.strip()
        replace_text = replace_text.strip()

        full_path = workdir / filepath
        if not full_path.exists():
            # Try to find the file
            for p in workdir.rglob(filepath.split('/')[-1]):
                if p.is_file():
                    full_path = p
                    filepath = str(p.relative_to(workdir))
                    break
            else:
                return False, f"File not found: {filepath}"

        try:
            content = full_path.read_text()
            search_lines = search_text.split('\n')
            replace_lines = replace_text.split('\n')
            content_lines = content.split('\n')

            # Find exact match first
            start_idx = -1
            for i in range(len(content_lines) - len(search_lines) + 1):
                if all(content_lines[i + j] == search_lines[j] for j in range(len(search_lines))):
                    start_idx = i
                    break

            if start_idx == -1:
                # Try fuzzy match (strip whitespace for comparison)
                search_stripped = [l.strip() for l in search_lines]
                for i in range(len(content_lines) - len(search_lines) + 1):
                    if all(content_lines[i + j].strip() == search_stripped[j] for j in range(len(search_lines))):
                        start_idx = i
                        break

            if start_idx == -1:
                return False, f"Could not find SEARCH text in {filepath}"

            # Apply the replacement with indentation preservation
            end_idx = start_idx + len(search_lines)

            # Calculate indentation for each line from the original content
            new_content_lines = []
            for j, replace_line in enumerate(replace_lines):
                # If this position exists in the original search, preserve its indentation
                if start_idx + j < len(content_lines):
                    original_line = content_lines[start_idx + j]
                    # Get leading whitespace from original
                    original_indent = len(original_line) - len(original_line.lstrip())
                    original_whitespace = original_line[:original_indent]
                    # Get leading whitespace from replacement
                    replace_stripped = replace_line.lstrip()
                    # Apply original indentation
                    new_content_lines.append(original_whitespace + replace_stripped)
                else:
                    new_content_lines.append(replace_line)

            # Build final content
            final_content_lines = content_lines[:start_idx] + new_content_lines + content_lines[end_idx:]

            # Write the modified file
            # Preserve the original file's line ending
            original_ending = '\n' if content.endswith('\n') else ''
            new_content = '\n'.join(final_content_lines)
            if original_ending and not new_content.endswith('\n'):
                new_content += '\n'
            full_path.write_text(new_content)

            # Generate diff for this change
            diff_result = subprocess.run(
                ["git", "diff", "--no-color", str(full_path.relative_to(workdir))],
                cwd=workdir,
                capture_output=True,
                text=True
            )
            if diff_result.returncode == 0 and diff_result.stdout:
                all_diffs.append(diff_result.stdout)

        except Exception as e:
            return False, f"Error applying patch to {filepath}: {str(e)}"

    return True, "\n".join(all_diffs)


class AgentlessPatchGenerator(PatchGenerator):

    def generate_patch(
        self,
        bug_id: str,
        workdir: Path,
        failing_info: dict,
        trigger_tests: list[str],
        localization_hits: list,
        attempt_index: int,
        out_dir: Path,
        llm_client,
    ) -> PatchResult:
        # Format traces
        traces = "\n\n".join(
            (fi["error_message"] + "\n" + fi["stack_trace"])[:2000]
            for fi in failing_info.values()
        )

        # Build location blocks with ACTUAL source code from workdir
        if not localization_hits:
            # Fall back to using snippet.json data
            locations = self._build_locations_from_snippets(failing_info, workdir)
        else:
            locations = build_location_block(localization_hits[:3], workdir)

        # Filter trigger tests to only include test class names (not ant output)
        clean_trigger_tests = [t for t in trigger_tests if "::" in t or "." in t]
        if not clean_trigger_tests:
            clean_trigger_tests = list(failing_info.keys())

        prompt = USER_TEMPLATE.format(
            bug_id=bug_id,
            trigger_test_names="\n".join(clean_trigger_tests[:3]),
            stack_trace_excerpt=traces[:3000],
            locations=locations,
        )

        messages = [{"role": "system", "content": SYSTEM},
                    {"role": "user", "content": prompt}]

        response = llm_client.chat(
            messages,
            purpose="patch_gen",
            attempt=attempt_index,
            out_dir=out_dir,
            max_tokens=1500
        )

        # Return the raw response which contains the search-replace format
        # The apply_patch function will handle search-replace format directly
        return PatchResult(diff_text=response or "", metadata={"strategy": "agentless", "raw_response": response or "", "format": "search_replace"})

    @staticmethod
    def _build_locations_from_snippets(failing_info: dict, workdir: Path) -> str:
        """Build location blocks from snippet.json when localization fails."""
        # This is a fallback - in practice, the runner should pass localization hits
        # from snippet.json data
        return "No specific locations identified. See failing test stack traces above."
