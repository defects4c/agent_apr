# swe_agent/__init__.py
"""
SWE-Agent: Multi-Baseline Agent-Based LLM Repair for Defects4J

This package implements five baseline APR approaches:
- agentless: Direct patch generation from failing info
- swe_agent: ReAct loop with file tools
- openhands: Budgeted tool-use loop
- openclaw: Structured 3-call pipeline (Search → Analyze → Patch)
- claude_code: Skill-based read/search/propose loop
"""

from .config import (
    D4J_HOME, D4J_FOLDER, REPOS_DIR, WORKSPACE_ROOT,
    OPENAI_API_KEY, OPENAI_BASE_URL, GPT_MODEL,
    MAX_ATTEMPTS_PER_BUG, MAX_LLM_CALLS_PER_ATTEMPT, MAX_LLM_CALLS_PER_BUG,
    MAX_TOKENS_PER_BUG, MAX_PATCH_LINES, MAX_FILES_CHANGED,
    TIMEOUT_PATCH_GEN, TIMEOUT_COMPILE, TIMEOUT_FUNC_TEST, TIMEOUT_REG_TEST,
    BASELINES
)
from .reason import (
    PATCH_APPLY_HUNK_FAILED, PATCH_APPLY_PATH_NOT_FOUND, PATCH_SIZE_EXCEEDED,
    JAVAC_SYMBOL_NOT_FOUND, JAVAC_TYPE_MISMATCH, BUILD_FAILED_UNKNOWN,
    TRIGGER_TEST_STILL_FAILING, NEW_FAILURES_INTRODUCED, REPAIRED,
    parse_build_reason, parse_test_reason
)

__version__ = "1.0.0"
__all__ = [
    "D4J_HOME", "D4J_FOLDER", "REPOS_DIR", "WORKSPACE_ROOT",
    "OPENAI_API_KEY", "OPENAI_BASE_URL", "GPT_MODEL",
    "MAX_ATTEMPTS_PER_BUG", "MAX_LLM_CALLS_PER_ATTEMPT", "MAX_LLM_CALLS_PER_BUG",
    "MAX_TOKENS_PER_BUG", "MAX_PATCH_LINES", "MAX_FILES_CHANGED",
    "BASELINES",
    "PATCH_APPLY_HUNK_FAILED", "PATCH_APPLY_PATH_NOT_FOUND", "REPAIRED",
    "parse_build_reason", "parse_test_reason",
]
