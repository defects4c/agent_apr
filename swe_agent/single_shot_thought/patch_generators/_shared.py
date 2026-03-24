# swe_agent/patch_generators/_shared.py
"""
Common prompt building blocks reused across prompting-strategy baselines.
"""
import re
from pathlib import Path
from typing import Optional


# ── Context builders ──────────────────────────────────────────────────────────

def build_fail_context(bug_id: str, trigger_tests: list[str],
                       failing_info: dict, max_chars: int = 3000) -> str:
    """Format failing test info into a compact context block."""
    # Handle None or empty failing_info
    if not failing_info:
        failing_info = {}

    traces_parts = []
    for fi in failing_info.values():
        if fi is None:
            continue
        error_msg = fi.get("error_message", "") if isinstance(fi, dict) else ""
        stack_trace = fi.get("stack_trace", "") if isinstance(fi, dict) else ""
        traces_parts.append((error_msg + "\n" + stack_trace)[:1000])

    traces = "\n\n".join(traces_parts) if traces_parts else "No failure information available."

    clean_tests = [t for t in trigger_tests if "::" in t or "." in t] if trigger_tests else []
    return (
        f"Bug: {bug_id}\n"
        f"Failing tests: {', '.join(clean_tests[:3]) if clean_tests else 'Unknown'}\n\n"
        f"Error output:\n{traces[:max_chars]}"
    )


def _find_source_file(workdir, filepath: str) -> Optional[Path]:
    """Find a source file in the workdir, handling various path formats.

    Args:
        workdir: Working directory path
        filepath: File path from stack trace (could be just filename or partial path)

    Returns:
        Path to the file if found, None otherwise
    """
    from pathlib import Path
    workdir = Path(workdir)

    # Try direct path first
    direct_path = workdir / filepath
    if direct_path.exists() and direct_path.is_file():
        return direct_path

    # If filepath is just a filename (no path separators), search for it
    if "/" not in filepath and "\\" not in filepath:
        for found in workdir.rglob(filepath):
            if (found.is_file() and
                "/test/" not in str(found) and
                "Test" not in found.name):
                return found

    # Try stripping leading path components
    if "/" in filepath:
        parts = filepath.split("/")
        for i in range(len(parts)):
            test_path = workdir / "/".join(parts[i:])
            if test_path.exists() and test_path.is_file():
                return test_path

    # Try with src/main/java prefix
    test_path = workdir / "src" / "main" / "java" / filepath
    if test_path.exists() and test_path.is_file():
        return test_path

    # Try with src/java prefix (some projects)
    test_path = workdir / "src" / "java" / filepath
    if test_path.exists() and test_path.is_file():
        return test_path

    return None


def build_location_context(localization_hits: list,
                            workdir, max_hits: int = 3) -> str:
    """Read source snippets for the top-N localization hits."""
    from pathlib import Path

    # Handle None or empty localization_hits
    if not localization_hits:
        return "No specific code locations identified. See stack traces in failure information."

    blocks = []
    for h in localization_hits[:max_hits]:
        # Skip None hits
        if h is None:
            continue

        # Safely get filepath attribute
        filepath = getattr(h, 'filepath', None)
        if not filepath:
            continue

        # Use the file finder to resolve the path
        fp = _find_source_file(workdir, filepath)
        if fp is not None:
            try:
                lines = fp.read_text().splitlines()
                start_line = getattr(h, 'start_line', 1)
                end_line = getattr(h, 'end_line', start_line + 10)
                s = max(0, start_line - 1)
                e = min(len(lines), end_line + 1)
                numbered = "\n".join(
                    f"{start_line + i}: {l}"
                    for i, l in enumerate(lines[s:e])
                )
                blocks.append(
                    f"### {filepath} lines {start_line}-{end_line}\n"
                    f"```java\n{numbered}\n```"
                )
            except Exception:
                blocks.append(f"### {filepath} (unreadable)")
        else:
            blocks.append(f"### {filepath} (not found)")

    if not blocks:
        return "No specific code locations identified. See stack traces in failure information."

    return "\n\n".join(blocks)


# ── Output parsers ────────────────────────────────────────────────────────────

# Multiple regex patterns to try for extracting SEARCH/REPLACE blocks
SEARCH_REPLACE_RE = re.compile(
    r'FILE:\s*(\S+)\s*SEARCH:\s*(.*?)\s*REPLACE:\s*(.*?)(?=\nFILE:|\Z)',
    re.DOTALL | re.IGNORECASE,
)

