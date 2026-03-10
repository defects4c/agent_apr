# swe_agent/patch_generators/openhands.py
"""
Strategy: OpenAI tool-use (function calling) loop.
  Tools: read_snippet, search_in_file, propose_patch
  Each tool call is one LLM interaction (counted against budget).
  context_lines_budget caps total lines read.
  Harness validates the diff from propose_patch — not OpenHands internally.

Uses search-replace format for more reliable patch generation.
"""
from pathlib import Path
import re
import json
from .base import PatchGenerator, PatchResult
from ..config import MAX_LLM_CALLS_PER_ATTEMPT, CONTEXT_LINES_PER_LOCATION, MAX_LOCATIONS_PER_ATTEMPT
from .agentless import search_replace_to_diff


TOOLS_DEFINITION = """Available tools:
1. read_snippet(path, start_line, end_line) - Read lines from a source file
2. search_in_file(path, pattern) - Search for a pattern in a file
3. propose_patch(search_replace) - Submit your fix in search-replace format

Respond with tool calls in the format:
TOOL: tool_name
ARGS: {"arg1": "value1", "arg2": "value2"}

For propose_patch, use search-replace format:
TOOL: propose_patch
ARGS: {"search_replace": "FILE: path/to/File.java\nSEARCH: exact code to find\nREPLACE: new code with fix"}

CRITICAL for propose_patch:
- SEARCH must match the source code EXACTLY - copy from read_snippet output
- Include 5-10 lines of context before and after the change

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
- Wrong method called for edge cases

You MUST propose a patch before your turns run out."""


