# swe_agent/apply_patch.py
"""
Patch application module.
Handles unified diffs and search-replace formats with rollback support.
"""
import re
import subprocess
from pathlib import Path
from typing import Tuple, List, Optional


def validate_diff_format(diff_text: str) -> Tuple[bool, str]:
    """
    Validate that the diff is in valid unified diff format.
    Returns (is_valid, error_message).
    """
    if not diff_text or not diff_text.strip():
        return False, "Empty diff"

    lines = diff_text.splitlines()

    # Check for basic unified diff structure
    has_header = False
    has_hunk = False

    for i, line in enumerate(lines):
        # Check for file headers
        if line.startswith("--- a/") or line.startswith("--- "):
            has_header = True
            # Check for corresponding +++ line
            if i + 1 < len(lines) and not (lines[i + 1].startswith("+++ b/") or lines[i + 1].startswith("+++ ")):
                return False, f"Missing '+++' line after '---' at line {i + 1}"

        # Check for hunk headers
        if line.startswith("@@"):
            has_hunk = True
            # Validate hunk header format: @@ -start,count +start,count @@
            hunk_pattern = r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@"
            if not re.match(hunk_pattern, line):
                return False, f"Invalid hunk header format at line {i + 1}: {line[:50]}"

    if not has_header:
        return False, "Missing file header (--- a/path and +++ b/path)"

    if not has_hunk:
        return False, "Missing hunk header (@@ ... @@)"

    return True, ""


def apply_patch(diff_text: str, workdir: Path) -> Tuple[bool, str]:
    """
    Apply a unified diff patch to the working directory.
    Uses multiple strategies with fallbacks for robustness.
    Returns (success, error_message).
    """
    if not diff_text or not diff_text.strip():
        return False, "Empty patch"

    # Check if this is a search-replace format patch
    if "FILE:" in diff_text and "SEARCH:" in diff_text and "REPLACE:" in diff_text:
        # Use search-replace application directly
        return apply_search_replace(diff_text, workdir)

    # First validate the diff format
    is_valid, error = validate_diff_format(diff_text)
    if not is_valid:
        # Try to extract a valid diff from the response
        extracted_diff = extract_diff_from_response(diff_text)
        if extracted_diff:
            diff_text = extracted_diff
            is_valid, error = validate_diff_format(diff_text)

    if not is_valid:
        return False, f"Invalid diff format: {error}"

    # Strategy 1: Try git apply with increasing fuzz factors
    for fuzz in [0, 1, 2, 3]:
        try:
            fuzz_args = ["--fuzz={}".format(fuzz)] if fuzz > 0 else []
            result = subprocess.run(
                ["git", "apply", "--whitespace=fix"] + fuzz_args + ["-"],
                input=diff_text,
                capture_output=True,
                text=True,
                cwd=workdir
            )
            if result.returncode == 0:
                return True, ""
        except Exception:
            pass

    # Strategy 2: Try patch command (more lenient than git apply)
    try:
        result = subprocess.run(
            ["patch", "-p1", "--force"],
            input=diff_text,
            capture_output=True,
            text=True,
            cwd=workdir
        )
        if result.returncode == 0:
            return True, ""
    except Exception:
        pass

    # Strategy 3: Try manual patch application for simple single-file patches
    manual_result = apply_patch_manually(diff_text, workdir)
    if manual_result[0]:
        return manual_result

    # All strategies failed
    return False, "Patch application failed with all strategies"