# Pattern for markdown-fenced code blocks with search/replace
MARKDOWN_SEARCH_REPLACE_RE = re.compile(
    r'```(?:\w+)?\s*\n?FILE:\s*(\S+)\s*\n?SEARCH:\s*(.*?)\n?REPLACE:\s*(.*?)\n?```',
    re.DOTALL | re.IGNORECASE,
)

# Pattern for just SEARCH/REPLACE without FILE (try to extract file from context)
SIMPLE_SEARCH_REPLACE_RE = re.compile(
    r'SEARCH:\s*(.*?)\s*REPLACE:\s*(.*?)(?=\nSEARCH:|\Z)',
    re.DOTALL | re.IGNORECASE,
)

# Pattern to extract file path from various formats
FILE_PATH_RE = re.compile(
    r'(?:FILE:\s*)?([a-zA-Z0-9_/$.-]+\.(?:java|py|js|ts|cpp|c|h))',
    re.IGNORECASE,
)

# Pattern for unified diff format (alternative output format)
UNIFIED_DIFF_RE = re.compile(
    r'---\s*(a/)?([^\n]+)\n\+\+\+\s*(b/)?([^\n]+)\n@@.*?@@',
    re.DOTALL,
)

# More flexible pattern for FILE/SEARCH/REPLACE with newlines
FLEXIBLE_SEARCH_REPLACE_RE = re.compile(
    r'FILE:\s*(\S+)\s*\n?\s*SEARCH:\s*\n?(.*?)\n?\s*REPLACE:\s*\n?(.*?)(?=\n\s*FILE:|\Z)',
    re.DOTALL | re.IGNORECASE,
)


def extract_search_replace(text: str) -> str:
    """Return the first valid SEARCH/REPLACE block, or '' if none found.

    Tries multiple patterns in order:
    1. Standard FILE:/SEARCH:/REPLACE: format
    2. Flexible format with newlines after colons
    3. Markdown-fenced code blocks
    4. Simple SEARCH:/REPLACE: without file
    5. Unified diff format (detected but passed through)

    Returns empty string if no valid patch format found.
    """
    if not text:
        return ""

    # Try standard pattern first
    m = SEARCH_REPLACE_RE.search(text)
    if m:
        return f"FILE: {m.group(1).strip()}\nSEARCH: {m.group(2).strip()}\nREPLACE: {m.group(3).strip()}"

    # Try flexible pattern (handles newlines after colons)
    m = FLEXIBLE_SEARCH_REPLACE_RE.search(text)
    if m:
        return f"FILE: {m.group(1).strip()}\nSEARCH: {m.group(2).strip()}\nREPLACE: {m.group(3).strip()}"

    # Try markdown-fenced pattern
    m = MARKDOWN_SEARCH_REPLACE_RE.search(text)
    if m:
        return f"FILE: {m.group(1).strip()}\nSEARCH: {m.group(2).strip()}\nREPLACE: {m.group(3).strip()}"

    # Try simple SEARCH/REPLACE pattern and extract file from context
    m = SIMPLE_SEARCH_REPLACE_RE.search(text)
    if m:
        # Try to find a file path anywhere in the text
        file_match = FILE_PATH_RE.search(text)
        if file_match:
            return f"FILE: {file_match.group(1).strip()}\nSEARCH: {m.group(1).strip()}\nREPLACE: {m.group(2).strip()}"

    # If we have a unified diff, return it as-is (will be handled by apply_patch)
    if UNIFIED_DIFF_RE.search(text):
        return text

    return ""


def extract_unified_diff(text: str) -> str:
    """Extract unified diff from text if present."""
    if not text:
        return ""

    # Look for standard diff header
    diff_match = UNIFIED_DIFF_RE.search(text)
    if diff_match:
        # Extract the full diff (from first --- line to end or next non-diff content)
        start = diff_match.start()
        return text[start:].strip()

    return ""


# ── Common system prompt ──────────────────────────────────────────────────────

