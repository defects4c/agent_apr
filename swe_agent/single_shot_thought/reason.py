# swe_agent/reason.py

# ── Patch-apply codes ──────────────────────────────────────────────────────
PATCH_APPLY_HUNK_FAILED    = "PATCH_APPLY_HUNK_FAILED"
PATCH_APPLY_PATH_NOT_FOUND = "PATCH_APPLY_PATH_NOT_FOUND"
PATCH_SIZE_EXCEEDED        = "PATCH_SIZE_EXCEEDED"
PATCH_SCOPE_VIOLATION      = "PATCH_SCOPE_VIOLATION"

# ── Build codes ────────────────────────────────────────────────────────────
JAVAC_SYMBOL_NOT_FOUND = "JAVAC_SYMBOL_NOT_FOUND"
JAVAC_TYPE_MISMATCH    = "JAVAC_TYPE_MISMATCH"
MAVEN_ENFORCER         = "MAVEN_ENFORCER"
BUILD_FAILED_UNKNOWN   = "BUILD_FAILED_UNKNOWN"

# ── Test codes ─────────────────────────────────────────────────────────────
TRIGGER_TEST_STILL_FAILING = "TRIGGER_TEST_STILL_FAILING"
NEW_FAILURES_INTRODUCED    = "NEW_FAILURES_INTRODUCED"
TIMEOUT_FUNC_TEST          = "TIMEOUT_FUNC_TEST"
TIMEOUT_REG_TEST           = "TIMEOUT_REG_TEST"

# ── Terminal attempt status ────────────────────────────────────────────────
PATCH_GENERATED      = "PATCH_GENERATED"
PATCH_APPLY_FAILED   = "PATCH_APPLY_FAILED"
BUILD_FAILED         = "BUILD_FAILED"
FUNCTIONALITY_FAILED = "FUNCTIONALITY_FAILED"
REGRESSION_FAILED    = "REGRESSION_FAILED"
TIMEOUT              = "TIMEOUT"
REPAIRED             = "REPAIRED"


def parse_build_reason(compiler_output: str) -> str:
    if "cannot find symbol" in compiler_output:     return JAVAC_SYMBOL_NOT_FOUND
    if "incompatible types"  in compiler_output:     return JAVAC_TYPE_MISMATCH
    if "enforcer"            in compiler_output.lower(): return MAVEN_ENFORCER
    return BUILD_FAILED_UNKNOWN


def parse_test_reason(failing_before: set[str], failing_after: set[str]) -> str:
    """
    failing_before: set of trigger test names that failed pre-patch
    failing_after:  set of ALL tests failing post-patch
    """
    trigger_still_failing = failing_before & failing_after
    new_failures          = failing_after - failing_before
    if trigger_still_failing: return TRIGGER_TEST_STILL_FAILING
    if new_failures:          return NEW_FAILURES_INTRODUCED
    return REPAIRED