class OpenHandsPatchGenerator(PatchGenerator):

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
        context_budget = CONTEXT_LINES_PER_LOCATION * MAX_LOCATIONS_PER_ATTEMPT
        context_used = 0

        messages = [
            {"role": "system", "content": TOOLS_DEFINITION},
            {"role": "user", "content": self._task_message(
                bug_id, trigger_tests, failing_info, localization_hits)}
        ]

        last_valid_patch = None
        accumulated_patches = []

        for turn in range(MAX_LLM_CALLS_PER_ATTEMPT):
            # Add reminder on last turn
            if turn == MAX_LLM_CALLS_PER_ATTEMPT - 1:
                messages.append({
                    "role": "user",
                    "content": "FINAL TURN: You must use propose_patch now with a COMPLETE search-replace patch. Include FILE:, SEARCH: (with full code context copied from read_snippet), and REPLACE: sections. Even a partial fix is acceptable."
                })

            response = llm_client.chat(
                messages,
                purpose=f"tool_turn_{turn}",
                attempt=attempt_index,
                out_dir=out_dir,
                max_tokens=1500
            )
            messages.append({"role": "assistant", "content": response})

            tool_name, tool_args = self._parse_tool_call(response)

            if tool_name == "propose_patch":
                patch_text = tool_args.get("search_replace", "")
                last_valid_patch = patch_text
                accumulated_patches.append(patch_text)

            # Also check if response contains FILE:/SEARCH:/REPLACE: pattern directly
            sr_match = re.search(r'FILE:\s*(\S+)\s*SEARCH:\s*([\s\S]*?)(?:REPLACE:\s*([\s\S]*?))?(?=\nTOOL:|\Z)', response or "")
            if sr_match:
                filepath = sr_match.group(1)
                search_text = sr_match.group(2).strip()
                replace_text = sr_match.group(3).strip() if sr_match.group(3) else ""
                patch_text = f"FILE: {filepath}\nSEARCH: {search_text}\nREPLACE: {replace_text}"
                accumulated_patches.append(patch_text)
                if not last_valid_patch:
                    last_valid_patch = patch_text

            if tool_name in ("read_snippet", "search_in_file"):
                result, lines_consumed = self._execute_tool(
                    tool_name, tool_args, workdir)
                context_used += lines_consumed
                if context_used > context_budget:
                    result = "[context budget exhausted - no more reads allowed]"
                messages.append({
                    "role": "user",
                    "content": f"Tool result ({tool_name}):\n{result}"
                })

        # Convert search-replace to unified diff
        # Try accumulated patches first
        for patch_text in accumulated_patches:
            if patch_text:
                diff_text = search_replace_to_diff(patch_text, workdir)
                if diff_text:
                    return PatchResult(
                        diff_text=diff_text,
                        metadata={
                            "strategy": "openhands",
                            "turns": MAX_LLM_CALLS_PER_ATTEMPT,
                            "context_lines_used": context_used,
                            "raw_response": patch_text
                        }
                    )

        if last_valid_patch:
            diff_text = search_replace_to_diff(last_valid_patch, workdir)
            return PatchResult(
                diff_text=diff_text,
                metadata={
                    "strategy": "openhands",
                    "turns": MAX_LLM_CALLS_PER_ATTEMPT,
                    "context_lines_used": context_used,
                    "raw_response": last_valid_patch
                }
            )

        return PatchResult(
            diff_text="",
            metadata={"strategy": "openhands", "reason": "no_patch_proposed"}
        )

    @staticmethod
    def _task_message(bug_id, trigger_tests, failing_info, loc_hits) -> str:
        traces = "\n\n".join(
            fi["error_message"] + "\n" + fi["stack_trace"][:500]
            for fi in list(failing_info.values())[:2]
        )
        hints = "\n".join(f"  {h.filepath}:{h.start_line}" for h in loc_hits) if loc_hits else ""
        return (f"Fix bug {bug_id}.\n\nFailing tests: {trigger_tests}\n\n"
                f"Traces:\n{traces}\n\n"
                f"Suspected locations:\n{hints}\n\n"
                "Use the available tools to explore the code and propose a fix. "
                "You have limited turns - use them wisely and submit a patch before running out.")

    @staticmethod
    def _parse_tool_call(response: str) -> tuple[str, dict]:
        """Parse TOOL:/ARGS: format from response."""
        if not response:
            return "", {}
        tool_match = re.search(r"TOOL:\s*(\w+)", response)
        args_match = re.search(r"ARGS:\s*(\{[\s\S]*?\})", response)

        tool_name = tool_match.group(1) if tool_match else ""

        if not args_match:
            # Try to find JSON-like content after TOOL
            tool_full_match = re.search(r"TOOL:\s*(\w+)\s*([\s\S]*)", response)
            if tool_full_match:
                tool_name = tool_full_match.group(1)
                json_part = tool_full_match.group(2).strip()
                # Try to parse the remaining text as JSON
                try:
                    args = json.loads(json_part)
                    return tool_name, args
                except:
                    pass
            # Try to extract search_replace directly from response
            sr_match = re.search(r'FILE:\s*(\S+)\s*SEARCH:\s*([\s\S]*?)(?:REPLACE:\s*([\s\S]*?))?(?=\nTOOL:|\Z)', response)
            if sr_match:
                filepath = sr_match.group(1)
                search_text = sr_match.group(2).strip()
                replace_text = sr_match.group(3).strip() if sr_match.group(3) else ""
                return "propose_patch", {"search_replace": f"FILE: {filepath}\nSEARCH: {search_text}\nREPLACE: {replace_text}"}
            return tool_name, {}

        try:
            args = json.loads(args_match.group(1))
        except json.JSONDecodeError:
            # Try to fix common JSON issues
            json_str = args_match.group(1)
            # Remove trailing commas
            json_str = re.sub(r',\s*}', '}', json_str)
            json_str = re.sub(r',\s*]', ']', json_str)
            # Try to extract search_replace content even if JSON is malformed
            sr_match = re.search(r'"search_replace"\s*:\s*"([\s\S]*?)"', json_str)
            if sr_match:
                args = {"search_replace": sr_match.group(1)}
            else:
                args = {}
        return tool_name, args

    @staticmethod
    def _execute_tool(tool_name: str, args: dict, workdir: Path) -> tuple[str, int]:
        """Execute a tool and return (result, lines_consumed)."""
        if tool_name == "read_snippet":
            path_str = args.get("path", "")
            path = workdir / path_str if not path_str.startswith('/') else Path(path_str)
            start = int(args.get("start_line", 1))
            end = int(args.get("end_line", 50))
            try:
                if path.is_dir():
                    return f"Error: '{path_str}' is a directory", 0
                if path.exists():
                    lines = path.read_text().splitlines()
                    snippet = lines[start - 1:end]
                    lines_consumed = len(snippet)
                    formatted = "\n".join(
                        f"{i + start}: {l}" for i, l in enumerate(snippet)
                    )
                    return formatted, lines_consumed
            except Exception:
                pass
            return f"File not found: {path_str}", 0

        elif tool_name == "search_in_file":
            path_str = args.get("path", "src")
            path = workdir / path_str if not path_str.startswith('/') else Path(path_str)
            pattern = args.get("pattern", "")
            import subprocess
            try:
                result = subprocess.run(
                    ["grep", "-rn", "--include=*.java", pattern, str(path)],
                    capture_output=True, text=True, timeout=10
                )
                matches = result.stdout[:2000].splitlines()
                lines_consumed = len(matches)
                return result.stdout[:2000] or "(no matches)", lines_consumed
            except Exception:
                return "(search error)", 0

        return f"Unknown tool: {tool_name}", 0