PATCH_SYSTEM = """You are an automated Java program repair system that fixes bugs in Java code.

Your task is to analyze the failing test, stack trace, and source code, then produce a patch that fixes the bug.

## Output Format (CRITICAL - MUST FOLLOW EXACTLY)

You MUST output your patch in this EXACT format. No markdown code fences around it:

FILE: path/to/File.java
SEARCH: <copy exact lines from source including ALL leading whitespace>
REPLACE: <your fix with same indentation>

## CRITICAL: Indentation Must Match Exactly

The SEARCH block must match the source file CHARACTER-FOR-CHARACTER:
- Copy ALL leading whitespace (spaces or tabs) from each line
- If a line has 8 leading spaces in the source, your SEARCH must have 8 leading spaces
- If a line has 4 spaces in the source, your SEARCH must have 4 spaces
- Do NOT strip or change any whitespace

## Critical Rules

1. **SEARCH must match EXACTLY**:
   - Copy the source code character-for-character
   - Include ALL leading whitespace/indentation on EVERY line
   - Match exact line breaks
   - Keep exact comments (unless you're fixing them)

2. **Include enough context**:
   - Include 5-10 lines BEFORE the change
   - Include 5-10 lines AFTER the change
   - This ensures the search is unique

3. **Understand stack traces correctly**:
   - When you see: `at Helper.method(Helper.java:100)` followed by `at Caller.method(Caller.java:50)`
   - The bug is usually in Caller.java at line 50 (the CALLER), NOT in Helper.java
   - The caller is making the wrong call or with wrong conditions

4. **Common bug patterns to look for**:
   - Off-by-one errors: `> 8` should be `>= 8` or vice versa
   - Missing null checks: add `if (x != null)` before method calls
   - Wrong boundary conditions: `> 0` vs `>= 0`
   - Missing edge case handling: empty strings, zero values, max values
   - Incorrect method called for specific input types

5. **What NOT to do**:
   - Do NOT output explanations before the patch
   - Do NOT use markdown code fences (```) around the patch
   - Do NOT change unrelated code
   - Do NOT add new methods or imports unless absolutely necessary
   - Do NOT strip leading whitespace from SEARCH lines

## Example showing correct indentation

If the source code looks like this (note the 8 spaces of indentation):
```
        if (hexDigits > 16) {
            return createBigInteger(str);
        }
```

Your patch should look like:
FILE: src/main/java/org/example/Foo.java
SEARCH:
        if (hexDigits > 16) {
            return createBigInteger(str);
        }
REPLACE:
        if (hexDigits >= 16) {
            return createBigInteger(str);
        }

Notice: The 8 leading spaces are preserved in both SEARCH and REPLACE!

Remember: Output ONLY the patch in the exact format above. No explanations, no markdown fences."""


# ── Apply/convert helpers (used by got.py, tot.py) ───────────────────────────

def apply_search_replace_directly(text: str, workdir) -> tuple:
    """Apply a FILE/SEARCH/REPLACE block directly to files in workdir.
    Returns (success: bool, message: str).
    """
    from pathlib import Path
    workdir = Path(workdir)

    sr_text = extract_search_replace(text)
    if not sr_text:
        return False, "No SEARCH/REPLACE block found"

    # Parse FILE/SEARCH/REPLACE
    m = SEARCH_REPLACE_RE.search(sr_text)
    if not m:
        m = FLEXIBLE_SEARCH_REPLACE_RE.search(sr_text)
    if not m:
        return False, "Could not parse SEARCH/REPLACE block"

    filepath = m.group(1).strip()
    search_text = m.group(2).strip()
    replace_text = m.group(3).strip()

    # Find the file
    fp = _find_source_file(workdir, filepath)
    if fp is None:
        return False, f"File not found: {filepath}"

    try:
        content = fp.read_text()
        if search_text not in content:
            # Try fuzzy: strip each line and compare
            search_lines = search_text.split("\n")
            content_lines = content.split("\n")
            found = False
            for i in range(len(content_lines) - len(search_lines) + 1):
                if all(content_lines[i+j].strip() == search_lines[j].strip()
                       for j in range(len(search_lines))):
                    # Replace preserving original indentation
                    replace_lines = replace_text.split("\n")
                    new_lines = content_lines[:i]
                    for j, rl in enumerate(replace_lines):
                        if i + j < len(content_lines):
                            orig = content_lines[i + j]
                            indent = len(orig) - len(orig.lstrip())
                            new_lines.append(orig[:indent] + rl.lstrip())
                        else:
                            new_lines.append(rl)
                    new_lines.extend(content_lines[i + len(search_lines):])
                    fp.write_text("\n".join(new_lines))
                    found = True
                    break
            if not found:
                return False, f"SEARCH text not found in {filepath}"
        else:
            fp.write_text(content.replace(search_text, replace_text, 1))
        return True, "Applied"
    except Exception as e:
        return False, str(e)


def search_replace_to_diff(text: str, workdir) -> str:
    """Convert a FILE/SEARCH/REPLACE response into a unified diff string.
    Does NOT modify files — just generates the diff text.
    Returns the search-replace text as-is (apply_patch handles conversion).
    """
    sr_text = extract_search_replace(text)
    return sr_text if sr_text else ""
