# swe_agent/patch_generators/few_shot_cot.py
"""
Few-Shot Chain-of-Thought patch generator.
Paper: Wei et al., NeurIPS 2022 (arXiv:2201.11903) — the ORIGINAL CoT paper.
Strategy: one call with 2 hand-written APR demonstrations before the new bug.
Budget: 1 LLM call per attempt.
Note: this is distinct from zero_shot_cot.py — no "Let's think step by step",
      relies entirely on in-context demonstrations showing reasoning chains.
"""
from pathlib import Path
from .base import PatchGenerator, PatchResult
from ._shared import (build_fail_context, build_location_context,
                       extract_search_replace, PATCH_SYSTEM)

# ── APR-specific reasoning demonstrations ─────────────────────────────────────
DEMONSTRATIONS = """
## Demonstration 1: Lang_6 - StringEscapeUtils

Bug: Lang_6
Failing test: org.apache.commons.lang3.StringEscapeUtilsTest::testEscapeJson
Error: AssertionError: expected:<...> but was:<...>
Stack trace:
  at StringEscapeUtils.escapeJson(StringEscapeUtils.java:248)

Suspicious location: StringEscapeUtils.java lines 245-255
```java
245: public static String escapeJson(String input) {
246:     if (input == null) return null;
247:     StringBuilder sb = new StringBuilder();
248:     for (char c : input.toCharArray()) {
249:         if (c == '"') sb.append("\\\"");
250:         if (c == '\\') sb.append("\\\\");
251:         sb.append(c);
252:     }
253:     return sb.toString();
254: }
```

Step 1 — Root cause: Lines 249-251 append the escape sequence AND then unconditionally
  append the original character `c`, so escaped chars appear doubled.
Step 2 — Fix: Use `else` so `c` is only appended when no escape was emitted.
Step 3 — Implementation:

FILE: src/main/java/org/apache/commons/lang3/StringEscapeUtils.java
SEARCH:
        if (c == '"') sb.append("\\\"");
        if (c == '\\') sb.append("\\\\");
        sb.append(c);
REPLACE:
        if (c == '"') { sb.append("\\\""); }
        else if (c == '\\') { sb.append("\\\\"); }
        else { sb.append(c); }

---

## Demonstration 2: Math_5 - Complex.reciprocal

Bug: Math_5
Failing test: org.apache.commons.math3.complex.ComplexTest::testReciprocalZero
Error: AssertionError: expected NaN but was <Infinity>
Stack trace:
  at Complex.reciprocal(Complex.java:299)

Suspicious location: Complex.java lines 295-305
```java
295: public Complex reciprocal() {
296:     if (isNaN) return NaN;
297:     if (real == 0.0 && imaginary == 0.0) {
298:         return NaN;                     // BUG: should be INF
299:     }
300:     ...
301: }
```

Step 1 — Root cause: Reciprocal of zero should be Complex.INF per IEEE semantics,
  but line 298 returns NaN instead.
Step 2 — Fix: Return INF when the denominator is zero.
Step 3 — Implementation:

FILE: src/main/java/org/apache/commons/math3/complex/Complex.java
SEARCH:
        if (real == 0.0 && imaginary == 0.0) {
            return NaN;
        }
REPLACE:
        if (real == 0.0 && imaginary == 0.0) {
            return INF;
        }
""".strip()

USER_TEMPLATE = """{demonstrations}

---

## New bug to fix

{fail_context}

## Suspicious location(s)
{location_context}

Step 1 — Root cause:
Step 2 — Fix strategy:
Step 3 — Implementation:

Output the patch using EXACTLY this format (no markdown fences):

FILE: path/to/File.java
SEARCH: <exact lines>
REPLACE: <corrected lines>
"""


class FewShotCoTPatchGenerator(PatchGenerator):

    def generate_patch(
        self,
        bug_id, workdir, failing_info, trigger_tests,
        localization_hits, attempt_index, out_dir, llm_client,
    ) -> PatchResult:

        fail_ctx = build_fail_context(bug_id, trigger_tests, failing_info)
        loc_ctx = build_location_context(localization_hits, workdir)

        prompt = USER_TEMPLATE.format(
            demonstrations=DEMONSTRATIONS,
            fail_context=fail_ctx,
            location_context=loc_ctx,
        )

        response = llm_client.chat(
            [{"role": "system", "content": PATCH_SYSTEM},
             {"role": "user", "content": prompt}],
            purpose="few_shot_cot_patch_gen", attempt=attempt_index,
            out_dir=out_dir, max_tokens=2000,
        )

        if not response:
            return PatchResult(diff_text="", metadata={
                "strategy": "few_shot_cot", "raw_response": "", "reason": "empty_response"
            })

        diff_text = extract_search_replace(response)
        return PatchResult(
            diff_text=diff_text,
            metadata={"strategy": "few_shot_cot", "raw_response": response},
        )
