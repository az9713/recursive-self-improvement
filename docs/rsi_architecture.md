# RSI Architecture: How Recursive Self-Improvement Works in This Project

**Last updated:** 2026-03-01

This document explains how Recursive Self-Improvement (RSI) is implemented in the poetiq_YC codebase. It covers the architecture, algorithms, the recursion mechanism, and how the system can be evolved.

---

## Table of Contents

1. [What Is RSI?](#1-what-is-rsi)
2. [System Overview](#2-system-overview)
3. [The Recursion Mechanism](#3-the-recursion-mechanism)
4. [Architecture Walkthrough](#4-architecture-walkthrough)
5. [Component Deep Dives](#5-component-deep-dives)
   - [5.1 Entry Point and Initialization](#51-entry-point-and-initialization)
   - [5.2 The RSI Loop Orchestrator](#52-the-rsi-loop-orchestrator)
   - [5.3 Program Interface and Baselines](#53-program-interface-and-baselines)
   - [5.4 Evaluation Layer](#54-evaluation-layer)
   - [5.5 Failure Analyzer](#55-failure-analyzer)
   - [5.6 Program Mutator](#56-program-mutator)
   - [5.7 Pareto Selector](#57-pareto-selector)
   - [5.8 Model Router and Cost Tracking](#58-model-router-and-cost-tracking)
   - [5.9 Sandbox: Dynamic Loading and Execution](#59-sandbox-dynamic-loading-and-execution)
6. [Data Flow Through One Cycle](#6-data-flow-through-one-cycle)
7. [The Genetic Analogy](#7-the-genetic-analogy)
8. [How to Evolve the Code](#8-how-to-evolve-the-code)
9. [File Reference](#9-file-reference)

---

## 1. What Is RSI?

Recursive Self-Improvement is a system that **uses its own output as input to improve itself**. In classical AI theory, this refers to an agent that rewrites its own code to become more capable.

In this project, RSI operates at the **program level**: the system generates, evaluates, and selects executable Python programs that solve reasoning tasks. The "recursive" part is that the system uses LLMs to write new programs, evaluates those programs against benchmarks, identifies why they fail, and feeds that failure analysis back into the next round of program generation.

```
                    ┌──────────────────────────────────────┐
                    │                                      │
   Programs are     │   LLMs write programs that use LLMs  │    Programs are
   the "DNA"        │   to solve problems. The system      │    the unit of
                    │   evolves the programs, not the LLMs. │    selection.
                    │                                      │
                    └──────────────────────────────────────┘
```

**Key distinction:** This is not prompt optimization. The system generates complete, executable Python modules — classes with `solve()` methods, import statements, helper functions, and arbitrary logic. The mutation can change prompts, add LLM calls, add programmatic verification steps, or restructure the entire solving strategy.

---

## 2. System Overview

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          run.py (CLI Entry Point)                       │
│  Parses args, builds infrastructure, seeds baselines, launches loop     │
└────────────────────────────────────┬────────────────────────────────────┘
                                     │
                                     ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                      RSILoop  (src/meta/loop.py)                        │
│                                                                         │
│  ┌───────────┐    ┌──────────┐    ┌─────────┐    ┌────────────────┐    │
│  │ Evaluator │───▶│ Analyzer │───▶│ Mutator │───▶│ Pareto Selector│    │
│  │           │    │          │    │         │    │                │    │
│  │ GSM8K /   │    │ Failure  │    │ Program │    │ Score vs Cost  │    │
│  │ ARC-AGI   │    │ Clusters │    │ Genesis │    │ Frontier       │    │
│  └───────────┘    └──────────┘    └─────────┘    └────────────────┘    │
│         │                              │                               │
│         ▼                              ▼                               │
│  ┌─────────────┐              ┌──────────────┐                         │
│  │  Sandbox    │              │  Generated   │                         │
│  │  (safe exec)│              │  Programs    │                         │
│  └─────────────┘              │  (*.py on    │                         │
│                               │   disk)      │                         │
│                               └──────────────┘                         │
│                                                                         │
│  ┌────────────────────────────────────────────────────────────────┐     │
│  │          Model Infrastructure                                  │     │
│  │  ModelRoster ──▶ ModelRouter ──▶ CostTracker                  │     │
│  │  (6 models)     (tier-aware)     (budget cap)                 │     │
│  └────────────────────────────────────────────────────────────────┘     │
└─────────────────────────────────────────────────────────────────────────┘
```

**Directory structure:**

```
src/
├── meta/                   # The RSI engine
│   ├── loop.py             # Orchestrator (RSILoop)
│   ├── analyzer.py         # Failure categorization
│   ├── mutator.py          # Program generation via LLM
│   └── selector.py         # Pareto-optimal selection
├── models/                 # LLM provider abstraction
│   ├── base.py             # ModelConfig, LLMProvider, ModelRoster
│   ├── router.py           # Cost-aware tier routing
│   ├── openai_provider.py  # OpenAI API wrapper
│   ├── anthropic_provider.py
│   └── google_provider.py
├── programs/               # The "organisms" being evolved
│   ├── interface.py        # ReasoningProgram base class + Solution
│   ├── baselines/          # 5 hand-written seed programs
│   └── generated/          # Auto-generated mutant programs (gitignored)
├── evaluator/              # Frozen benchmark evaluation
│   ├── base.py             # EvalResult, BenchmarkTask, Evaluator ABC
│   ├── gsm8k.py            # Grade school math evaluator
│   └── arc.py              # ARC-AGI visual reasoning evaluator
└── utils/
    ├── sandbox.py          # Dynamic module loading + safe execution
    └── cost_tracker.py     # Budget enforcement
```

---

## 3. The Recursion Mechanism

The "recursion" in RSI is achieved through a feedback loop where **each cycle's output becomes the next cycle's input**:

```
 Cycle N                                          Cycle N+1
┌─────────────────────┐                    ┌─────────────────────┐
│                     │                    │                     │
│  Library:           │                    │  Library:           │
│   program_A (best)  │────────────────────│   program_A (best)──│──── parent
│   program_B         │   Pareto           │   program_C (new!)  │     for
│   program_C (new!)  │◄──select──┐        │                     │     cycle
│                     │           │        │                     │     N+2
└─────────────────────┘           │        └─────────────────────┘
         │                        │                  │
         │evaluate                │                  │evaluate
         ▼                        │                  ▼
   ┌───────────┐           ┌──────┴────┐       ┌───────────┐
   │ Failures  │──analyze──│ Mutants   │       │ Failures  │──analyze──▶ ...
   │ from N    │           │ from N    │       │ from N+1  │
   └───────────┘           └───────────┘       └───────────┘
         │                      ▲
         │ failure              │ new programs
         │ summary              │ (Python files)
         ▼                      │
   ┌───────────┐          ┌─────┴─────┐
   │ Analyzer  │─────────▶│  Mutator  │
   │ (LLM:     │  "Here   │  (LLM:    │
   │  strong)  │  is why  │   strong)  │
   │           │  they    │           │
   └───────────┘  fail"   └───────────┘
```

### The recursion in concrete terms:

1. **Cycle 1**: Evaluate 5 baselines → Analyze failures → Mutate best program → Get 3 new programs → Select best → Library now has 3 programs
2. **Cycle 2**: Evaluate library (using cache for unchanged programs) → Analyze failures → Mutate **the winner from cycle 1** → Get 3 new programs → Select → Library updated
3. **Cycle N**: The winner from cycle N-1 becomes the mutation parent → Its children compete against it → The best child may become the parent for cycle N+1

**This is the recursion**: the system's output (a generated program) becomes the input (the mutation parent) for the next cycle. Each generation of programs builds on the previous generation's improvements:

```
chain_of_thought (baseline, human-written)
    │
    │  Cycle 1: LLM reads this source, writes an improved version
    ▼
enhanced_chain_of_thought (gen 1, 95.5%)
    │
    │  Cycles 2-4: LLM reads gen 1 source + failure analysis, writes improvement
    ▼
enhanced_chain_of_thought_v2 (gen 2, 96.0%)
    │
    │  Cycle 5: LLM reads gen 2 source + failure analysis, writes improvement
    ▼
enhanced_chain_of_thought_v3 (gen 3, 97.0%)
```

The system literally reads its own prior output (the Python source code of the winning program) and uses an LLM to write a better version. **Programs improving programs** — that is the recursion.

---

## 4. Architecture Walkthrough

### One Complete RSI Cycle

```
Step 1: EVALUATE                Step 2: ANALYZE
┌────────────────────┐          ┌────────────────────┐
│ For each program   │          │ Collect ALL failed  │
│ in the library:    │          │ EvalResults across  │
│                    │          │ all programs        │
│  for each task:    │          │                     │
│    solve(problem)  │────────▶ │ Auto-detect:        │
│    check_answer()  │          │   timeout, error    │
│    log result      │          │                     │
│                    │          │ LLM categorize:     │
│  (skip if cached)  │          │   arithmetic_error  │
│                    │          │   misread_problem    │
└────────────────────┘          │   wrong_strategy    │
                                │   ... (8 classes)   │
                                │                     │
                                │ Output: failure     │
                                │ summary string      │
                                └────────┬───────────┘
                                         │
                    ┌────────────────────┘
                    ▼
Step 3: MUTATE                  Step 4: EVALUATE MUTANTS
┌────────────────────┐          ┌────────────────────┐
│ Select best program│          │ For each valid      │
│ as mutation parent │          │ mutant:             │
│                    │          │                     │
│ Read its source    │          │  load_program()     │
│ code from disk     │          │  evaluate on ALL    │
│                    │────────▶ │  200 tasks          │
│ For each of 3      │          │  add to library     │
│ candidates:        │          │                     │
│   pick strategy    │          └────────┬───────────┘
│   call strong LLM  │                   │
│   extract code     │                   ▼
│   write to disk    │          Step 5: PARETO SELECT
│   validate         │          ┌────────────────────┐
│                    │          │ Compute stats for   │
└────────────────────┘          │ ALL library programs│
                                │                     │
                                │ Remove dominated:   │
                                │  if ∃ program with  │
                                │  better score AND   │
                                │  lower cost → prune │
                                │                     │
                                │ Always keep the     │
                                │ highest scorer      │
                                └────────┬───────────┘
                                         │
                    ┌────────────────────┘
                    ▼
Step 6-7: LOG + STOPPING
┌────────────────────┐
│ Log cycle results  │
│ to cycles.jsonl    │
│                    │
│ Check: budget      │
│ exhausted?         │
│                    │
│ Check: plateau?    │
│ (no improvement    │
│  for N cycles)     │
│                    │
│ If neither → next  │
│ cycle              │
└────────────────────┘
```

---

## 5. Component Deep Dives

### 5.1 Entry Point and Initialization

**File:** `run.py`

The CLI parses arguments and wires everything together:

```python
# run.py, lines 57-59 — Build infrastructure
cost_tracker = CostTracker(budget_usd=args.budget)
roster = build_default_roster()
router = ModelRouter(roster, cost_tracker)
```

```python
# run.py, lines 111-120 — Create the RSI loop
loop = RSILoop(
    evaluator=evaluator,
    models=roster,
    router=router,
    cost_tracker=cost_tracker,
    max_cycles=args.cycles,
    n_mutants_per_cycle=args.mutants,
    results_dir=results_dir,
    start_cycle=start_cycle,
)
```

For a fresh run, all 5 baselines are seeded into the library:

```python
# run.py, lines 141-149
baselines = [
    DirectProgram(),
    ChainOfThoughtProgram(),
    DecomposeSolveProgram(),
    GenerateVerifyProgram(),
    EnsembleVoteProgram(),
]
for b in baselines:
    loop.add_program(b)
```

For resumed runs, all generated programs are loaded from disk:

```python
# run.py, lines 126-133
for py_file in sorted(GENERATED_DIR.glob("*.py")):
    prog = load_program(py_file)
    if prog is not None:
        loop.add_program(prog, source_path=py_file)
```

**Usage:**
```bash
python run.py --benchmark gsm8k --samples 200 --cycles 10 --budget 5.0
python run.py --resume results/gsm8k_20260228_075055 --cycles 15 --budget 10.0
```

---

### 5.2 The RSI Loop Orchestrator

**File:** `src/meta/loop.py` — `RSILoop` class

This is the heart of the system. The `run()` method (lines 156-344) implements the full optimization loop.

**Key data structures:**

```python
# loop.py, lines 82-87
# The evolving population of programs
self.library: list[tuple[ReasoningProgram, Path | None]] = []

# Evaluation cache: id(program) -> list[EvalResult]
# Unchanged programs skip re-evaluation
self._eval_cache: dict[int, list[EvalResult]] = {}
```

**The main loop (simplified):**

```python
# loop.py, lines 174-341 (simplified)
for cycle in range(start_cycle, max_cycles + 1):

    # 1. Evaluate all library programs
    for program, _ in self.library:
        prog_id = id(program)
        if prog_id in self._eval_cache:          # Cache hit — skip
            all_results[prog_id] = self._eval_cache[prog_id]
            continue
        results = await self.evaluate_program(program, tasks, cycle)
        all_results[prog_id] = results
        self._eval_cache[prog_id] = results

    # 2. Analyze failures across ALL programs
    all_failures = [r for results in all_results.values()
                    for r in results if not r.correct]
    failure_classes, failure_summary = await self.analyzer.analyze(
        all_failures, self.models, self.router, cost_tracker=self.cost_tracker
    )

    # 3. Mutate the best-scoring program
    stats_sorted = sorted(self._compute_stats(all_results),
                          key=lambda s: s.avg_score, reverse=True)
    best_prog, best_path = ...  # find the top scorer
    source = best_path.read_text() if best_path else inspect.getfile(...)
    valid_paths = await self.mutator.mutate(
        parent_program=best_prog, parent_source=source,
        failure_summary=failure_summary, ...
    )

    # 4. Evaluate mutants
    for path in valid_paths:
        new_prog = load_program(path)
        results = await self.evaluate_program(new_prog, tasks, cycle)
        self.library.append((new_prog, path))

    # 5. Pareto select
    selected = self.selector.select(self._compute_stats(all_results))
    self.library = [(p, path) for p, path in self.library
                    if id(p) in {s.program_id for s in selected}]

    # 6-7. Log, check budget, check plateau
    if not self.cost_tracker.within_budget(): break
    if cycles_without_improvement >= self.plateau_patience: break
```

**Eval caching** (lines 184-195) is critical for efficiency. Programs that survive Pareto selection are not re-evaluated — their `id(program)` remains the same Python object, so the cache hits. Only newly generated mutants need evaluation.

**Source retrieval** for baselines uses `inspect.getfile()` (line 243):

```python
# loop.py, lines 238-243
if best_path and best_path.exists():
    source = best_path.read_text(encoding="utf-8")
else:
    # For baselines, read the full module file
    import inspect
    source = Path(inspect.getfile(type(best_prog))).read_text(encoding="utf-8")
```

This is how the system reads its own code — it literally opens the Python file of the winning program and passes the source code to the mutator LLM.

---

### 5.3 Program Interface and Baselines

**File:** `src/programs/interface.py`

Every program — baseline or generated — must implement this interface:

```python
# interface.py, lines 16-22
@dataclass
class Solution:
    answer: str   # The final answer string
    cost: float   # Total API cost in USD
    trace: str    # Log of all LLM calls made

class ReasoningProgram:
    name: str
    description: str

    async def solve(self, problem: dict, models: ModelRoster,
                    router: ModelRouter) -> Solution:
        raise NotImplementedError
```

**The contract:**
- `problem["question"]` — the problem text (string)
- `problem["context"]` — optional context (e.g., ARC training examples)
- Returns a `Solution` with the answer, cost, and execution trace
- Must be `async` (all LLM calls are async)

**Baselines (5 programs in `src/programs/baselines/`):**

```
┌──────────────────┬─────────────────────────────────────────────────┐
│ Program          │ Strategy                                        │
├──────────────────┼─────────────────────────────────────────────────┤
│ direct           │ 1 call, no system prompt. "Solve this problem." │
│ chain_of_thought │ 1 call, "Think step by step" system prompt.     │
│ decompose_solve  │ 2 calls: decompose → solve with sub-problems.   │
│ generate_verify  │ 2-6 calls: generate → verify → retry loop.      │
│ ensemble_vote    │ 3 parallel calls, majority voting.              │
└──────────────────┴─────────────────────────────────────────────────┘
```

Example — the `chain_of_thought` baseline (the ancestor of all winning programs):

```python
# baselines/chain_of_thought.py, lines 29-67
class ChainOfThoughtProgram(ReasoningProgram):
    name = "chain_of_thought"
    description = "Single cheap LLM call with a chain-of-thought system prompt."

    _SYSTEM = (
        "Think step by step. After your reasoning, write your final answer "
        "on a new line starting with 'ANSWER:'"
    )

    async def solve(self, problem, models, router) -> Solution:
        question = problem["question"]
        config, provider = router.route("cheap")
        response, cost = await provider.complete(
            prompt=question, system=self._SYSTEM,
            temperature=0.0, model_name=config.name,
        )
        answer = _extract_answer(response)
        trace = _build_trace_entry(1, config.name, cost, question, response)
        return Solution(answer=answer, cost=cost, trace=trace)
```

---

### 5.4 Evaluation Layer

**Files:** `src/evaluator/base.py`, `src/evaluator/gsm8k.py`, `src/evaluator/arc.py`

The evaluation layer is **frozen** — it is never modified by the RSI loop. This ensures a stable fitness function.

```python
# evaluator/base.py (key types)
@dataclass
class BenchmarkTask:
    task_id: str
    question: str
    expected_answer: str
    context: dict | None = None

@dataclass
class EvalResult:
    program_id: str
    task_id: str
    correct: bool
    score: float      # 1.0 if correct, 0.0 if not
    cost: float       # USD spent on this single evaluation
    trace: str        # Full execution trace

class Evaluator(ABC):
    @abstractmethod
    def load_tasks(self, n_samples: int = 100) -> list[BenchmarkTask]: ...

    @abstractmethod
    def check_answer(self, predicted: str, expected: str) -> bool: ...
```

**GSM8K evaluator:** Extracts the last number from the LLM's output and compares it numerically to the expected answer (handles floats, commas, dollar signs).

**ARC evaluator:** Parses a 2D grid from the output and checks exact cell-by-cell match against the expected grid.

Both evaluators use `seed=42` for reproducible sampling — every cycle evaluates the same set of problems.

**How evaluation works in the loop:**

```python
# loop.py, lines 93-128
async def evaluate_program(self, program, tasks, cycle):
    results = []
    for task in tasks:
        problem = {"question": task.question, "context": task.context}

        solution = await run_program_safe(program, problem, self.models, self.router)
        self.cost_tracker.add(solution.cost, model_name="program_eval")
        correct = self.evaluator.check_answer(solution.answer, task.expected_answer)

        result = EvalResult(
            program_id=program.name, task_id=task.task_id,
            correct=correct, score=1.0 if correct else 0.0,
            cost=solution.cost, trace=solution.trace,
        )
        results.append(result)
        self._log_eval_result(cycle, program.name, task, result, solution.answer)
    return results
```

Each evaluation produces a telemetry record in `eval_log.jsonl`:

```json
{"cycle": 1, "program": "chain_of_thought", "task_id": "gsm8k_123",
 "question": "...", "expected": "42", "predicted": "42",
 "correct": true, "score": 1.0, "cost": 0.000199, "trace": "..."}
```

---

### 5.5 Failure Analyzer

**File:** `src/meta/analyzer.py` — `FailureAnalyzer` class

The analyzer examines WHY programs fail. It categorizes failures into 8 classes that the mutator can target:

```python
# analyzer.py, lines 17-26
FAILURE_CLASSES = [
    "arithmetic_error",          # Wrong calculation
    "misread_problem",           # Misunderstood the question
    "wrong_decomposition",       # Broke problem into wrong sub-problems
    "hallucinated_constraint",   # Added non-existent constraint
    "timeout",                   # Exceeded 30s time limit
    "code_error",                # Program crashed
    "formatting_error",          # Answer correct but badly formatted
    "wrong_strategy",            # Fundamentally wrong approach
]
```

**Two-phase classification:**

```
Phase 1: LOCAL (no LLM cost)         Phase 2: LLM (1 strong-tier call)
┌──────────────────────────┐          ┌──────────────────────────┐
│ Scan trace for markers:  │          │ Sample up to 20 failures │
│                          │          │                          │
│ "timeout after" → timeout│          │ Send to strong LLM:      │
│ "Traceback"     → error  │          │   "Classify each failure │
│                          │          │    into one of 8 classes" │
│ Cost: $0.00              │          │                          │
│                          │          │ Parse response:          │
│ Handles: ~5-10% of       │          │   "Failure 1: arithmetic │
│ failures automatically   │          │    Failure 2: misread..."│
└──────────────────────────┘          │                          │
                                      │ Propagate to unsampled:  │
                                      │   assign dominant class  │
                                      │                          │
                                      │ Cost: 1 strong LLM call  │
                                      └──────────────────────────┘
```

**Local detection** (lines 36-48):

```python
# analyzer.py, lines 36-48
def _detect_trace_class(trace: str) -> str | None:
    lower = trace.lower()
    if any(marker in lower for marker in _TIMEOUT_MARKERS):
        return "timeout"
    if any(marker in lower for marker in _CODE_ERROR_MARKERS):
        return "code_error"
    return None
```

**LLM categorization** (lines 223-270): Sends up to 20 sampled failures (with 500-char trace excerpts) to a strong-tier LLM. The response is parsed line-by-line for `"Failure N: <class>"` patterns. The `SUMMARY:` section becomes the natural-language failure summary passed to the mutator.

**Output:** A tuple of `(failure_classes: dict[str, list[EvalResult]], summary: str)`.

---

### 5.6 Program Mutator

**File:** `src/meta/mutator.py` — `ProgramMutator` class

This is where new programs are born. The mutator takes the best program's source code and the failure analysis, and asks a strong LLM to write an improved version.

**Two mutation strategies:**

```
┌──────────────────────────────────────────────────────────────────┐
│                    PROMPT MUTATION                                │
│                                                                  │
│  "Rewrite the prompts in this program to address the reported    │
│   failure patterns. Keep the same class structure and solve()    │
│   flow. Only change the prompt strings."                         │
│                                                                  │
│  → Changes WHAT the program says to the LLM                     │
│  → Does not change architecture                                 │
└──────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────┐
│                   STRATEGY INJECTION                              │
│                                                                  │
│  "Add new reasoning steps to address the top failure pattern:    │
│   '{top_failure}'."                                              │
│                                                                  │
│  Examples:                                                       │
│    arithmetic_error     → add arithmetic verification step       │
│    misread_problem      → add problem restatement step           │
│    wrong_decomposition  → add sub-problem validation step        │
│                                                                  │
│  → Changes HOW the program works                                │
│  → Can add LLM calls, helper functions, new logic               │
└──────────────────────────────────────────────────────────────────┘
```

**The mutation process for each candidate:**

```
┌─────────────┐     ┌──────────────────┐     ┌────────────────┐
│ 1. Pick     │     │ 2. Build prompt  │     │ 3. Call strong  │
│ strategy:   │────▶│                  │────▶│ LLM (temp=0.7) │
│ random.     │     │ instruction +    │     │                │
│ choice()    │     │ failure_summary +│     │ "Write the     │
│             │     │ parent_source    │     │  improved      │
│ 50/50       │     │                  │     │  program"      │
└─────────────┘     └──────────────────┘     └───────┬────────┘
                                                      │
┌─────────────┐     ┌──────────────────┐     ┌───────▼────────┐
│ 6. Return   │     │ 5. Validate:     │     │ 4. Extract     │
│ list of     │◀────│ - syntax ok?     │◀────│ ```python      │
│ valid paths │     │ - imports ok?    │     │ code block     │
│             │     │ - has subclass?  │     │ from response  │
│             │     │ - has solve()?   │     │                │
│             │     │ - is async?      │     │ Write to       │
│             │     │                  │     │ generated/     │
│             │     │ Invalid → delete │     │ {name}_mut_N.py│
└─────────────┘     └──────────────────┘     └────────────────┘
```

**The prompt sent to the LLM** (lines 87-104):

```python
# mutator.py, lines 87-104
def _build_mutation_prompt(parent_source, failure_summary, strategy, top_failure):
    if strategy == _STRATEGY_PROMPT_MUTATION:
        instruction = _PROMPT_MUTATION_INSTRUCTION
    else:
        instruction = _STRATEGY_INJECTION_INSTRUCTION.format(top_failure=top_failure)

    return (
        f"{instruction}\n\n"
        f"FAILURE SUMMARY:\n{failure_summary}\n\n"
        f"PARENT PROGRAM SOURCE:\n```python\n{parent_source}\n```\n\n"
        "Now produce the improved program:"
    )
```

**The LLM call** (lines 202-219):

```python
# mutator.py, lines 202-216
config, provider = router.route("strong")      # Uses strong-tier model
response, cost = await provider.complete(
    prompt=prompt,
    system=_SYSTEM_PROMPT,    # "You are an expert Python programmer..."
    temperature=0.7,          # Higher temp for creative variation
    model_name=config.name,
)
if cost_tracker:
    cost_tracker.add(cost, model_name=config.name)
```

**Validation** (via `sandbox.validate_program()`, 6 checks):
1. File exists
2. Valid Python syntax (`compile()`)
3. Module imports successfully
4. Contains exactly one `ReasoningProgram` subclass
5. Class has `name` and `description` string attributes
6. Class has an `async def solve()` method

Invalid candidates are deleted from disk (line 273).

---

### 5.7 Pareto Selector

**File:** `src/meta/selector.py` — `ParetoSelector` class

The selector decides which programs survive to the next generation. It uses **Pareto dominance** on two objectives: maximize accuracy, minimize cost.

**Dominance definition:**

```
Program A DOMINATES Program B when:
  - A.score >= B.score  AND  A.cost <= B.cost
  - At least one inequality is strict

If A dominates B, then B is PRUNED (removed from the library).
Programs that are NOT dominated by any other program form the
"Pareto frontier" — the set of optimal tradeoffs.
```

**Visual example:**

```
Score ↑
 97%  │  ●  v3                 ← Pareto frontier (non-dominated)
 96%  │  ●  v2                 ← Pareto frontier
 95.5%│  ●  enhanced_cot       ← Pareto frontier
 95%  │  ╳  chain_of_thought   ← DOMINATED (enhanced_cot: better score, same cost)
 94%  │  ●  direct             ← Pareto frontier (cheapest)
 92%  │     ╳  decomp_valid    ← DOMINATED (direct: better score AND cheaper)
 91%  │     ╳  decompose_solve ← DOMINATED (direct: better score AND cheaper)
 80%  │        ╳  gen_verify   ← DOMINATED
 73%  │           ╳  ensemble  ← DOMINATED
      └──────────────────────────── Cost →
      $0.00016  $0.00020  $0.00050
```

**The algorithm** (lines 53-146):

```python
# selector.py, lines 94-138 (simplified)
# Step 1: Find non-dominated programs
non_dominated = [
    prog for prog in candidates
    if not _is_dominated(prog, candidates)
]

# Step 2: Always keep the highest-scoring program (safety net)
best_by_score = max(candidates, key=lambda p: p.avg_score)
if best_by_score not in non_dominated:
    non_dominated.append(best_by_score)

# Step 3: Trim to max_library_size by score if needed
if len(kept) > max_library_size:
    kept = sorted(kept, key=lambda p: p.avg_score, reverse=True)[:max_library_size]
```

**The dominance check** (lines 26-47):

```python
# selector.py, lines 26-47
def _is_dominated(candidate, others):
    for other in others:
        if other.program_id == candidate.program_id:
            continue
        score_at_least_as_good = other.avg_score >= candidate.avg_score
        cost_at_least_as_good  = other.avg_cost  <= candidate.avg_cost
        strictly_better = (
            other.avg_score > candidate.avg_score
            or other.avg_cost < candidate.avg_cost
        )
        if score_at_least_as_good and cost_at_least_as_good and strictly_better:
            return True
    return False
```

---

### 5.8 Model Router and Cost Tracking

**Files:** `src/models/router.py`, `src/utils/cost_tracker.py`

**Model tiers:**

```
┌─────────────────────────────────────────────────────────────────┐
│                        CHEAP TIER                                │
│  Used by: all program evaluations (router.route("cheap"))       │
│                                                                  │
│  gpt-4o-mini        ($0.15/$0.60 per 1M tokens)                │
│  gemini-2.5-flash   ($0.15/$0.60 per 1M tokens)                │
│  claude-haiku-4-5   ($0.80/$4.00 per 1M tokens)                │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│                       STRONG TIER                                │
│  Used by: analyzer (1 call/cycle), mutator (3 calls/cycle)      │
│                                                                  │
│  gpt-4o             ($2.50/$10.00 per 1M tokens)                │
│  claude-sonnet-4-6  ($3.00/$15.00 per 1M tokens)                │
│  gemini-2.5-pro     ($1.25/$10.00 per 1M tokens)                │
└─────────────────────────────────────────────────────────────────┘
```

**Router selection within a tier** (lines 140-161):

```python
# router.py, lines 140-161
def _pick(self, candidates):
    if len(candidates) == 1:
        return candidates[0]
    # Weighted random by historical success rate (default 1.0)
    weights = [self.success_rate(c.name) for c, _ in candidates]
    if sum(weights) == 0:
        return random.choice(candidates)
    return random.choices(candidates, weights=weights, k=1)[0]
```

**Budget enforcement** (router.py, lines 78-85):

```python
# router.py, lines 78-85
if not self.cost_tracker.within_budget():
    tier = "cheap"  # Force cheap tier when budget exhausted
```

**Cost tracker** (cost_tracker.py): Simple accumulator with per-model breakdown and budget cap.

```python
# cost_tracker.py, lines 12-43
@dataclass
class CostTracker:
    budget_usd: float | None = None   # None = no cap

    def add(self, cost_usd: float, model_name: str = "unknown"):
        self._total_spent += cost_usd
        self._per_model[model_name] += cost_usd

    def within_budget(self) -> bool:
        if self.budget_usd is None: return True
        return self._total_spent < self.budget_usd
```

---

### 5.9 Sandbox: Dynamic Loading and Execution

**File:** `src/utils/sandbox.py`

The sandbox handles two critical operations: loading generated Python modules at runtime, and executing them safely with timeouts.

**Dynamic loading** (lines 26-115):

```python
# sandbox.py, lines 26-115 (simplified)
def load_program(module_path: Path) -> ReasoningProgram | None:
    # 1. Compile check (catch syntax errors cheaply)
    source = module_path.read_text(encoding="utf-8")
    compile(source, str(module_path), "exec")

    # 2. Dynamic import with unique module name
    module_name = f"_poetiq_generated_{module_path.stem}"
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(module_name, None)  # Clean up sys.modules

    # 3. Find the ReasoningProgram subclass
    subclasses = [obj for _, obj in inspect.getmembers(module, inspect.isclass)
                  if issubclass(obj, ReasoningProgram) and obj is not ReasoningProgram]

    # 4. Instantiate and return
    return subclasses[0]()
```

**Safe execution** (lines 118-152):

```python
# sandbox.py, lines 118-152
async def run_program_safe(program, problem, models, router, timeout=30):
    try:
        solution = await asyncio.wait_for(
            program.solve(problem, models, router),
            timeout=timeout,
        )
        return solution
    except asyncio.TimeoutError:
        return Solution(answer="ERROR", cost=0.0, trace=f"Timeout after {timeout}s")
    except Exception as exc:
        return Solution(answer="ERROR", cost=0.0, trace=f"Error: {exc}\n{tb}")
```

The 30-second timeout prevents runaway programs (e.g., infinite loops) from blocking the system.

**Circular import avoidance:** The sandbox uses `TYPE_CHECKING` guards (line 15) and lazy imports (lines 32, 130) for `ReasoningProgram` and `Solution` to avoid circular dependencies with `src.programs.interface`.

---

## 6. Data Flow Through One Cycle

Here is the complete data flow for one RSI cycle, showing what crosses each boundary:

```
                    ┌──────────────────────────────────────────┐
                    │           RSILoop.run()                   │
                    │                                          │
  ┌─────────────────┼──────────────────────────────────────────┼─────────────┐
  │                 │                                          │             │
  │   EVALUATE      │                                          │  EVALUATE   │
  │                 │                                          │  MUTANTS    │
  │  library[]──────┤                                          │             │
  │       │         │                                          │    ┌───┐    │
  │       ▼         │                                          │    │*.py│   │
  │  ┌─────────┐    │                                          │    └─┬─┘    │
  │  │program  │    │                                          │      │      │
  │  │.solve() │    │                                          │ load_program│
  │  └────┬────┘    │                                          │      │      │
  │       │ Solution│                                          │      ▼      │
  │       ▼         │                                          │  ┌───────┐  │
  │  check_answer() │                                          │  │program│  │
  │       │         │                                          │  │.solve()│ │
  │       ▼         │                                          │  └───┬───┘  │
  │  EvalResult[]   │                                          │      │      │
  │       │         │                                          │ EvalResult[]│
  └───────┼─────────┘                                          └──────┼──────┘
          │                                                           │
          │ all failures (correct==False)                              │
          ▼                                                           │
  ┌──────────────────┐                                                │
  │   ANALYZER        │                                               │
  │                   │                                               │
  │  1. Auto-detect   │                                               │
  │     timeout/error │                                               │
  │  2. LLM classify  │                                               │
  │     (20 samples)  │                                               │
  │                   │                                               │
  │  Output:          │                                               │
  │   failure_summary │                                               │
  │   (string)        │                                               │
  └────────┬──────────┘                                               │
           │                                                          │
           │ failure_summary + parent_source                          │
           ▼                                                          │
  ┌──────────────────┐          ┌────────────────────┐                │
  │   MUTATOR         │          │   GENERATED DIR    │               │
  │                   │          │                    │               │
  │  For each of 3:   │  write   │   parent_mut_1.py  │───────────────┘
  │   1. Pick strategy│────────▶ │   parent_mut_2.py  │
  │   2. LLM generate │          │   parent_mut_3.py  │
  │   3. Extract code │          │                    │
  │   4. Validate     │          └────────────────────┘
  │                   │
  └───────────────────┘
                                        after all evaluations
                                                │
                                                ▼
                                  ┌──────────────────────┐
                                  │   PARETO SELECTOR     │
                                  │                       │
                                  │  Input:               │
                                  │   ProgramStats[] for  │
                                  │   ALL library programs │
                                  │                       │
                                  │  Output:              │
                                  │   pruned library      │
                                  │   (non-dominated set) │
                                  └───────────┬──────────┘
                                              │
                                              ▼
                                  library[] for next cycle
```

---

## 7. The Genetic Analogy

The system draws on evolutionary computation concepts:

```
┌───────────────────┬──────────────────────────────────────────────┐
│ Genetic Concept   │ RSI Implementation                           │
├───────────────────┼──────────────────────────────────────────────┤
│ Organism          │ ReasoningProgram (Python module)             │
│ DNA / Genotype    │ Program source code (the .py file)          │
│ Phenotype         │ Program behavior (how it solves problems)   │
│ Fitness function  │ Accuracy × (1/cost) on benchmark tasks      │
│ Population        │ RSILoop.library[]                           │
│ Mutation          │ LLM rewrites source code                    │
│ Selection         │ Pareto dominance on (score, cost)           │
│ Generation        │ One RSI cycle                               │
│ Seed population   │ 5 baseline programs                         │
│ Mutation rate     │ 3 candidates per cycle                      │
│ Fitness landscape │ GSM8K accuracy surface                      │
│ Extinction        │ Pareto-dominated → removed from library     │
│ Plateau/stasis    │ plateau_patience stopping condition         │
└───────────────────┴──────────────────────────────────────────────┘
```

**Key difference from traditional genetic algorithms:** Mutations are not random bit-flips. They are **guided** by the failure analysis — the LLM knows what went wrong and tries to fix it. This makes the search far more directed than blind random mutation.

**Key difference from traditional prompt optimization:** The unit of optimization is a complete Python module, not a prompt string. Mutations can change program architecture (add LLM calls, add helper functions, add programmatic verification). In practice, the most successful mutations turned out to be prompt changes, but the system has the capacity for architectural innovation.

---

## 8. How to Evolve the Code

The RSI system itself can be extended in several ways:

### 8.1 Add New Baselines

Create a new file in `src/programs/baselines/`:

```python
# src/programs/baselines/my_strategy.py
from src.programs.interface import ReasoningProgram, Solution
from src.models.base import ModelRoster
from src.models.router import ModelRouter

class MyStrategyProgram(ReasoningProgram):
    name = "my_strategy"
    description = "Describe what makes this strategy unique."

    async def solve(self, problem, models, router) -> Solution:
        question = problem["question"]
        config, provider = router.route("cheap")
        response, cost = await provider.complete(
            prompt=question, system="Your system prompt here",
            temperature=0.0, model_name=config.name,
        )
        # ... extract answer, build trace ...
        return Solution(answer=answer, cost=cost, trace=trace)
```

Then add it to `run.py`:

```python
from src.programs.baselines.my_strategy import MyStrategyProgram
baselines.append(MyStrategyProgram())
```

### 8.2 Add New Benchmarks

Implement the `Evaluator` interface in `src/evaluator/`:

```python
# src/evaluator/my_benchmark.py
from src.evaluator.base import Evaluator, BenchmarkTask

class MyBenchmarkEvaluator(Evaluator):
    name = "my_benchmark"

    def load_tasks(self, n_samples=100) -> list[BenchmarkTask]:
        # Load and return benchmark tasks
        ...

    def check_answer(self, predicted: str, expected: str) -> bool:
        # Compare predicted vs expected
        ...
```

### 8.3 Add New Mutation Strategies

Extend `_MUTATION_STRATEGIES` in `src/meta/mutator.py`:

```python
# Add a new strategy that combines two parent programs
_STRATEGY_CROSSOVER = "crossover"
_MUTATION_STRATEGIES.append(_STRATEGY_CROSSOVER)

_CROSSOVER_INSTRUCTION = (
    "Combine the best ideas from these TWO parent programs into a "
    "single improved program. Parent A excels at {strength_a}. "
    "Parent B excels at {strength_b}. Merge their strategies."
)
```

### 8.4 Multi-Parent Mutation

Currently only the best-scoring program is mutated (line 232 of `loop.py`). To mutate diverse parents:

```python
# In loop.py, replace single-parent selection with:
parents = stats_sorted[:3]  # Top 3 programs
for parent_stat in parents:
    parent_prog, parent_path = ...
    valid_paths = await self.mutator.mutate(
        parent_program=parent_prog, parent_source=source,
        failure_summary=failure_summary, n_candidates=1, ...
    )
```

### 8.5 Add New Failure Classes

Extend `FAILURE_CLASSES` in `src/meta/analyzer.py`:

```python
FAILURE_CLASSES = [
    ...,
    "insufficient_context",   # New: didn't use all provided context
    "overconfident_wrong",    # New: confident but wrong answer
]
```

Then add corresponding injection recipes in `src/meta/mutator.py`:

```python
_STRATEGY_INJECTION_INSTRUCTION = (
    "...\n"
    "  - insufficient_context → add a context extraction/verification step\n"
    "  - overconfident_wrong  → add a self-doubt/double-check step\n"
)
```

### 8.6 Add Strong-Tier Evaluation

Currently all programs use `router.route("cheap")`. To test whether strong models break the accuracy ceiling:

```python
# In a new baseline or mutation instruction:
config, provider = router.route("strong")  # Use gpt-4o / claude-sonnet / gemini-pro
```

### 8.7 Parallel Evaluation

Currently evaluation is sequential (one program, one task at a time). For faster cycles:

```python
# In loop.py, replace sequential evaluation with:
async def evaluate_program(self, program, tasks, cycle):
    async def eval_one(task):
        solution = await run_program_safe(program, ...)
        ...
        return result

    results = await asyncio.gather(*[eval_one(t) for t in tasks])
    return results
```

---

## 9. File Reference

| File | Lines | Role |
|------|-------|------|
| `run.py` | 189 | CLI entry point, wires infrastructure, launches loop |
| `src/meta/loop.py` | 437 | RSI orchestrator — the core cycle |
| `src/meta/analyzer.py` | 302 | Failure categorization (local + LLM) |
| `src/meta/mutator.py` | 283 | Program generation via LLM mutation |
| `src/meta/selector.py` | 147 | Pareto-optimal selection on (score, cost) |
| `src/programs/interface.py` | ~55 | `ReasoningProgram` base class + `Solution` |
| `src/programs/baselines/*.py` | 5 files | Hand-written seed programs |
| `src/programs/generated/*.py` | 36 files | Auto-generated mutant programs |
| `src/evaluator/base.py` | ~80 | `Evaluator` ABC, `EvalResult`, `BenchmarkTask` |
| `src/evaluator/gsm8k.py` | ~120 | GSM8K math benchmark evaluator |
| `src/evaluator/arc.py` | ~180 | ARC-AGI visual reasoning evaluator |
| `src/models/base.py` | ~130 | `ModelConfig`, `LLMProvider`, `ModelRoster` |
| `src/models/router.py` | 162 | Cost-aware tier routing + model selection |
| `src/utils/cost_tracker.py` | 70 | Budget tracking and enforcement |
| `src/utils/sandbox.py` | 242 | Dynamic module loading + safe execution |
