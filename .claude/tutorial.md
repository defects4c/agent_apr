# GenAI: Prompting Baselines — Full Tutorial

> **Setup**
> ```bash
> pip install ollama==0.5.1
> ```
> All baselines share `llm = "qwen2.5"` and `q = "What is 30 multiplied by 10?"`.
> The only thing that changes across baselines is **how the prompt is designed**
> and, in some cases, **how many model calls are made**.

---

## The Abstract Base Class

Every baseline inherits from one common interface. This mirrors the `PatchGenerator`
pattern used in production APR systems.

```python
# baselines/base.py
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

@dataclass
class BaselineResult:
    answer:   str                          # final answer extracted from the model
    raw:      str                          # full model output, unmodified
    metadata: dict = field(default_factory=dict)  # calls, strategy, etc.

class PromptingBaseline(ABC):
    """Common interface all baselines implement."""

    def __init__(self, model: str = "qwen2.5"):
        self.model  = model
        self._calls = 0

    def _ask(self, messages: list[dict], **kwargs) -> str:
        import ollama
        self._calls += 1
        resp = ollama.chat(model=self.model, messages=messages, **kwargs)
        return resp["message"]["content"]

    @abstractmethod
    def run(self, question: str) -> BaselineResult:
        """Run the baseline on `question` and return a BaselineResult."""
        ...
```

---

## How to design each baseline

When designing each baseline, think in terms of four variables:

1. **What reasoning behaviour you want from the model**
2. **How much structure you impose in the prompt**
3. **Whether the method needs one call or multiple calls**
4. **Whether the model reasons in natural language, branching trees, graphs, or code**

The table below summarises every baseline before diving into each one:

| # | Baseline | Paper | Venue | LLM calls | Core mechanism |
|---|---|---|---|---|---|
| 1 | Standard Prompting | Wei et al. (control) | NeurIPS 2022 | 1 | No scaffold — direct question |
| 2 | Zero-Shot CoT | Kojima et al. | NeurIPS 2022 | 1–2 | "Let's think step by step" |
| 3 | Few-Shot CoT | Wei et al. | NeurIPS 2022 | 1 | Reasoning demonstrations |
| 4 | ReAct | Yao et al. | ICLR 2023 | 1+ | Thought / Action / Observation loop |
| 5 | Reflexion | Shinn et al. | NeurIPS 2023 | 2+ | Multi-trial verbal RL + memory |
| 6 | Self-Consistency | Wang et al. | ICLR 2023 | N+1 | Majority vote over N CoT samples |
| 7 | Tree of Thoughts | Yao et al. | NeurIPS 2023 | many | BFS/DFS + state evaluation + backtracking |
| 8 | Graph of Thoughts | Besta et al. | AAAI 2024 | many | Graph ops: generate + aggregate + refine |
| 9 | Program of Thoughts | Chen et al. | TMLR 2023 | 1 + exec | Code generation + interpreter |
| 10 | Function Calling | OpenAI API | — (not a paper) | 2 | Structured tool-use via JSON schemas |

---

## 1. Standard Prompting

**Paper:** Used as the control condition in Wei et al. (NeurIPS 2022) and Kojima et al.
(NeurIPS 2022). Also known as *vanilla prompting* or *direct prompting*.

### What it is

Pass the question directly with no reasoning scaffold.
The model decides its own response style.
Every other baseline is measured against this as the zero-cost control.

> **Nuance:** The literature distinguishes *zero-shot* standard prompting (question only,
> no examples) from *few-shot* standard prompting (input→output examples but no reasoning
> chains). The version below is zero-shot. If you add examples without reasoning steps,
> that is few-shot standard prompting — a different and often stronger baseline.

### How to design it

```
prompt = question
```

Nothing is added. The model receives exactly the user's text.
The goal is to measure the model's **default ability** without prompting tricks.
It acts as the reference point. If later methods perform better, you can attribute
the improvement to the reasoning scaffold, not to any change in the underlying task.

### Python example

```python
# baselines/standard.py
from .base import PromptingBaseline, BaselineResult

class StandardPrompting(PromptingBaseline):

    def run(self, question: str) -> BaselineResult:
        self._calls = 0
        raw = self._ask([{"role": "user", "content": question}])
        return BaselineResult(
            answer=raw.strip(),
            raw=raw,
            metadata={"strategy": "standard", "calls": self._calls}
        )

# --- quick test ---
if __name__ == "__main__":
    b = StandardPrompting()
    r = b.run("What is 30 multiplied by 10?")
    print(r.answer)   # 300
```

