# Defects4J Automatic Repair Report

Date: 2026-03-15

## Summary

| Baseline | Attempted | Repaired | Repair rate | LLM calls/bug (med) | Tokens/bug (med) | Verify time/bug (med) |
|:---------|----------:|---------:|------------:|--------------------:|-----------------:|----------------------:|
| agentless | 3 | 0 | 0.0% | 5 | 15568 | 13.6s |
| claude_code | 3 | 0 | 0.0% | 15 | 29398 | 3.4s |
| cot | 3 | 0 | 0.0% | 5 | 16781 | 17.5s |
| few_shot_cot | 3 | 0 | 0.0% | 5 | 21449 | 0.0s |
| function_calling | 3 | 0 | 0.0% | 5 | 11516 | 12.5s |
| got | 3 | 0 | 0.0% | 5 | 16188 | 0.0s |
| openclaw | 3 | 0 | 0.0% | 10 | 19131 | 0.9s |
| openhands | 3 | 1 | 33.3% | 15 | 14550 | 0.0s |
| pot | 3 | 0 | 0.0% | 8 | 19193 | 0.0s |
| react | 3 | 0 | 0.0% | 12 | 19954 | 0.0s |
| reflexion | 3 | 0 | 0.0% | 5 | 13567 | 0.0s |
| self_consistency | 3 | 0 | 0.0% | 15 | 28088 | 5.0s |
| standard | 3 | 0 | 0.0% | 5 | 14026 | 3.4s |
| swe_agent | 3 | 1 | 33.3% | 15 | 19798 | 3.3s |
| tot | 3 | 0 | 0.0% | 15 | 29579 | 0.0s |
| zero_shot_cot | 3 | 0 | 0.0% | 10 | 19201 | 0.0s |

## Repaired — agentless
## Unrepaired / Errors — agentless
- Lang_1 — TRIGGER_TEST_STILL_FAILING
- Lang_5 — BUILD_FAILED_UNKNOWN
- Math_1 — PATCH_APPLY_HUNK_FAILED

## Repaired — claude_code
## Unrepaired / Errors — claude_code
- Lang_1 — BUILD_FAILED_UNKNOWN
- Lang_5 — BUILD_FAILED_UNKNOWN
- Math_1 — BUILD_FAILED_UNKNOWN

## Repaired — cot
## Unrepaired / Errors — cot
- Lang_1 — TRIGGER_TEST_STILL_FAILING
- Lang_5 — TRIGGER_TEST_STILL_FAILING
- Math_1 — PATCH_APPLY_HUNK_FAILED

## Repaired — few_shot_cot
## Unrepaired / Errors — few_shot_cot
- Lang_1 — EMPTY_DIFF
- Lang_5 — EMPTY_DIFF
- Math_1 — EMPTY_DIFF

## Repaired — function_calling
## Unrepaired / Errors — function_calling
- Lang_1 — TRIGGER_TEST_STILL_FAILING
- Lang_5 — TRIGGER_TEST_STILL_FAILING
- Math_1 — EMPTY_DIFF

## Repaired — got
## Unrepaired / Errors — got
- Lang_1 — EMPTY_DIFF
- Lang_5 — EMPTY_DIFF
- Math_1 — EMPTY_DIFF

## Repaired — openclaw
## Unrepaired / Errors — openclaw
- Lang_1 — BUILD_FAILED_UNKNOWN
- Lang_5 — BUILD_FAILED_UNKNOWN
- Math_1 — EMPTY_DIFF

## Repaired — openhands
- Math_1 (attempt 1, 100.0s)
## Unrepaired / Errors — openhands
- Lang_1 — EMPTY_DIFF
- Lang_5 — EMPTY_DIFF

## Repaired — pot
## Unrepaired / Errors — pot
- Lang_1 — EMPTY_DIFF
- Lang_5 — EMPTY_DIFF
- Math_1 — PATCH_APPLY_HUNK_FAILED

## Repaired — react
## Unrepaired / Errors — react
- Lang_1 — EMPTY_DIFF
- Lang_5 — PATCH_APPLY_HUNK_FAILED
- Math_1 — PATCH_APPLY_HUNK_FAILED

## Repaired — reflexion
## Unrepaired / Errors — reflexion
- Lang_1 — PATCH_APPLY_HUNK_FAILED
- Lang_5 — PATCH_APPLY_HUNK_FAILED
- Math_1 — PATCH_APPLY_HUNK_FAILED

## Repaired — self_consistency
## Unrepaired / Errors — self_consistency
- Lang_1 — EMPTY_DIFF
- Lang_5 — TRIGGER_TEST_STILL_FAILING
- Math_1 — EMPTY_DIFF

## Repaired — standard
## Unrepaired / Errors — standard
- Lang_1 — TRIGGER_TEST_STILL_FAILING
- Lang_5 — EMPTY_DIFF
- Math_1 — EMPTY_DIFF

## Repaired — swe_agent
- Math_1 (attempt 1, 94.7s)
## Unrepaired / Errors — swe_agent
- Lang_1 — EMPTY_DIFF
- Lang_5 — BUILD_FAILED_UNKNOWN

## Repaired — tot
## Unrepaired / Errors — tot
- Lang_1 — BUILD_FAILED_UNKNOWN
- Lang_5 — PATCH_APPLY_HUNK_FAILED
- Math_1 — EMPTY_DIFF

## Repaired — zero_shot_cot
## Unrepaired / Errors — zero_shot_cot
- Lang_1 — EMPTY_DIFF
- Lang_5 — PATCH_APPLY_HUNK_FAILED
- Math_1 — EMPTY_DIFF