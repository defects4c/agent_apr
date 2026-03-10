# swe_agent/patch_generators/claude_code.py
"""
Strategy: skill-based loop with search-replace format.
Skill set: read_file, search, write_patch.

Uses search-replace format for more reliable patch generation.
"""
from pathlib import Path
import re
import json
import subprocess
from .base import PatchGenerator, PatchResult
from ..config import MAX_LLM_CALLS_PER_ATTEMPT
from .agentless import search_replace_to_diff


SKILL_SYSTEM = """You are Claude Code, an expert software repair agent.
You have three skills:
  read_file(path, start, end)  - returns lines of code with line numbers
  search(pattern)              - searches project source for a regex pattern
  write_patch(search_replace)  - submit your repair in search-replace format

Think step by step. Use skills to gather context, then write_patch once confident.
Output format per turn:
  SKILL: <skill_name>
  ARGS: <json args>

IMPORTANT:
- When you have identified the fix, use write_patch with search-replace format
- Format for write_patch ARGS: {"patch": "FILE: path\\nSEARCH: code\\nREPLACE: new code"}
- SEARCH must match the source code EXACTLY - copy from read_file output
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
- Wrong method called for edge cases (e.g., calling createInteger when value needs Long)

You MUST submit a patch before your turns run out. Even a partial fix is better than no fix.
Do not explain or use markdown."""