### Best use

- Control condition for all experiments
- Simple Q&A, summarisation, rewriting, translation
- Cheapest possible baseline (1 call, 0 overhead)

---

## 2. Zero-Shot Chain-of-Thought

**Paper:** Kojima et al., *"Large Language Models are Zero-Shot Reasoners"*,
NeurIPS 2022. arXiv:2205.11916

### What it is

Append the trigger phrase **"Let's think step by step."** to elicit a reasoning
chain before the answer. No examples are required.

> **Critical distinction:** "Let's think step by step" belongs exclusively to
> **Kojima et al.** (zero-shot CoT). Wei et al. introduced *few-shot* CoT using
> hand-written demonstrations — a different technique. These are two separate papers
> with two separate mechanisms. Conflating them is one of the most common errors
> in prompting tutorials.

> **Two-stage design:** Kojima et al.'s original paper uses two calls.
> Stage 1 appends the trigger phrase to extract reasoning.
> Stage 2 sends the reasoning back with "Therefore, the answer is" to extract
> the final answer cleanly. Most tutorials skip Stage 2 and let the model embed
> the answer in the same output. Stage 2 is optional in practice but helps with parsing.

> **Size caveat (verified):** Wei et al. showed CoT is an *emergent ability* —
> it reliably helps only on models with ~100B+ parameters. On small models it can
> hurt performance. Modern efficient models like Qwen2.5 handle it well.

### How to design it

```
Stage 1:  prompt = question + "\nLet's think step by step."
Stage 2:  prompt = question + reasoning + "\nTherefore, the answer is"
```

### Python example

```python
# baselines/zero_shot_cot.py
from .base import PromptingBaseline, BaselineResult

class ZeroShotCoT(PromptingBaseline):

    def run(self, question: str) -> BaselineResult:
        self._calls = 0

        # Stage 1: elicit reasoning chain
        stage1_prompt = f"{question}\nLet's think step by step."
        reasoning = self._ask([{"role": "user", "content": stage1_prompt}])

        # Stage 2: extract clean final answer
        stage2_prompt = f"{stage1_prompt}\n{reasoning}\n\nTherefore, the answer is"
        final = self._ask([{"role": "user", "content": stage2_prompt}])

        return BaselineResult(
            answer=final.strip(),
            raw=reasoning,
            metadata={"strategy": "zero_shot_cot", "calls": self._calls,
                      "stage1_reasoning": reasoning, "stage2_answer": final}
        )

if __name__ == "__main__":
    b = ZeroShotCoT()
    r = b.run("What is 30 multiplied by 10?")
    print(r.answer)
```

### Best use

- Math, logic, planning
- When no examples are available
- Lightweight upgrade over standard prompting (1–2 calls)

---

## 3. Few-Shot Chain-of-Thought

**Paper:** Wei et al., *"Chain-of-Thought Prompting Elicits Reasoning in Large Language
Models"*, NeurIPS 2022. arXiv:2201.11903

### What it is

Provide 2–3 hand-written *(question → step-by-step reasoning → answer)* examples
**before** the new question. The model learns the reasoning pattern from the
demonstrations and applies it to the new question.

> **This is the original CoT paper.** Wei et al. do **not** use "Let's think step by step."
> They rely entirely on in-context demonstrations showing reasoning chains.
> The few-shot version consistently outperforms zero-shot CoT on arithmetic benchmarks.

### How to design it

```
[demo 1: question + reasoning chain + answer]
[demo 2: question + reasoning chain + answer]
Q: {new question}
A:
```

The demonstrations must show full intermediate reasoning, not just final answers.

### Python example

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
        return BaselineResult(
            answer=raw.strip(),
            raw=raw,
            metadata={"strategy": "few_shot_cot", "calls": self._calls}
        )

if __name__ == "__main__":
    b = FewShotCoT()
    r = b.run("What is 30 multiplied by 10?")
    print(r.answer)
