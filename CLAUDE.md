# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

RSI (Recursive Self-Improvement) Proof of Concept — a program-genetic system that generates executable reasoning programs, evaluates them against benchmarks (GSM8K math, ARC-AGI visual reasoning), and optimizes through mutation and Pareto selection. Programs (not prompts) are the unit of optimization.

## Commands

```bash
# Install
pip install -e ".[dev]"

# Run RSI loop
python run.py --benchmark gsm8k --samples 20 --cycles 3 --budget 5.0
python run.py --benchmark arc --samples 50 --cycles 10 --budget 25.0

# Lint & format
black --check --line-length 100 src/
ruff check src/
mypy src/

# Tests
pytest                        # all tests
pytest tests/test_foo.py      # single file
pytest tests/test_foo.py::test_bar -v  # single test
```

## Environment

Requires Python 3.11+. API keys in `.env` (see `.env.example`):
- `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`
- Missing keys are handled gracefully — that provider is simply unavailable.

## Architecture

The system runs a 5-step optimization cycle: **evaluate → analyze failures → mutate → evaluate mutants → Pareto select**.

### Core Loop (`src/meta/loop.py` — `RSILoop`)
Orchestrates the cycle. Stops on budget exhaustion or plateau (no improvement for N consecutive cycles). Saves results to `results/{benchmark}_{timestamp}/`.

### Models (`src/models/`)
- `base.py`: `ModelConfig`, `LLMProvider` (abstract), `ModelRoster` — all async
- `router.py`: `ModelRouter` — cost-aware tier selection ("cheap"/"strong"), tracks per-model success rates
- 3 providers: OpenAI (gpt-4o-mini, gpt-4o), Anthropic (haiku, sonnet), Google (gemini-flash, gemini-pro)
- `build_default_roster()` factory instantiates everything

### Programs (`src/programs/`)
- `interface.py`: `ReasoningProgram` base class + `Solution` dataclass
- `baselines/`: 5 strategies — direct, chain_of_thought, decompose_solve, generate_verify, ensemble_vote
- `generated/`: Auto-generated mutant programs (gitignored). Each is a valid Python module with a `ReasoningProgram` subclass

### Evaluators (`src/evaluator/`)
Frozen evaluation layer. `GSM8KEvaluator` extracts last number from output for comparison. `ARCEvaluator` does exact grid match. Both load reproducible samples (seed=42).

### Meta System (`src/meta/`)
- `analyzer.py`: Clusters failures into pattern classes (arithmetic_error, misread_problem, etc.) using LLM categorization
- `mutator.py`: Generates new program variants via prompt mutation or strategy injection. Validates syntax/structure before writing to `generated/`
- `selector.py`: Pareto frontier on (score, cost) — keeps programs that are either best-scoring or cheapest

### Utilities (`src/utils/`)
- `sandbox.py`: `load_program()` dynamically imports generated modules, `run_program_safe()` executes with 30s timeout, `validate_program()` checks structure
- `cost_tracker.py`: Budget tracking with per-model breakdown

## Key Design Decisions

- **Program identity**: Uses `id(program)` (object identity), not `program.name`, to avoid eval cache collisions
- **Circular import avoidance**: `sandbox.py` uses `TYPE_CHECKING` guard and lazy imports for `ReasoningProgram`/`Solution`
- **Source retrieval**: `inspect.getfile(type(program))` gets full module source for baselines
- **Meta-cost tracking**: Analyzer and mutator receive `cost_tracker` param to track their own LLM costs against the budget
- **Eval caching**: Results cached by `id(program)` — unchanged programs are not re-evaluated

## Conventions

- `from __future__ import annotations` in all files
- All LLM interaction is `async def`
- Dataclasses for all data carriers (`Solution`, `EvalResult`, `BenchmarkTask`, `ModelConfig`, `CycleResult`)
- Black line-length 100, ruff with E/F/I/UP rules
- Logging via `logging.getLogger(__name__)` in every module
