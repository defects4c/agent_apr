# swe_agent/budget.py
from .config import MAX_PATCH_LINES, MAX_FILES_CHANGED


class BudgetExceededError(Exception):
    """Raised when a patch violates budget constraints."""
    pass


class BudgetManager:
    """
    Validates patch constraints before applying.
    Checks: max_patch_lines, max_files_changed
    """

    def check_patch(self, diff_text: str) -> bool:
        """
        Validate that the patch adheres to budget constraints.
        Raises BudgetExceededError if violated.
        """
        if not diff_text.strip():
            raise BudgetExceededError("Empty patch")

        lines = diff_text.splitlines()
        if len(lines) > MAX_PATCH_LINES:
            raise BudgetExceededError(
                f"Patch too long: {len(lines)} > {MAX_PATCH_LINES} lines")

        # Count files changed (lines starting with "+++ ")
        files_changed = sum(1 for line in lines if line.startswith("+++ "))
        if files_changed > MAX_FILES_CHANGED:
            raise BudgetExceededError(
                f"Too many files changed: {files_changed} > {MAX_FILES_CHANGED}")

        return True