```

### Best use

- Math, logic, multi-step reasoning
- When high-quality demonstrations exist
- Consistently stronger than zero-shot CoT on benchmarks

---

## 4. ReAct (Reason + Act)

**Paper:** Yao et al., *"ReAct: Synergizing Reasoning and Acting in Language Models"*,
**ICLR 2023**. arXiv:2210.03629
*(preprint October 2022 — cite as 2023 for the published version)*

### What it is

Interleave **Thought** (LLM-generated reasoning), **Action** (LLM-selected tool call),
and **Observation** (environment-returned result) in a loop until the task is solved.

> **Verified nuance:** In the original paper, **Observations come from real external tools**
> (Wikipedia Search API, Lookup, Finish). When no real tools are connected, the model
> *simulates* Observations — acceptable for demonstrating the format but not the full
> ReAct agent. The paper used few-shot in-context trajectory examples, not just a format
> description. Benchmarks: HotpotQA, FEVER, ALFWorld, WebShop.

### How to design it

The design choice is that reasoning is **externalized into labelled steps**.
The format constraint prevents the model from jumping directly to the answer.

```
Task: {question}

Thought: [what the model knows and what it needs next]
Action:  [one action or tool name]
Observation: [result — from environment or simulated]
... repeat ...
Final Answer: <answer>
```

### Python example

```python
# baselines/react.py
from .base import PromptingBaseline, BaselineResult