class ClaudeCodePatchGenerator(PatchGenerator):

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
            {"role": "system", "content": SKILL_SYSTEM},
            {"role": "user", "content": self._task_msg(
                bug_id, trigger_tests, failing_info, localization_hits)},
        ]

        last_valid_patch = None
        accumulated_patches = []

        for turn in range(MAX_LLM_CALLS_PER_ATTEMPT):
            # Add reminder on last turn
            if turn == MAX_LLM_CALLS_PER_ATTEMPT - 1:
                history.append({
                    "role": "user",
                    "content": "FINAL TURN: You must use write_patch now with a COMPLETE search-replace patch. Include FILE:, SEARCH: (with full code context), and REPLACE: sections. Even a partial fix is acceptable."
                })

            resp = llm_client.chat(
                history,
                purpose=f"skill_turn_{turn}",
                attempt=attempt_index,
                out_dir=out_dir,
                max_tokens=1500
            )
            history.append({"role": "assistant", "content": resp})

            skill, args = self._parse_skill(resp)

            if skill == "write_patch":
                patch_text = args.get("patch", "")
                # Handle case where REPLACE is a separate key (LLM output format variation)
                if patch_text and "REPLACE" in args and "REPLACE:" not in patch_text:
                    patch_text = patch_text + "\nREPLACE: " + args["REPLACE"]
                last_valid_patch = patch_text
                accumulated_patches.append(patch_text)

            # Also check if response contains FILE:/SEARCH:/REPLACE: pattern directly
            sr_match = re.search(r'FILE:\s*(\S+)\s*SEARCH:\s*([\s\S]*?)(?:REPLACE:\s*([\s\S]*?))?(?=\nSKILL:|\Z)', resp or "")
            if sr_match:
                filepath = sr_match.group(1)
                search_text = sr_match.group(2).strip()
                replace_text = sr_match.group(3).strip() if sr_match.group(3) else ""
                patch_text = f"FILE: {filepath}\nSEARCH: {search_text}\nREPLACE: {replace_text}"
                accumulated_patches.append(patch_text)
                if not last_valid_patch:
                    last_valid_patch = patch_text

            observation = self._execute_skill(skill, args, workdir)
            if observation:
                history.append({"role": "user", "content": f"Result:\n{observation}"})

        # Convert search-replace to unified diff
        # Try accumulated patches first
        for patch_text in accumulated_patches:
            if patch_text:
                diff_text = search_replace_to_diff(patch_text, workdir)
                if diff_text:
                    return PatchResult(
                        diff_text=diff_text,
                        metadata={"strategy": "claude_code", "turns": MAX_LLM_CALLS_PER_ATTEMPT, "raw_patch": patch_text}
                    )

        if last_valid_patch:
            diff_text = search_replace_to_diff(last_valid_patch, workdir)
            if diff_text:
                return PatchResult(
                    diff_text=diff_text,
                    metadata={"strategy": "claude_code", "turns": MAX_LLM_CALLS_PER_ATTEMPT, "raw_patch": last_valid_patch}
                )

        return PatchResult(
            diff_text="",
            metadata={"strategy": "claude_code", "reason": "no_patch_submitted"}
        )

    @staticmethod
    def _task_msg(bug_id, trigger_tests, failing_info, loc_hits) -> str:
        traces = "\n\n".join(
            fi["error_message"] + "\n" + fi["stack_trace"][:500]
            for fi in list(failing_info.values())[:2]
        )
        hints = "\n".join(f"  {h.filepath}:{h.start_line}" for h in loc_hits) if loc_hits else ""
        return (f"Fix bug {bug_id}.\n\nFailing tests: {trigger_tests}\n\n"
                f"Traces:\n{traces}\n\n"
                f"Suspected locations:\n{hints}\n\n"
                "Use read_file to examine the code at the suspected locations, "
                "then use write_patch with a search-replace patch to fix the bug.")

    @staticmethod
    def _parse_skill(response: str) -> tuple[str, dict]:
        if not response:
            return "", {}
        sm = re.search(r"SKILL:\s*(\w+)", response)
        skill = sm.group(1) if sm else ""

        # Capture everything after ARGS: (not just first {} block)
        am = re.search(r"ARGS:\s*(.*)", response, re.DOTALL)

        if not am:
            # Try to find JSON-like content after SKILL
            skill_match = re.search(r"SKILL:\s*(\w+)\s*([\s\S]*)", response)
            if skill_match:
                skill = skill_match.group(1)
                json_part = skill_match.group(2).strip()
                # Try to parse the remaining text as JSON
                try:
                    args = json.loads(json_part)
                    return skill, args
                except:
                    pass
            return skill, {}

        try:
            args = json.loads(am.group(1))
        except json.JSONDecodeError:
            # Try to fix common JSON issues
            json_str = am.group(1)
            # Remove trailing commas
            json_str = re.sub(r',\s*}', '}', json_str)
            json_str = re.sub(r',\s*]', ']', json_str)
            # Try to extract patch content even if JSON is malformed
            patch_match = re.search(r'"patch"\s*:\s*"([\s\S]*?)"', json_str)
            if patch_match:
                args = {"patch": patch_match.group(1)}
            else:
                args = {}
        return skill, args

    @staticmethod
    def _execute_skill(skill: str, args: dict, workdir: Path) -> str:
        if skill == "read_file":
            path_str = args.get("path", "")
            # Handle both absolute and relative paths
            if not path_str.startswith('/'):
                path = workdir / path_str
            else:
                path = Path(path_str)
            start = int(args.get("start", 1))
            end = int(args.get("end", 50))
            try:
                if not path.exists():
                    return f"File not found: {path_str}"
                if path.is_dir():
                    return f"Error: '{path_str}' is a directory, not a file"
                lines = path.read_text().splitlines()
                return "\n".join(
                    f"{i + start}: {l}"
                    for i, l in enumerate(lines[start - 1:end])
                )
            except FileNotFoundError:
                return f"File not found: {path_str}"
            except Exception as e:
                return f"Error reading file: {e}"

        if skill == "search":
            pattern = args.get("pattern", "")
            try:
                result = subprocess.run(
                    ["grep", "-rn", "--include=*.java", pattern, str(workdir / "src")],
                    capture_output=True, text=True, timeout=10
                )
                return result.stdout[:2000] or "(no matches)"
            except Exception:
                return "(search error)"

        if skill == "write_patch":
            return "Patch received. Converting to diff format..."

        return f"Unknown skill: {skill}"
