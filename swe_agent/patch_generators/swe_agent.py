# swe_agent/patch_generators/swe_agent.py
"""
Strategy: ReAct loop.
  Each turn: LLM outputs Thought + Action (one of: read_file, search, submit_patch)
  Harness executes action, returns Observation.
  Loop ends on submit_patch or budget exhausted.
  Max turns = MAX_LLM_CALLS_PER_ATTEMPT.

Uses search-replace format for more reliable patch generation.
"""
from pathlib import Path
import re
import json
from .base import PatchGenerator, PatchResult
from ..config import MAX_LLM_CALLS_PER_ATTEMPT


SYSTEM = """You are a software engineer fixing a bug. Respond with:
Thought: <reasoning>
Action: <one of the actions below>

Available actions:
  read_file(path, start_line, end_line) - returns file lines with line numbers
  search(pattern, path)                  - returns matching lines
  submit_patch(search_replace)           - submit fix in search-replace format

Rules:
- Output ONLY in the format above. No markdown. One action per turn.
- When you submit_patch, use search-replace format:
  FILE: path/to/File.java
  SEARCH: exact code to find (copy exactly from read_file output)
  REPLACE: new code with the fix

CRITICAL - Understanding Stack Traces:
When you see a stack trace like:
  at HelperClass.helperMethod(Helper.java:100)
  at CallerClass.callerMethod(Caller.java:50)

The bug is typically in Caller.java at line 50, NOT in Helper.java!
- The caller is calling the wrong helper, or with wrong conditions
- Fix the CALLER's condition/logic, not the helper method

COMMON BUG PATTERNS:
- Off-by-one errors: "> 8" should be ">= 8" or vice versa
- Missing null checks before method calls
- Wrong method called for edge cases (e.g., calling createInteger when value needs Long)

CRITICAL REQUIREMENTS FOR PATCHES:
1. You MUST read the file first using read_file to see the EXACT code
2. SEARCH must match the source code EXACTLY - character for character, including:
   - Same variable names (e.g., "str" not "string", "val" not "value")
   - Same indentation (spaces vs tabs)
   - Same whitespace
3. Copy the code DIRECTLY from the read_file output - do not retype or paraphrase
4. Include 5-10 lines of context before and after the change
5. You MUST submit_patch before your turns run out - submit even a partial fix

WORKFLOW:
1. First, use read_file to examine the suspicious code locations
2. Identify the exact lines that need to change
3. Copy those lines EXACTLY into SEARCH
4. Write the fixed version into REPLACE
5. Call submit_patch with your fix"""