class ReAct(PromptingBaseline):

    SYSTEM = (
        "You are a reasoning agent. Solve tasks using a strict loop of:\n"
        "Thought: [what you know and what you need next]\n"
        "Action:  [one action or tool you would take]\n"
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

        answer = raw
        for line in raw.splitlines():
            if line.lower().startswith("final answer"):
                answer = line.split(":", 1)[-1].strip()
                break

        return BaselineResult(
            answer=answer,
            raw=raw,
            metadata={"strategy": "react", "calls": self._calls}
        )

if __name__ == "__main__":
    b = ReAct()
    r = b.run("What is 30 multiplied by 10?")
    print(r.answer)
    print("--- Full trace ---")
    print(r.raw)
```

### Why this differs from CoT

CoT says: "reason step by step."
ReAct says: "reason, then decide an action, then observe the result, then repeat."
ReAct is better designed for tool calling, search, retrieval, and agents.

### Best use

- Tool-enabled agents
- Multi-hop question answering (HotpotQA, FEVER)
- Search + reasoning loops
- Production agent frameworks

---

## 5. Reflexion

**Paper:** Shinn et al., *"Reflexion: Language Agents with Verbal Reinforcement Learning"*,
**NeurIPS 2023**. arXiv:2303.11366

### What it is

A **multi-trial verbal reinforcement learning** framework. The agent attempts the task,
receives feedback, generates a natural-language reflection, stores it in episodic memory,
and retries. Four components: Actor → Evaluator → Self-Reflection → Memory buffer.

> **Verified nuance:** The "two-call self-critique" description found in most tutorials
> is a significant oversimplification. The original framework:
> (a) uses **external feedback** signals (failing unit tests, environment reward, heuristics),
> (b) stores reflections in a **sliding-window memory buffer** across multiple trials,
> (c) uses a **separate Evaluator** LLM or heuristic to score outcomes.
> The paper achieved 91% pass@1 on HumanEval, beating GPT-4's 80% at the time.
> The two-call version below is a simplified approximation for tutorial purposes.

### How to design it

```
Call 1 (Actor):    question + CoT → initial answer
Call 2 (Evaluator): initial answer + "is this correct?" → verdict
Call 3 (Reflector): initial answer + verdict + memory → reflection + revised answer
[Repeat across N trials; store reflections in sliding-window buffer]
```

### Python example

```python
# baselines/reflexion.py
from .base import PromptingBaseline, BaselineResult

class Reflexion(PromptingBaseline):

    def __init__(self, model: str = "qwen2.5", max_trials: int = 2):
        super().__init__(model)
        self.max_trials = max_trials
        self._memory: list[str] = []   # episodic buffer — persists across trials

    def run(self, question: str) -> BaselineResult:
        self._calls = 0
        self._memory.clear()
        reflection_raw = ""

        # Trial 1 — Actor: generate initial answer with CoT
        actor_prompt = f"{question}\nLet's think step by step."
        current_answer = self._ask([{"role": "user", "content": actor_prompt}])

        for trial in range(1, self.max_trials):
            # Build memory context (sliding window of last 3 reflections)
            mem_block = ""
            if self._memory:
                mem_block = "Previous reflections:\n" + \
                            "\n".join(f"- {m}" for m in self._memory[-3:]) + "\n\n"

            # Evaluator + Reflector (combined for tutorial simplicity)
            reflect_prompt = (
                f"{mem_block}"
                f"Question: {question}\n\n"
                f"Previous answer (trial {trial}):\n{current_answer}\n\n"
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
            metadata={"strategy": "reflexion", "calls": self._calls,
                      "trials": self.max_trials, "memory": list(self._memory)}
        )

if __name__ == "__main__":
    b = Reflexion(max_trials=2)
    r = b.run("What is 30 multiplied by 10?")
    print(r.answer)
    print("Reflections stored:", r.metadata["memory"])
```

### Best use

- Code generation (high-quality feedback from test runners)
- Sequential decision-making
- Quality improvement when first pass may be shallow
- Multi-attempt tasks where failure information is available

---

## 6. Self-Consistency

**Paper:** Wang et al., *"Self-Consistency Improves Chain of Thought Reasoning in Language
Models"*, **ICLR 2023**. arXiv:2203.11171

> **Why this baseline matters:** Self-Consistency is the **single most important
> prompting baseline** often omitted from tutorials. It delivers **+17.9% absolute
> on GSM8K** over standard CoT. Any complete prompting baseline set must include it.

### What it is

Sample **N independent CoT completions** using different reasoning phrasings.
Aggregate the final answers by **majority vote** (marginalising over reasoning paths).
The most consistent answer wins.

### How to design it

```
[N different CoT phrasings of the same question]  →  N answers
→ majority_vote(answers)  →  final answer
```

The key insight: the *path* to the answer varies; the *correct answer* is invariant.
Multiple independent chains that agree on the same answer are more trustworthy than
a single chain.

### Python example

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
        m = re.search(r"final answer[:\s]+([0-9,.\-]+)", text, re.I)
        if m:
            return m.group(1).replace(",", "").strip()
        numbers = re.findall(r"\b\d[\d,\.]*\b", text)
        return numbers[-1].replace(",", "") if numbers else text.strip()

    def run(self, question: str) -> BaselineResult:
        self._calls = 0
        candidates, raws = [], []

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
            metadata={"strategy": "self_consistency", "calls": self._calls,
                      "vote_counts": dict(vote_counts), "candidates": candidates}
        )

if __name__ == "__main__":
    b = SelfConsistency(n_samples=5)
    r = b.run("What is 30 multiplied by 10?")
    print("Answer:", r.answer)
    print("Votes: ", r.metadata["vote_counts"])
```

### Best use

- Arithmetic and maths benchmarks (huge gains on GSM8K, MATH)
- Any task where multiple independent reasoning paths can converge
- When answer accuracy matters more than token cost

---

## 7. Tree of Thoughts (ToT)

**Paper:** Yao et al., *"Tree of Thoughts: Deliberate Problem Solving with Large Language
Models"*, **NeurIPS 2023 (oral)**. arXiv:2305.10601

### What it is

Generalise CoT from a single reasoning chain into a **tree search problem** with
four explicit components:

1. **Thought decomposition** — define what one reasoning step looks like
2. **Thought generation** — produce candidate thoughts (sampling or proposing)
3. **State evaluation** — score each state: *sure / maybe / impossible* (or vote)
4. **Search algorithm** — BFS or DFS with **backtracking** over the tree

> **Verified nuance:** The "multiple branches + compare" framing in most tutorials is a
> serious simplification. The defining features it omits are **state evaluation** and
> **backtracking** — the ability to abandon unpromising paths before they complete.
> On Game of 24, ToT achieved **74% success vs. 4% for GPT-4 with CoT**.
> Standard CoT, CoT-SC, and regular prompting are all proven special cases of ToT.

### How to design it

```
Generate K candidate thoughts  →  evaluate each (sure/maybe/impossible)
→  keep top-B  →  expand again  →  ...  →  select best leaf  →  Final Answer
```

### Python example (BFS, depth-2, breadth-2)

```python
# baselines/tree_of_thoughts.py
import re
from .base import PromptingBaseline, BaselineResult

class TreeOfThoughts(PromptingBaseline):

    def __init__(self, model: str = "qwen2.5",
                 n_branches: int = 3, depth: int = 2):
        super().__init__(model)
        self.n_branches = n_branches
        self.depth = depth

    # ── Step 1: generate candidate thoughts ──────────────────────────────
    def _generate_thoughts(self, question: str, context: str = "") -> list[str]:
        prompt = (
            f"Task: {question}\n"
            + (f"Reasoning so far:\n{context}\n\n" if context else "")
            + f"Generate {self.n_branches} distinct next reasoning steps.\n"
            "Number each step. Be concise."
        )
        raw = self._ask([{"role": "user", "content": prompt}])
        steps = re.split(r"\n\s*\d+[\.\)]\s*", "\n" + raw)
        return [s.strip() for s in steps if s.strip()][:self.n_branches]

    # ── Step 2: evaluate each thought (state evaluation) ─────────────────
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

    # ── Step 3: BFS with pruning (backtracking) ───────────────────────────
    def run(self, question: str) -> BaselineResult:
        self._calls = 0
        frontier = [""]

        for _ in range(self.depth):
            next_frontier = []
            for ctx in frontier:
                thoughts = self._generate_thoughts(question, ctx)
                scored = []
                for t in thoughts:
                    score = self._evaluate_thought(question, t)
                    if score != "impossible":           # pruning step
                        scored.append((score, t))
                # sure > maybe; keep top-2
                scored.sort(key=lambda x: 0 if x[0] == "sure" else 1)
                next_frontier.extend(
                    (ctx + "\n" + t).strip()
                    for _, t in scored[:2]
                )
            frontier = next_frontier or frontier

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

        return BaselineResult(
            answer=answer,
            raw=raw,
            metadata={"strategy": "tot", "calls": self._calls,
                      "best_path": best_context}
        )

if __name__ == "__main__":
    b = TreeOfThoughts(n_branches=3, depth=2)
    r = b.run("What is 30 multiplied by 10?")
    print(r.answer)
    print("\nBest path:\n", r.metadata["best_path"])
```

### Best use

- Combinatorial search (Game of 24, crossword puzzles)
- Complex planning requiring backtracking
- Ambiguous tasks where the first reasoning path is often wrong

---

## 8. Graph of Thoughts (GoT)

**Paper:** Besta et al., *"Graph of Thoughts: Solving Elaborate Problems with Large
Language Models"*, **AAAI 2024**. arXiv:2308.09687
*(published AAAI 2024 — not 2023)*

### What it is

Generalise ToT from trees to directed graphs. Formal definition: 4-tuple **(G, T, E, R)** —
graph state, thought transformations, evaluator, ranking. Three transformations are defined:

- **Generation** — standard branching (also in ToT)
- **Aggregation** — merge multiple thoughts into one (impossible in a tree — the key novelty)
- **Refinement** — self-loop to improve an existing thought

> **Verified nuance:** The defining innovation over ToT is **aggregation** — merging two
> separate reasoning branches into one improved thought. On sorting benchmarks, GoT
> achieved **62% quality improvement over ToT with >31% cost reduction**.
> GoT, ToT, CoT, and CoT-SC are all proven subsets of the GoT framework.

> **Critical implementation note:** The synthesis call must receive the **full graph
> summary** — not just the original question. Passing only the question discards all
> graph construction work and reduces GoT to standard prompting.

### How to design it

```
Seed thought (root-cause analysis)
├── Generation → Thought A (approach 1)
├── Generation → Thought B (approach 2)
│       └── Refinement → Thought B' (improved)
└── Aggregation(A, B') → Thought C  ← the GoT novelty
→ Synthesis: pass full graph summary → Final Answer
```

### Python example

```python
# baselines/graph_of_thoughts.py
from dataclasses import dataclass, field
from .base import PromptingBaseline, BaselineResult

@dataclass
class GoTGraph:
    nodes:  dict = field(default_factory=dict)   # id → text
    edges:  list = field(default_factory=list)   # (src, dst, relation)
    _ctr:   int  = field(default=1, repr=False)

    def add(self, text: str) -> str:
        nid = f"T{self._ctr}"; self._ctr += 1
        self.nodes[nid] = text
        return nid

    def connect(self, src: str, dst: str, rel: str):
        self.edges.append((src, dst, rel))

    def summary(self) -> str:
        node_lines = "\n".join(f"  {k}: {v[:150]}" for k, v in self.nodes.items())
        edge_lines = "\n".join(f"  {f} --[{r}]--> {t}" for f, t, r in self.edges)
        return f"Nodes:\n{node_lines}\n\nEdges:\n{edge_lines}"


class GraphOfThoughts(PromptingBaseline):

    def run(self, question: str) -> BaselineResult:
        self._calls = 0
        g = GoTGraph()

        # ── Generation: seed thought ──────────────────────────────────────
        seed_raw = self._ask([{
            "role": "user",
            "content": f"Reason briefly about: {question}"
        }])
        n1 = g.add(seed_raw)

        # ── Generation: two expansion branches ───────────────────────────
        for rel in ("alternative approach", "refinement"):
            exp = self._ask([{"role": "user", "content": (
                f"Task: {question}\n\n"
                f"Previous thought ({n1}):\n{g.nodes[n1]}\n\n"
                f"Provide a {rel} of the above thought."
            )}])
            nx = g.add(exp)
            g.connect(n1, nx, rel)

        node_ids = list(g.nodes.keys())

        # ── Aggregation: merge two branches (the GoT novelty) ─────────────
        if len(node_ids) >= 2:
            merge = self._ask([{"role": "user", "content": (
                f"Task: {question}\n\n"
                f"Thought A ({node_ids[-2]}):\n{g.nodes[node_ids[-2]]}\n\n"
                f"Thought B ({node_ids[-1]}):\n{g.nodes[node_ids[-1]]}\n\n"
                "Merge the strongest parts of both thoughts into one improved node."
            )}])
            nm = g.add(merge)
            g.connect(node_ids[-2], nm, "aggregation")
            g.connect(node_ids[-1], nm, "aggregation")

        # ── Synthesis: MUST pass full graph summary, not just question ────
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

        return BaselineResult(
            answer=answer,
            raw=raw,
            metadata={"strategy": "got", "calls": self._calls,
                      "graph": g.summary()}
        )

if __name__ == "__main__":
    b = GraphOfThoughts()
    r = b.run("What is 30 multiplied by 10?")
    print(r.answer)
    print("\nGraph:\n", r.metadata["graph"])
```

### Best use

- Sorting, merging, set operations (tasks decomposable into subtasks that benefit from merging)
- Complex planning where partial solutions should be combined
- Iterative refinement with cross-branch synthesis

---

## 9. Program of Thoughts (PoT)

**Paper:** Chen et al., *"Program of Thoughts Prompting: Disentangling Computation from
Reasoning for Numerical Reasoning Tasks"*, **TMLR 2023**. arXiv:2211.12588

**Concurrent work:** Gao et al., *"PAL: Program-aided Language Models"*, **ICML 2023** —
same idea, independent discovery four days earlier. Both are widely cited.

### What it is

Ask the model to **write executable Python** instead of free-form prose.
The LLM handles logical decomposition; a Python interpreter handles computation.
The paper specifically leverages **SymPy** for symbolic maths on numerical benchmarks.

> **Verified key insight:** The name is "disentangling computation from reasoning."
> The LLM is a *program writer*, not a calculator. Computation errors are eliminated
> because arithmetic is delegated to the interpreter.
> Concurrent PAL paper (Gao et al.) uses `def solution()` function style;
> PoT uses `result = ...` assignment style.

> **Security note:** Always run model-generated code in a **sandbox**.
> Never use bare `exec()` with full Python builtins.

### How to design it

```
Call 1  →  model writes Python code, assigns answer to `result`
Exec    →  sandbox_exec(code) → actual computed result
```

### Python example

```python
# baselines/program_of_thoughts.py
import re
from .base import PromptingBaseline, BaselineResult

# ── Restricted builtins for safe execution ────────────────────────────────────
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
        import sympy
        safe_globals["sympy"] = sympy     # optional: symbolic math
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
        exec_error = None
        if code:
            try:
                executed_result = _sandbox_exec(code)
            except Exception as e:
                exec_error = str(e)

        answer = str(executed_result) if executed_result is not None else raw.strip()
        return BaselineResult(
            answer=answer,
            raw=raw,
            metadata={"strategy": "pot", "calls": self._calls,
                      "code": code, "executed_result": executed_result,
                      "exec_error": exec_error}
        )

if __name__ == "__main__":
    b = ProgramOfThoughts()
    r = b.run("What is 30 multiplied by 10?")
    print("Answer:   ", r.answer)
    print("Code used:\n", r.metadata["code"])
```

### Best use

- Arithmetic, numerical reasoning (financial, scientific)
- Symbolic maths with SymPy
- Any task where correctness depends on exact deterministic calculation
- Eliminates a whole class of LLM arithmetic errors

---

## 10. Function Calling

**Origin:** OpenAI API feature, June 13 2023. **Not a prompting paper.**
**Related academic work:** Schick et al. *Toolformer* NeurIPS 2023; Patil et al. *Gorilla* 2023.

### What it is

Define tools as JSON schemas. Pass them to the model alongside messages. The model decides
whether to call a tool and returns a structured `tool_calls` object. The application
executes the function and returns the result. The model then answers using real computed values.

> **Verified classification:** Function calling is **not a prompting technique** — it
> involves **model fine-tuning** to output structured JSON. The Prompt Report (Schulhoff
> et al., 2024) catalogued 58 prompting techniques and does not include function calling.
> It belongs to the category of **tool-use / agentic capabilities**.
> It is included here as the cleanest production baseline for tool-enabled agents.

### How to design it

```
messages + tools → Call 1 → tool_calls → execute functions → append tool results
                 → Call 2 → Final Answer using real computed values
```

### Python example

```python
# baselines/function_calling.py
import ollama
from .base import PromptingBaseline, BaselineResult

# ── 1. Define real Python functions ──────────────────────────────────────────
def multiply_numbers(a: int, b: int) -> int:
    return a * b

def get_current_date() -> str:
    from datetime import datetime
    return datetime.today().strftime("%Y-%m-%d")

AVAILABLE_FUNCTIONS = {
    "multiply_numbers": multiply_numbers,
    "get_current_date": get_current_date,
}

# ── 2. Describe them as JSON schemas ──────────────────────────────────────────
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
    tool-call output. Included as the tool-use / agent baseline.
    """

    def run(self, question: str) -> BaselineResult:
        self._calls = 0
        messages = [{"role": "user", "content": question}]

        # ── Call 1: model decides whether to use a tool ───────────────────
        self._calls += 1
        response = ollama.chat(model=self.model, messages=messages, tools=TOOLS)
        messages.append(response["message"])

        tool_calls  = response["message"].get("tool_calls", [])
        used_tools  = []

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

        # ── Call 2: model produces final answer using tool result ──────────
        if tool_calls:
            self._calls += 1
            final = ollama.chat(model=self.model, messages=messages)
            raw = final["message"]["content"]
        else:
            raw = response["message"].get("content", "")

        return BaselineResult(
            answer=raw.strip(),
            raw=raw,
            metadata={"strategy": "function_calling", "calls": self._calls,
                      "tool_calls": used_tools}
        )

if __name__ == "__main__":
    b = FunctionCalling()
    r = b.run("What is 30 multiplied by 10?")
    print("Answer:", r.answer)
    print("Tools used:", r.metadata["tool_calls"])
```

### Best use

- Production agent systems
- Calculator, API, database, retrieval tools
- When structured, auditable tool invocation matters more than interpretability
- Cleanest baseline for evaluating tool-enabled agents (Berkeley Function Calling Leaderboard)

---

## Putting It All Together — Unified Runner

```python
# run_all.py
from baselines.standard          import StandardPrompting
from baselines.zero_shot_cot     import ZeroShotCoT
from baselines.few_shot_cot      import FewShotCoT
from baselines.react             import ReAct
from baselines.reflexion         import Reflexion
from baselines.self_consistency  import SelfConsistency
from baselines.tree_of_thoughts  import TreeOfThoughts
from baselines.graph_of_thoughts import GraphOfThoughts
from baselines.program_of_thoughts import ProgramOfThoughts
from baselines.function_calling  import FunctionCalling

QUESTION = "What is 30 multiplied by 10?"

BASELINES = [
    ("1.  Standard",           StandardPrompting()),
    ("2.  Zero-Shot CoT",      ZeroShotCoT()),
    ("3.  Few-Shot CoT",       FewShotCoT()),
    ("4.  ReAct",              ReAct()),
    ("5.  Reflexion",          Reflexion(max_trials=2)),
    ("6.  Self-Consistency",   SelfConsistency(n_samples=5)),
    ("7.  Tree of Thoughts",   TreeOfThoughts(n_branches=3, depth=2)),
    ("8.  Graph of Thoughts",  GraphOfThoughts()),
    ("9.  Program of Thoughts",ProgramOfThoughts()),
    ("10. Function Calling",   FunctionCalling()),
]

for name, baseline in BASELINES:
    result = baseline.run(QUESTION)
    print(
        f"{name:<28} "
        f"answer={result.answer!r:<12} "
        f"calls={result.metadata['calls']}"
    )
```

Expected output:
```
1.  Standard                 answer='300'         calls=1
2.  Zero-Shot CoT            answer='300'         calls=2
3.  Few-Shot CoT             answer='300'         calls=1
4.  ReAct                    answer='300'         calls=1
5.  Reflexion                answer='300'         calls=3
6.  Self-Consistency         answer='300'         calls=5
7.  Tree of Thoughts         answer='300'         calls=9
8.  Graph of Thoughts        answer='300'         calls=5
9.  Program of Thoughts      answer='300'         calls=1
10. Function Calling         answer='300'         calls=2
```

---

## Design Principles — What Changes Across Baselines

The table below shows exactly which design variable each baseline changes:

| Baseline | Prompt changes | LLM calls | Who computes | Key novelty |
|---|---|---|---|---|
| Standard | None | 1 | Model | Zero-scaffold control |
| Zero-Shot CoT | Trigger phrase appended | 1–2 | Model | Emergent reasoning from phrase |
| Few-Shot CoT | Demonstrations prepended | 1 | Model | In-context reasoning patterns |
| ReAct | Format scaffold (T/A/O loop) | 1+ | Model + tools | Reasoning grounded by actions |
| Reflexion | Two-pass with memory buffer | 2+ | Model | Verbal RL across trials |
| Self-Consistency | N independent phrasings | N+1 | Model + vote | Aggregation over reasoning paths |
| Tree of Thoughts | Branch + evaluate + prune | many | Model + BFS | State evaluation and backtracking |
| Graph of Thoughts | Graph ops + synthesis | many | Model + graph | Aggregation across branches |
| Program of Thoughts | Output as code | 1 + exec | Python interpreter | Computation disentangled from reasoning |
| Function Calling | Tool schemas added | 2 | Your functions | Structured, auditable tool use |

---

## Reference — All Papers and Venues

| Baseline | Full citation | Venue |
|---|---|---|
| Standard Prompting | Wei et al. (2022) — used as control | NeurIPS 2022 |
| Zero-Shot CoT | Kojima et al., "Large Language Models are Zero-Shot Reasoners" | NeurIPS 2022 |
| Few-Shot CoT | Wei et al., "Chain-of-Thought Prompting Elicits Reasoning in LLMs" | NeurIPS 2022 |
| ReAct | Yao et al., "ReAct: Synergizing Reasoning and Acting in Language Models" | ICLR 2023 |
| Reflexion | Shinn et al., "Reflexion: Language Agents with Verbal Reinforcement Learning" | NeurIPS 2023 |
| Self-Consistency | Wang et al., "Self-Consistency Improves Chain of Thought Reasoning in LMs" | ICLR 2023 |
| Tree of Thoughts | Yao et al., "Tree of Thoughts: Deliberate Problem Solving with LLMs" | NeurIPS 2023 (oral) |
| Graph of Thoughts | Besta et al., "Graph of Thoughts: Solving Elaborate Problems with LLMs" | AAAI 2024 |
| Program of Thoughts | Chen et al., "Program of Thoughts Prompting: Disentangling Computation from Reasoning" | TMLR 2023 |
| PAL (concurrent) | Gao et al., "PAL: Program-aided Language Models" | ICML 2023 |
| Function Calling | OpenAI API feature (June 2023) — not a paper | — |
| Toolformer (related) | Schick et al., "Toolformer: Language Models Can Teach Themselves to Use Tools" | NeurIPS 2023 |

