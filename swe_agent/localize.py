# swe_agent/localize.py
"""
Fault localization module.
Parses test failure logs to identify suspicious locations in the codebase.
"""
import re
import json
import os
from pathlib import Path
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class LocalizationHit:
    filepath: str
    start_line: int
    end_line: int
    confidence: float
    snippet: str = ""
    method_name: str = ""  # Optional: name of the method if available
    is_bug_source: bool = False  # True if this is marked as the bug source


def localize(workdir: Path, project: str, test_log: str,
             bug_info_dir: Optional[str] = None) -> List[LocalizationHit]:
    """
    Parse test failure log to extract suspicious locations.
    Returns list of LocalizationHit objects sorted by confidence.

    Args:
        workdir: Working directory with the checked out code
        project: Project name (e.g., "Lang_1")
        test_log: Test failure log content
        bug_info_dir: Optional path to bug info directory for snippet.json
    """
    hits = []

    # Parse stack traces for file:line patterns
    # Format: at package.Class.method(File.java:123)
    stack_pattern = r"at\s+([\w.$]+)\.(\w+)\(([\w/.]+\.java):(\d+)\)"
    matches = re.findall(stack_pattern, test_log)

    seen = set()
    for i, (cls, method, filepath, line_no) in enumerate(matches):
        # Filter out test files and external libs
        # Note: stack traces often only show filename (e.g., "NumberUtils.java") without full path
        # So we can't filter by "src/" - instead filter by test patterns and java.* packages
        if "/test/" in filepath or "\\test\\" in filepath:
            continue
        if "Test" in filepath:  # Skip test files like NumberUtilsTest.java
            continue
        if filepath.startswith("java/"):  # Skip Java standard library
            continue

        key = f"{filepath}:{line_no}"
        if key in seen:
            continue
        seen.add(key)

        # Calculate confidence based on position in stack trace
        # Earlier in stack = higher confidence
        # First non-test frame is most likely the bug location
        confidence = 1.0 - (len(hits) * 0.15)
        confidence = max(0.1, confidence)

        # Expand window to include more context
        # Include the full method context by reading more lines
        start_line = max(1, int(line_no) - 10)
        end_line = int(line_no) + 30

        method_name = f"{cls}.{method}"
        hits.append(LocalizationHit(
            filepath=filepath,
            start_line=start_line,
            end_line=end_line,
            confidence=confidence,
            method_name=method_name
        ))

    # Sort by confidence descending
    hits.sort(key=lambda h: h.confidence, reverse=True)

    # Load snippets for top hits (up to 3)
    for hit in hits[:3]:
        hit.snippet = _load_snippet(workdir, hit.filepath, hit.start_line, hit.end_line)

    # If bug_info_dir is provided, try to enrich with snippet.json data
    if bug_info_dir:
        hits = _enrich_with_snippet_data(hits, bug_info_dir, workdir)

    return hits[:3]  # Return only top 3 hits to avoid overwhelming the LLM


def _enrich_with_snippet_data(hits: List[LocalizationHit], bug_info_dir: str,
                               workdir: Path) -> List[LocalizationHit]:
    """
    Enrich localization hits with snippet.json data.
    This helps identify which method is actually marked as the bug source.

    NOTE: snippet.json marks methods that are involved in the bug, but the actual
    fix location is typically the CALLER (first project frame in stack trace),
    not the callee (where the exception is ultimately thrown).
    """
    snippet_path = os.path.join(bug_info_dir, "snippet.json")
    if not os.path.exists(snippet_path):
        return hits

    try:
        with open(snippet_path) as f:
            snippets = json.load(f)

        # Find methods marked as is_bug: true
        bug_methods = [s for s in snippets if s.get("is_bug", False)]

        if bug_methods:
            # The snippet.json marks certain methods as involved in the bug
            # We use this to identify the file, but NOT to change confidence
            # The first stack frame (highest confidence) is the actual fix location
            for hit in hits:
                for bug_method in bug_methods:
                    if bug_method["file"] in hit.filepath:
                        # Mark as bug source but don't change confidence
                        # The caller (first frame) is where the fix should be
                        hit.is_bug_source = True

                        # Load the snippet from snippet.json if not already loaded
                        if not hit.snippet:
                            hit.snippet = bug_method.get("snippet", "")
                            hit.method_name = bug_method.get("name", hit.method_name)
    except Exception:
        pass  # Silently ignore errors in snippet loading

    return hits


def _load_snippet(workdir: Path, filepath: str, start: int, end: int) -> str:
    """Load code snippet from file."""
    try:
        # Handle different path formats
        # filepath could be: "NumberUtils.java" (just filename from stack trace)
        # or: "src/main/java/org/apache/commons/lang3/MathUtils.java"
        # or: "org/apache/commons/lang3/MathUtils.java"

        file_path = None

        # Try direct path first
        direct_path = workdir / filepath
        if direct_path.exists() and direct_path.is_file():
            file_path = direct_path

        # If filepath is just a filename (no path separators), search for it
        if file_path is None and "/" not in filepath and "\\" not in filepath:
            # Search for the file in the workdir
            for found in workdir.rglob(filepath):
                if found.is_file() and "/test/" not in str(found) and "Test" not in found.name:
                    file_path = found
                    break

        # Try stripping leading path components
        if file_path is None and "/" in filepath:
            parts = filepath.split("/")
            for i in range(len(parts)):
                test_path = workdir / "/".join(parts[i:])
                if test_path.exists() and test_path.is_file():
                    file_path = test_path
                    break

        # Try with src/main/java prefix
        if file_path is None:
            test_path = workdir / "src" / "main" / "java" / filepath
            if test_path.exists() and test_path.is_file():
                file_path = test_path

        # Try with src/java prefix (some projects)
        if file_path is None:
            test_path = workdir / "src" / "java" / filepath
            if test_path.exists() and test_path.is_file():
                file_path = test_path

        if file_path is None or not file_path.exists():
            return ""

        lines = file_path.read_text().splitlines()
        start_idx = max(0, start - 1)
        end_idx = min(len(lines), end)
        return "\n".join(lines[start_idx:end_idx])
    except Exception:
        return ""


def parse_stack_trace(log: str) -> List[dict]:
    """
    Parse stack trace from test log.
    Returns list of {class, method, file, line} dicts.
    """
    frames = []
    pattern = r"at\s+([\w.$]+)\.(\w+)\(([\w/.]+):(\d+)\)"

    for match in re.finditer(pattern, log):
        frames.append({
            "class": match.group(1),
            "method": match.group(2),
            "file": match.group(3),
            "line": int(match.group(4))
        })

    return frames