class SWEAgentPatchGenerator(PatchGenerator):

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
        history = [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": self._initial_message(
                bug_id, trigger_tests, failing_info, localization_hits)}
        ]

        last_valid_patch = None

        accumulated_patch_parts = []

        for turn in range(MAX_LLM_CALLS_PER_ATTEMPT):
            # Add reminder on last turn
            if turn == MAX_LLM_CALLS_PER_ATTEMPT - 1:
                history.append({
                    "role": "user",
                    "content": "FINAL TURN: You must submit_patch now with a COMPLETE search-replace patch. Include FILE:, SEARCH: (with full code context), and REPLACE: sections. Even a partial fix is better than no fix."
                })

            response = llm_client.chat(
                history,
                purpose=f"react_turn_{turn}",
                attempt=attempt_index,
                out_dir=out_dir,
                max_tokens=1500
            )
            history.append({"role": "assistant", "content": response})

            action, arg = self._parse_action(response)

            if action == "submit_patch":
                # Store this as a valid patch candidate
                last_valid_patch = arg
                accumulated_patch_parts.append(arg)

            # Also check if response contains FILE:/SEARCH:/REPLACE: pattern directly
            sr_match = re.search(r'FILE:\s*(\S+)\s*SEARCH:\s*([\s\S]*?)(?:REPLACE:\s*([\s\S]*?))?(?=\nThought:|\nAction:|\Z)', response or "")
            if sr_match and not last_valid_patch:
                filepath = sr_match.group(1)
                search_text = sr_match.group(2).strip()
                replace_text = sr_match.group(3).strip() if sr_match.group(3) else ""
                accumulated_patch_parts.append(f"FILE: {filepath}\nSEARCH: {search_text}\nREPLACE: {replace_text}")

            # Execute tool and add observation
            observation = self._execute_tool(action, arg, workdir)
            if observation:
                history.append({"role": "user", "content": f"Observation:\n{observation}"})

        # Convert search-replace to unified diff
        # Try accumulated patches first, then last_valid_patch
        for patch_text in accumulated_patch_parts:
            if patch_text:
                diff_text = search_replace_to_diff(patch_text, workdir)
                if diff_text:
                    return PatchResult(
                        diff_text=diff_text,
                        metadata={"strategy": "swe_agent", "turns": MAX_LLM_CALLS_PER_ATTEMPT, "raw_response": patch_text}
                    )

        if last_valid_patch:
            diff_text = search_replace_to_diff(last_valid_patch, workdir)
            if diff_text:
                return PatchResult(
                    diff_text=diff_text,
                    metadata={"strategy": "swe_agent", "turns": MAX_LLM_CALLS_PER_ATTEMPT, "raw_response": last_valid_patch}
                )

        return PatchResult(
            diff_text="",
            metadata={"strategy": "swe_agent", "reason": "no_patch_submitted"}
        )

    @staticmethod
    def _initial_message(bug_id, trigger_tests, failing_info, loc_hits) -> str:
        traces = "\n".join(
            fi["error_message"] + "\n" + fi["stack_trace"][:500]
            for fi in list(failing_info.values())[:2]
        )
        files = list(set(h.filepath for h in loc_hits)) if loc_hits else []
        return (f"Bug: {bug_id}\nFailing tests: {trigger_tests}\n"
                f"Traces:\n{traces}\n\n"
                f"Suspicious files: {files}\n\n"
                "Use read_file to examine code, then submit_patch with your fix. "
                "You have limited turns - use them wisely.")

    @staticmethod
    def _parse_action(response: str) -> tuple[str, str]:
        """Parse Thought + Action format from response."""
        if not response:
            return "", ""

        # First check if response contains FILE:/SEARCH:/REPLACE: pattern anywhere
        # This is the most important pattern for patch submission
        # Be more lenient - match FILE: and SEARCH: even if REPLACE is missing or incomplete
        sr_match = re.search(r'FILE:\s*(\S+)\s*SEARCH:\s*([\s\S]*?)(?:REPLACE:\s*([\s\S]*?))?(?=\nThought:|\nAction:|\Z)', response or "")
        if sr_match:
            filepath = sr_match.group(1)
            search_text = sr_match.group(2).strip()
            replace_text = sr_match.group(3).strip() if sr_match.group(3) else ""
            return "submit_patch", f"FILE: {filepath}\nSEARCH: {search_text}\nREPLACE: {replace_text}"

        # Look for Action: pattern with parentheses
        action_match = re.search(r"Action:\s*(\w+)\s*\(([^)]*)\)", response or "")
        if action_match:
            action = action_match.group(1)
            args = action_match.group(2)
            return action, args

        # Look for Action: pattern without parentheses (e.g., "Action: submit_patch")
        action_simple_match = re.search(r"Action:\s*(\w+)\s*$", response or "", re.MULTILINE)
        if action_simple_match:
            action = action_simple_match.group(1)
            # Check if there's content after this line that looks like patch data
            action_pos = action_simple_match.end()
            remaining = response[action_pos:].strip()
            if remaining and not remaining.startswith("Thought:"):
                return action, remaining
            return action, ""

        # Fallback: look for submit_patch with content in parentheses
        submit_match = re.search(r"submit_patch\(([\s\S]+?)\)", response or "")
        if submit_match:
            return "submit_patch", submit_match.group(1)

        return "", ""

    @staticmethod
    def _execute_tool(action: str, arg: str, workdir: Path) -> str:
        """Execute a tool action and return observation."""
        if action == "read_file":
            # Parse path, start, end from args
            match = re.match(r'["\']?([^"\',]+)["\']?,?\s*(\d+)?,?\s*(\d+)?', arg)
            if match:
                path = match.group(1)
                start = int(match.group(2)) if match.group(2) else 1
                end = int(match.group(3)) if match.group(3) else 50
                try:
                    file_path = workdir / path
                    if file_path.is_dir():
                        return f"Error: '{path}' is a directory"
                    if file_path.exists():
                        lines = file_path.read_text().splitlines()
                        return "\n".join(
                            f"{i + start}: {l}"
                            for i, l in enumerate(lines[start - 1:end])
                        )
                    return f"File not found: {path}"
                except Exception as e:
                    return f"Error reading file: {e}"
            return "Invalid arguments for read_file (use: path, start_line, end_line)"

        elif action == "search":
            match = re.match(r'["\']?([^"\',]+)["\']?,?\s*["\']?([^"\',]*)["\']?', arg)
            if match:
                pattern = match.group(1)
                path = match.group(2) if match.group(2) else "src"
                import subprocess
                try:
                    result = subprocess.run(
                        ["grep", "-rn", "--include=*.java", pattern, str(workdir / path)],
                        capture_output=True, text=True, timeout=10
                    )
                    return result.stdout[:2000] or "(no matches)"
                except Exception:
                    return "(search error)"
            return "Invalid arguments for search (use: pattern, path)"

        return ""  # No output for unknown actions or submit_patch


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
        search_text = search_text.strip()
        replace_text = replace_text.strip()

        # Find the file in workdir
        full_path = workdir / filepath
        if not full_path.exists():
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
            content_lines = content.split('\n')

            # Find exact match first
            start_idx = -1
            for i in range(len(content_lines) - len(search_lines) + 1):
                if all(content_lines[i + j] == s_line for j, s_line in enumerate(search_lines)):
                    start_idx = i
                    break

            # Try fuzzy match if exact fails
            if start_idx == -1:
                search_stripped = [l.strip() for l in search_lines]
                for i in range(len(content_lines) - len(search_lines) + 1):
                    if all(content_lines[i + j].strip() == s_line for j, s_line in enumerate(search_stripped)):
                        start_idx = i
                        break

            if start_idx == -1:
                continue

            end_idx = start_idx + len(search_lines)
            old_start = start_idx + 1
            new_start = start_idx + 1

            # Build hunk with context
            context_before = max(0, start_idx - 3)
            context_after = min(len(content_lines), end_idx + 3)

            hunk_lines = [f"@@ -{old_start},{len(search_lines)} +{new_start},{len(replace_lines)} @@"]

            for i in range(context_before, start_idx):
                hunk_lines.append(f" {content_lines[i]}")
            for line in search_lines:
                hunk_lines.append(f"-{line}")
            for line in replace_lines:
                hunk_lines.append(f"+{line}")
            for i in range(end_idx, context_after):
                hunk_lines.append(f" {content_lines[i]}")

            diff_lines.append(f"--- a/{filepath}")
            diff_lines.append(f"+++ b/{filepath}")
            diff_lines.extend(hunk_lines)

        except Exception:
            continue

    return '\n'.join(diff_lines) if diff_lines else ""