def apply_search_replace(search_replace_text: str, workdir: Path) -> Tuple[bool, str]:
    """
    Apply a search-replace format patch.
    Returns (success, error_message).
    """
    import re
    # Match explicit newline after SEARCH: and REPLACE: to preserve leading whitespace in content
    # Pattern: FILE: <path>\nSEARCH:\n<content>\nREPLACE:\n<content>
    pattern = r'FILE:\s*(\S+)\s*SEARCH:\n(.*?)\nREPLACE:\n(.*?)(?=\nFILE:|\Z)'
    matches = re.findall(pattern, search_replace_text, re.DOTALL)

    if not matches:
        return False, "No valid SEARCH/REPLACE blocks found"

    for filepath, search_text, replace_text in matches:
        # Strip only trailing whitespace to preserve leading indentation on each line
        search_text = search_text.rstrip()
        replace_text = replace_text.rstrip()

        full_path = workdir / filepath
        if not full_path.exists():
            # Try to find the file
            for p in workdir.rglob(filepath.split('/')[-1]):
                if p.is_file():
                    full_path = p
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
            new_content_lines = []
            for j, replace_line in enumerate(replace_lines):
                if start_idx + j < len(content_lines):
                    original_line = content_lines[start_idx + j]
                    original_indent = len(original_line) - len(original_line.lstrip())
                    original_whitespace = original_line[:original_indent]
                    replace_stripped = replace_line.lstrip()
                    new_content_lines.append(original_whitespace + replace_stripped)
                else:
                    new_content_lines.append(replace_line)

            final_content_lines = content_lines[:start_idx] + new_content_lines + content_lines[end_idx:]
            original_ending = '\n' if content.endswith('\n') else ''
            new_content = '\n'.join(final_content_lines)
            if original_ending and not new_content.endswith('\n'):
                new_content += '\n'
            full_path.write_text(new_content)
            return True, ""

        except Exception as e:
            return False, f"Error applying patch to {filepath}: {str(e)}"

    return True, ""


