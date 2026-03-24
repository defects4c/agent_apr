# swe_agent/patch_generators/react.py
"""
ReAct (Reason + Act) patch generator.
Paper: Yao et al. ICLR 2023 (arXiv:2210.03629)

REAL ReAct: interleaves reasoning (Thought) with actual tool execution (Action)
and feeds real output back as Observations. This is NOT multi-stage CoT.

The key innovation vs CoT: Observations come from REAL tool execution (grep,
sed, defects4j compile, etc.), not from the LLM's imagination.

Budget: Up to MAX_LLM_CALLS_PER_ATTEMPT calls per attempt (each call =
  Thought + Action, then real Observation fed back).
"""
import re
from pathlib import Path
from .base import PatchGenerator, PatchResult
from ._shared import build_fail_context, build_location_context, PATCH_SYSTEM
from ..config import MAX_LLM_CALLS_PER_ATTEMPT


REACT_SYSTEM = """You are a program repair agent using the ReAct framework.
Each turn you MUST output:
  Thought: <your reasoning about current state and next step>
  Action: <a shell command to execute>

Available actions (shell commands):
  grep -n 'pattern' <file>        — search with line numbers
  sed -n 'START,ENDp' <file>      — view specific lines
  sed -i 's/old/new/' <file>      — edit file in place
  defects4j compile               — compile the project
  defects4j test -r               — run relevant tests
  defects4j test                  — run full test suite
  git diff                        — show your changes
  git checkout -- <file>          — revert a file

When you believe the bug is fixed (tests pass), output:
  Thought: All tests pass. Submitting patch.
  Action: DONE

RULES:
- Output EXACTLY one Action per turn (a shell command or DONE)
- Do NOT use 'cat' on entire files. Use grep + sed -n for targeted reading.
- Most bugs are 1-3 line fixes. Be minimal.
"""

REACT_USER_INIT = """{fail_context}

## Suspicious location(s)
{location_context}

Begin by reading the suspicious code. Output Thought + Action."""


class ReActPatchGenerator(PatchGenerator):

    def generate_patch(
        self,
        bug_id, workdir, failing_info, trigger_tests,
        localization_hits, attempt_index, out_dir, llm_client,
    ) -> PatchResult:
        from .. import defects4j as d4j

        fail_ctx = build_fail_context(bug_id, trigger_tests, failing_info)
        loc_ctx = build_location_context(localization_hits, workdir)

        messages = [
            {"role": "system", "content": REACT_SYSTEM},
            {"role": "user", "content": REACT_USER_INIT.format(
                fail_context=fail_ctx, location_context=loc_ctx)},
        ]

        history = []
        max_steps = min(MAX_LLM_CALLS_PER_ATTEMPT, 5)

        for step in range(max_steps):
            # ── LLM call: get Thought + Action ──
            response = llm_client.chat(
                messages, purpose=f"react_step{step+1}",
                attempt=attempt_index, out_dir=out_dir, max_tokens=800,
            )
            if not response:
                break

            messages.append({"role": "assistant", "content": response})
            history.append(response)

            # ── Parse Action ──
            action = self._extract_action(response)
            if not action:
                messages.append({"role": "user", "content":
                    "Observation: No valid Action found. Output Thought: + Action: <command>"})
                continue

            # ── Check DONE signal ──
            if action.strip().upper() == "DONE":
                # Capture patch via git diff
                rc, diff_out, _ = d4j.shell("git diff", workdir)
                if diff_out and diff_out.strip():
                    return PatchResult(
                        diff_text=diff_out.strip(),
                        metadata={"strategy": "react", "steps": step + 1,
                                  "history": history, "format": "unified_diff"},
                    )
                messages.append({"role": "user", "content":
                    "Observation: git diff is empty. No changes made. Edit a file first."})
                continue

            # ── Execute Action in Docker ──
            rc, stdout, stderr = d4j.shell(action, workdir)
            observation = (stdout + ("\n" + stderr if stderr else ""))[:4000]
            if not observation.strip():
                observation = "(no output)"

            messages.append({"role": "user", "content": f"Observation:\n{observation}"})

        # ── End of loop: capture any remaining diff ──
        rc, diff_out, _ = d4j.shell("git diff", workdir)
        diff_text = diff_out.strip() if diff_out else ""

        # Revert changes so runner's apply_patch can cleanly re-apply
        if diff_text:
            d4j.shell("git checkout -- .", workdir)

        return PatchResult(
            diff_text=diff_text,
            metadata={"strategy": "react", "steps": max_steps,
                      "history": history, "format": "unified_diff"},
        )

    @staticmethod
    def _extract_action(response: str) -> str:
        """Extract the Action from a ReAct response."""
        # Try "Action: <command>" pattern
        m = re.search(r'Action:\s*(.+)', response, re.IGNORECASE)
        if m:
            action = m.group(1).strip()
            # Remove markdown fences if present
            action = re.sub(r'^```(?:bash|sh)?\s*', '', action)
            action = re.sub(r'\s*```$', '', action)
            return action

        # Try ```bash block as fallback
        m = re.search(r'```(?:bash|sh)?\n(.*?)```', response, re.DOTALL)
        if m:
            return m.group(1).strip()

        return ""
