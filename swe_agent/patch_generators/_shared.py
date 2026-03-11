# swe_agent/patch_generators/_shared.py
"""
Common prompt building blocks reused across prompting-strategy baselines.
"""
import re


# ── Context builders ──────────────────────────────────────────────────────────

def build_fail_context(bug_id: str, trigger_tests: list[str],
                       failing_info: dict, max_chars: int = 3000) -> str:
    """Format failing test info into a compact context block."""
    traces = "\n\n".join(
        (fi["error_message"] + "\n" + fi["stack_trace"])[:1000]
        for fi in failing_info.values()
    )
    clean_tests = [t for t in trigger_tests if "::" in t or "." in t]
    return (
        f"Bug: {bug_id}\n"
        f"Failing tests: {', '.join(clean_tests[:3])}\n\n"
        f"Error output:\n{traces[:max_chars]}"
    )


def build_location_context(localization_hits: list,
                            workdir, max_hits: int = 3) -> str:
    """Read source snippets for the top-N localization hits."""
    from pathlib import Path
    blocks = []
    for h in localization_hits[:max_hits]:
        fp = Path(workdir) / h.filepath
        if fp.exists():
            try:
                lines = fp.read_text().splitlines()
                s = max(0, h.start_line - 1)
                e = min(len(lines), h.end_line + 1)
                numbered = "\n".join(
                    f"{h.start_line + i}: {l}"
                    for i, l in enumerate(lines[s:e])
                )
                blocks.append(
                    f"### {h.filepath} lines {h.start_line}-{h.end_line}\n"
                    f"```java\n{numbered}\n```"
                )
            except Exception:
                blocks.append(f"### {h.filepath} (unreadable)")
        else:
            blocks.append(f"### {h.filepath} (not found)")
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


def extract_search_replace(text: str) -> str:
    """Return the first valid SEARCH/REPLACE block, or '' if none found.

    Tries multiple patterns:
    1. Standard FILE:/SEARCH:/REPLACE: format
    2. Markdown-fenced code blocks
    3. Simple SEARCH:/REPLACE: without file
    """
    if not text:
        return ""

    # Try standard pattern first
    m = SEARCH_REPLACE_RE.search(text)
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

    return ""


# ── Common system prompt ──────────────────────────────────────────────────────

PATCH_SYSTEM = """You are an automated Java program repair system.

Output format — use EXACTLY this structure, no markdown fences around it:

FILE: path/to/File.java
SEARCH: <exact source lines to replace — must match the file character-for-character>
REPLACE: <corrected lines>

Rules:
- SEARCH must be a verbatim copy of existing source including all whitespace and indentation.
- Include 5-10 lines of context around the changed lines so the match is unique.
- Fix only the buggy logic; do not modify unrelated code.
- When you see a stack trace, the bug is in the CALLER, not the called method."""