def apply_patch_manually(diff_text: str, workdir: Path) -> Tuple[bool, str]:
    """
    Manually apply a unified diff patch by parsing and applying hunks.
    This is more lenient than git apply for whitespace issues.
    Returns (success, error_message).
    """
    try:
        lines = diff_text.splitlines()
        current_file = None
        current_hunk_start = None
        current_hunk_lines = []
        hunks = []

        i = 0
        while i < len(lines):
            line = lines[i]

            # Parse file header
            if line.startswith("--- a/"):
                current_file = line[6:]  # Remove "--- a/"
            elif line.startswith("--- "):
                current_file = line[4:]  # Remove "--- "

            # Parse hunk header
            if line.startswith("@@"):
                # Save previous hunk
                if current_file and current_hunk_start is not None:
                    hunks.append((current_file, current_hunk_start, current_hunk_lines))

                # Parse new hunk header: @@ -start,count +start,count @@
                match = re.match(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", line)
                if match:
                    current_hunk_start = int(match.group(1))
                    current_hunk_lines = []
                i += 1
                continue

            # Collect hunk lines
            if current_hunk_start is not None and (line.startswith('-') or line.startswith('+') or line.startswith(' ')):
                current_hunk_lines.append(line)

            i += 1

        # Save last hunk
        if current_file and current_hunk_start is not None:
            hunks.append((current_file, current_hunk_start, current_hunk_lines))

        # Apply hunks
        for filepath, hunk_start, hunk_lines in hunks:
            if not apply_hunk_manually(workdir, filepath, hunk_start, hunk_lines):
                return False, f"Failed to apply hunk at line {hunk_start} in {filepath}"

        return True, ""
    except Exception as e:
        return False, f"Manual patch application error: {str(e)}"


def apply_hunk_manually(workdir: Path, filepath: str, hunk_start: int, hunk_lines: list) -> bool:
    """
    Apply a single hunk to a file with fuzzy matching.
    """
    try:
        full_path = workdir / filepath
        if not full_path.exists():
            # Try to find the file
            for p in workdir.rglob(filepath.split('/')[-1]):
                if p.is_file():
                    full_path = p
                    break
            else:
                return False

        content = full_path.read_text()
        content_lines = content.splitlines(keepends=True)
        if not content.endswith('\n'):
            content_lines = [l + '\n' for l in content.splitlines()]

        # Extract search and replace patterns from hunk
        search_lines = []
        replace_lines = []
        for line in hunk_lines:
            if line.startswith('-'):
                search_lines.append(line[1:])
            elif line.startswith('+'):
                replace_lines.append(line[1:])
            elif line.startswith(' '):
                search_lines.append(line[1:])
                replace_lines.append(line[1:])

        # Try to find the search pattern in the file
        search_start = hunk_start - 1  # Convert to 0-indexed

        # Try exact match first
        match_found = True
        for j, s_line in enumerate(search_lines):
            if search_start + j >= len(content_lines):
                match_found = False
                break
            if content_lines[search_start + j].strip() != s_line.strip():
                match_found = False
                break

        if match_found and search_lines:
            # Apply the replacement
            new_content_lines = content_lines[:search_start] + replace_lines + content_lines[search_start + len(search_lines):]
            full_path.write_text(''.join(new_content_lines))
            return True

        # Try fuzzy match - find the search pattern anywhere in the file
        for start_idx in range(len(content_lines) - len(search_lines) + 1):
            match_found = True
            for j, s_line in enumerate(search_lines):
                if content_lines[start_idx + j].strip() != s_line.strip():
                    match_found = False
                    break
            if match_found:
                new_content_lines = content_lines[:start_idx] + replace_lines + content_lines[start_idx + len(search_lines):]
                full_path.write_text(''.join(new_content_lines))
                return True

        return False
    except Exception:
        return False


def extract_diff_from_response(response: str) -> Optional[str]:
    """
    Extract a unified diff from a response that may contain extra text.
    Looks for patterns like:
    - Lines starting with --- a/
    - Lines starting with +++ b/
    - Hunk headers @@ ... @@
    """
    if not response:
        return None

    lines = response.splitlines()
    diff_lines = []
    in_diff = False
    has_header = False
    has_hunk = False

    for i, line in enumerate(lines):
        # Look for diff start
        if line.startswith("--- a/") or line.startswith("--- "):
            in_diff = True
            has_header = True
            diff_lines.append(line)
        elif in_diff:
            # Look for +++ line after ---
            if not has_hunk and diff_lines and diff_lines[-1].startswith("---"):
                if line.startswith("+++ b/") or line.startswith("+++ "):
                    diff_lines.append(line)
                    continue

            # Look for hunk header
            if line.startswith("@@"):
                has_hunk = True
                diff_lines.append(line)
            elif has_hunk:
                # Collect hunk lines (context, removed, added)
                if (line.startswith("-") or line.startswith("+") or
                    line.startswith(" ") or line.startswith("\\")):
                    diff_lines.append(line)
                elif line.strip() == "" and diff_lines:
                    # Allow empty lines within hunk
                    diff_lines.append(line)
                elif not line.startswith("#") and not line.startswith("Thought") and not line.startswith("Action"):
                    # Might be trailing content, check if it looks like code
                    if line.startswith(" ") or line.startswith("\t"):
                        # Could be indented code in context
                        diff_lines.append(line)
                    else:
                        # End of diff
                        break

    # Validate we got a proper diff
    diff_text = "\n".join(diff_lines)
    if has_header and has_hunk:
        return diff_text

    # Fallback: try to find any valid diff pattern
    if "--- a/" in response and "@@" in response:
        # Try a more aggressive extraction
        start_idx = response.find("--- a/")
        if start_idx >= 0:
            end_idx = response.rfind("\n+")
            if end_idx > start_idx:
                return response[start_idx:end_idx + 50]

    return None


def init_git_baseline(workdir: Path):
    """
    Initialize git baseline for rollback support.
    Creates a baseline commit to reset to on failure.
    """
    try:
        # Check if already a git repo
        git_dir = workdir / ".git"
        if not git_dir.exists():
            subprocess.run(["git", "init"], cwd=workdir, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "agent@local"], cwd=workdir, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Agent"], cwd=workdir, check=True, capture_output=True)

        # Check if there are uncommitted changes to commit first
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=workdir,
            capture_output=True,
            text=True
        )
        if result.stdout.strip():
            # Add all changes
            subprocess.run(["git", "add", "-A"], cwd=workdir, check=True, capture_output=True)

        # Create baseline commit if none exists
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=workdir,
            capture_output=True,
            text=True
        )
        if not result.stdout.strip() or result.returncode != 0:
            subprocess.run(["git", "add", "-A"], cwd=workdir, check=True, capture_output=True)
            subprocess.run(
                ["git", "commit", "-m", "Baseline commit"],
                cwd=workdir,
                check=True,
                capture_output=True
            )
    except Exception:
        pass  # Git init is best-effort


def rollback(workdir: Path):
    """
    Rollback all changes since the baseline commit.
    Resets the working directory to the initial state.
    """
    try:
        # Hard reset to HEAD
        subprocess.run(
            ["git", "reset", "--hard", "HEAD"],
            cwd=workdir,
            check=True,
            capture_output=True
        )
        # Clean untracked files
        subprocess.run(
            ["git", "clean", "-fd"],
            cwd=workdir,
            check=True,
            capture_output=True
        )
    except Exception:
        pass  # Rollback is best-effort


def get_patch_diff(workdir: Path) -> str:
    """Get the current git diff as a unified diff string."""
    try:
        result = subprocess.run(
            ["git", "diff"],
            cwd=workdir,
            capture_output=True,
            text=True
        )
        return result.stdout
    except Exception:
        return ""
