# swe_agent/patch_generators/openclaw.py
"""
Strategy: fixed 3-call pipeline (Search -> Analyze -> Patch).
  Call 1: identify suspicious methods -> JSON {suspicious_methods: [...]}
  Call 2: analyze methods -> JSON {root_cause, fix_strategy}
  Call 3: generate search-replace patch from analysis

Uses search-replace format for more reliable patch generation.
"""
from pathlib import Path
import re
import json
from .base import PatchGenerator, PatchResult
from .agentless import search_replace_to_diff


class OpenClawPatchGenerator(PatchGenerator):

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
        traces = self._format_traces(failing_info)

        # Use localization hits if available
        if localization_hits:
            suspicious = [f"{h.filepath}:{h.start_line}-{h.end_line}" for h in localization_hits[:3]]
        else:
            # Call 1: Search - identify suspicious locations
            search_prompt = (
                f"Bug {bug_id}. Failing traces:\n{traces}\n\n"
                "Based on the stack trace, identify the source file and line where the bug occurs. "
                "The bug is at the FIRST occurrence of the project's code in the stack trace (not in test files). "
                "Output ONLY JSON: {\"suspicious_locations\": [\"path/to/File.java:start-end\", ...]}"
            )
            raw = llm_client.chat(
                [{"role": "user", "content": search_prompt}],
                purpose="search",
                attempt=attempt_index,
                out_dir=out_dir,
                max_tokens=500
            )
            suspicious = self._parse_json(raw).get("suspicious_locations", [])
            if not suspicious:
                # Fallback to stack trace parsing
                suspicious = self._extract_locations_from_traces(traces)

        # Call 2: Analyze - load snippets and analyze
        snippets = self._load_snippets(suspicious, workdir)
        analyze_prompt = (
            f"Bug {bug_id}. Suspected locations with code:\n{snippets}\n\n"
            f"Failing traces:\n{traces}\n\n"
            "Analyze the root cause following these rules:\n"
            "1. The error occurs at the FIRST project frame in the stack trace (not in test files)\n"
            "2. Look at the condition/check at that line - that's what needs to be fixed\n"
            "3. The bug is in the CALLER, not in helper methods being called\n"
            "4. Common patterns: off-by-one errors (>, >=), missing null checks, wrong method for edge cases\n"
            "Output ONLY JSON: {\"root_cause\": \"...\", \"fix_strategy\": \"...\"}"
        )
        raw2 = llm_client.chat(
            [{"role": "user", "content": analyze_prompt}],
            purpose="analyze",
            attempt=attempt_index,
            out_dir=out_dir,
            max_tokens=600
        )
        analysis = self._parse_json(raw2)

        # Call 3: Patch - generate search-replace patch
        patch_prompt = (
            f"Bug {bug_id}.\n\n"
            f"Root cause: {analysis.get('root_cause', 'Unknown')}\n"
            f"Fix strategy: {analysis.get('fix_strategy', 'Fix the identified issue')}\n\n"
            f"Relevant code:\n{snippets}\n\n"
            "Generate a search-replace patch to fix the bug.\n\n"
            "OUTPUT FORMAT (exactly as shown):\n"
            "FILE: path/to/File.java\n"
            "SEARCH: <exact code from snippets above - copy character-for-character>\n"
            "REPLACE: <fixed code with same indentation>\n\n"
            "CRITICAL RULES:\n"
            "1. SEARCH must match the source EXACTLY - including all whitespace and indentation\n"
            "2. Include 5-10 lines of context before and after the change\n"
            "3. Fix the condition AT the line where the error occurs (from stack trace)\n"
            "4. Do NOT modify helper methods - fix the calling code\n\n"
            "Example:\n"
            "FILE: src/main/java/org/example/Foo.java\n"
            "SEARCH:\n"
            "    if (x > 10) {\n"
            "        return small;\n"
            "    }\n"
            "    return large;\n"
            "REPLACE:\n"
            "    if (x >= 10) {\n"
            "        return small;\n"
            "    }\n"
            "    return large;"
        )
        raw_patch = llm_client.chat(
            [{"role": "user", "content": patch_prompt}],
            purpose="patch_gen",
            attempt=attempt_index,
            out_dir=out_dir,
            max_tokens=1500
        )

        # Convert search-replace to unified diff
        diff_text = search_replace_to_diff(raw_patch or "", workdir)

        return PatchResult(
            diff_text=diff_text,
            metadata={
                "strategy": "openclaw",
                "root_cause": analysis.get("root_cause"),
                "suspicious_locations": suspicious,
                "raw_response": raw_patch or ""
            }
        )

    @staticmethod
    def _format_traces(failing_info: dict) -> str:
        return "\n\n".join(
            fi["error_message"] + "\n" + fi["stack_trace"][:600]
            for fi in list(failing_info.values())[:2]
        )

    @staticmethod
    def _parse_json(raw: str) -> dict:
        if not raw:
            return {}
        raw_clean = re.sub(r"```json\s*|\s*```", "", raw or "")
        raw_clean = re.sub(r"^[\s\S]*?\{", "{", raw_clean)  # Remove prefix before first {
        raw_clean = re.sub(r"\}[\s\S]*?$", "}", raw_clean)  # Remove suffix after last }
        try:
            return json.loads(raw_clean)
        except json.JSONDecodeError:
            return {}

    @staticmethod
    def _extract_locations_from_traces(traces: str) -> list[str]:
        """Extract file:line locations from stack traces."""
        locations = []
        pattern = r"at\s+[\w.$]+\.\w+\(([\w/.]+\.java):(\d+)\)"
        for match in re.finditer(pattern, traces):
            filepath = match.group(1)
            line = int(match.group(2))
            # Only include project files (not test files)
            if "src/main" in filepath or ("src/" in filepath and "/test/" not in filepath):
                locations.append(f"{filepath}:{max(1, line-5)}-{min(1000, line+20)}")
        return locations[:3] if locations else []

    @staticmethod
    def _load_snippets(locations: list[str], workdir: Path) -> str:
        """
        Load code snippets from file:line-line locations.
        Returns concatenated snippets with line numbers.
        """
        snippets = []
        for loc in locations:
            # Parse location string like "path/to/File.java:100-150"
            match = re.match(r'([^:]+):?(\d+)?-(\d+)?', loc)
            if not match:
                # Try just file path
                file_path = workdir / loc
                if file_path.exists() and not file_path.is_dir():
                    try:
                        content = file_path.read_text().splitlines()[:50]
                        snippet = "\n".join(f"{i+1}: {l}" for i, l in enumerate(content))
                        snippets.append(f"// {loc}\n{snippet}")
                    except Exception:
                        snippets.append(f"// {loc}\n[Could not read]")
                continue

            filepath = match.group(1)
            start_line = int(match.group(2)) if match.group(2) else 1
            end_line = int(match.group(3)) if match.group(3) else start_line + 50

            full_path = workdir / filepath
            if not full_path.exists():
                # Try to find the file
                for p in workdir.rglob(filepath.split('/')[-1]):
                    if p.is_file():
                        full_path = p
                        filepath = str(p.relative_to(workdir))
                        break
                else:
                    snippets.append(f"// {loc}\n[File not found]")
                    continue

            if full_path.is_dir():
                snippets.append(f"// {loc}\n[Is a directory]")
                continue

            try:
                content = full_path.read_text().splitlines()
                snippet_lines = content[start_line - 1:end_line]
                snippet = "\n".join(
                    f"{i + start_line}: {l}"
                    for i, l in enumerate(snippet_lines)
                )
                snippets.append(f"// {filepath}:{start_line}-{end_line}\n{snippet}")
            except Exception:
                snippets.append(f"// {loc}\n[Could not read]")

        return "\n\n".join(snippets) if snippets else "[No snippets found]"
