# swe_agent/patch_generators/got.py
"""
Graph of Thoughts patch generator.
Paper: Besta et al. (AAAI 2024)
Strategy: Build a graph of reasoning nodes (observations, hypotheses, fixes),
then synthesize a patch based on the graph structure.
Budget: 2-3 calls per attempt (graph construction + synthesis).
"""
from pathlib import Path
from .base import PatchGenerator, PatchResult
from ._shared import (build_fail_context, build_location_context, PATCH_SYSTEM, extract_search_replace)
from .agentless import apply_search_replace_directly, search_replace_to_diff


GRAPH_SYSTEM = """You are building a reasoning graph for a Java bug.
Identify key nodes: observations, hypotheses about root cause, and potential fixes.

Output format (one node per line):
OBSERVATION: <fact from stack trace or code>
HYPOTHESIS: <possible root cause>
FIX: <potential fix idea>
"""

GRAPH_USER = """## Bug: {bug_id}
## Failing tests: {fail_context}
## Locations: {location_context}

Build a reasoning graph for this bug:
1. OBSERVATION: Extract 2-3 key facts from the stack trace
2. HYPOTHESIS: Generate 2-3 possible root causes
3. FIX: Suggest 1-2 fix ideas for the most likely hypothesis

Output each node on a separate line with the prefix.
"""

SYNTHESIS_SYSTEM = """You are synthesizing a patch from a reasoning graph.
The graph contains observations, hypotheses, and fix ideas.

Combine the most likely hypothesis with the corresponding fix idea.
Output a concrete patch:

FILE: path/to/File.java
SEARCH: <exact lines>
REPLACE: <corrected lines>
"""

SYNTHESIS_USER = """## Bug: {bug_id}

## Reasoning Graph
{graph_summary}

## Task
Synthesize the graph into a concrete patch.
1. Select the most likely hypothesis based on observations
2. Apply the corresponding fix idea to the code location
3. Output the exact search-replace patch

Output the patch:
"""


class GraphOfThoughts:
    """Simple graph structure for reasoning nodes."""

    def __init__(self):
        self.observations = []
        self.hypotheses = []
        self.fixes = []

    def add_node(self, node_type: str, content: str):
        if node_type == "OBSERVATION":
            self.observations.append(content)
        elif node_type == "HYPOTHESIS":
            self.hypotheses.append(content)
        elif node_type == "FIX":
            self.fixes.append(content)

    def summary(self) -> str:
        lines = ["## Reasoning Graph"]
        if self.observations:
            lines.append("### Observations")
            for i, o in enumerate(self.observations, 1):
                lines.append(f"{i}. {o}")
        if self.hypotheses:
            lines.append("### Hypotheses")
            for i, h in enumerate(self.hypotheses, 1):
                lines.append(f"{i}. {h}")
        if self.fixes:
            lines.append("### Fix Ideas")
            for i, f in enumerate(self.fixes, 1):
                lines.append(f"{i}. {f}")
        return "\n".join(lines)

    def is_empty(self) -> bool:
        return not (self.observations or self.hypotheses or self.fixes)


class GoTPatchGenerator(PatchGenerator):

    def __init__(self):
        self.graph = None

    def generate_patch(
        self,
        bug_id, workdir, failing_info, trigger_tests,
        localization_hits, attempt_index, out_dir, llm_client,
    ) -> PatchResult:

        fail_ctx = build_fail_context(bug_id, trigger_tests, failing_info)
        loc_ctx = build_location_context(localization_hits, workdir)

        # Stage 1: Build the reasoning graph
        graph_prompt = GRAPH_USER.format(
            bug_id=bug_id,
            fail_context=fail_ctx,
            location_context=loc_ctx,
        )

        messages = [
            {"role": "system", "content": GRAPH_SYSTEM},
            {"role": "user", "content": graph_prompt}
        ]

        graph_response = llm_client.chat(
            messages, purpose="got_graph_construction", attempt=attempt_index,
            out_dir=out_dir, max_tokens=1500
        )

        # Parse the graph
        self.graph = GraphOfThoughts()
        if graph_response:
            for line in graph_response.split('\n'):
                line = line.strip()
                if line.startswith("OBSERVATION:"):
                    self.graph.add_node("OBSERVATION", line[12:].strip())
                elif line.startswith("HYPOTHESIS:"):
                    self.graph.add_node("HYPOTHESIS", line[11:].strip())
                elif line.startswith("FIX:"):
                    self.graph.add_node("FIX", line[4:].strip())

        if self.graph.is_empty():
            return PatchResult(diff_text="", metadata={
                "strategy": "got", "graph_nodes": 0, "reason": "empty graph"
            })

        # Stage 2: Synthesize patch from graph
        synthesis_prompt = SYNTHESIS_USER.format(
            bug_id=bug_id,
            graph_summary=self.graph.summary(),
        )

        messages = [
            {"role": "system", "content": SYNTHESIS_SYSTEM},
            {"role": "user", "content": synthesis_prompt}
        ]

        synthesis_response = llm_client.chat(
            messages, purpose="got_synthesis", attempt=attempt_index,
            out_dir=out_dir, max_tokens=1500
        )

        if not synthesis_response:
            return PatchResult(diff_text="", metadata={
                "strategy": "got",
                "graph_nodes": len(self.graph.observations) + len(self.graph.hypotheses) + len(self.graph.fixes),
                "reason": "no_synthesis_response"
            })

        # Strategy 1: Try to apply synthesized patch directly
        success, result = apply_search_replace_directly(synthesis_response, workdir)
        if success:
            from ..apply_patch import rollback
            rollback(workdir)
            return PatchResult(diff_text=result, metadata={
                "strategy": "got",
                "graph_nodes": len(self.graph.observations) + len(self.graph.hypotheses) + len(self.graph.fixes),
                "observations": len(self.graph.observations),
                "hypotheses": len(self.graph.hypotheses),
                "fixes": len(self.graph.fixes),
                "synthesis_response": synthesis_response,
                "method": "direct_apply"
            })

        # Strategy 2: Extract and try again
        extracted = extract_search_replace(synthesis_response)
        if extracted:
            success2, result2 = apply_search_replace_directly(extracted, workdir)
            if success2:
                from ..apply_patch import rollback
                rollback(workdir)
                return PatchResult(diff_text=result2, metadata={
                    "strategy": "got",
                    "graph_nodes": len(self.graph.observations) + len(self.graph.hypotheses) + len(self.graph.fixes),
                    "observations": len(self.graph.observations),
                    "hypotheses": len(self.graph.hypotheses),
                    "fixes": len(self.graph.fixes),
                    "synthesis_response": synthesis_response,
                    "method": "extract_then_apply"
                })

        # Strategy 3: Convert to diff format
        diff_text = search_replace_to_diff(synthesis_response, workdir)
        if diff_text:
            return PatchResult(diff_text=diff_text, metadata={
                "strategy": "got",
                "graph_nodes": len(self.graph.observations) + len(self.graph.hypotheses) + len(self.graph.fixes),
                "observations": len(self.graph.observations),
                "hypotheses": len(self.graph.hypotheses),
                "fixes": len(self.graph.fixes),
                "synthesis_response": synthesis_response,
                "method": "convert_to_diff"
            })

        return PatchResult(diff_text="", metadata={
            "strategy": "got",
            "graph_nodes": len(self.graph.observations) + len(self.graph.hypotheses) + len(self.graph.fixes),
            "synthesis_response": synthesis_response,
            "reason": "patch_extraction_failed"
        })
