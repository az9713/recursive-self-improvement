# Test Report #001: GSM8K Small Run

**Date:** 2026-02-27
**Duration:** ~34 minutes (13:32 — 14:06)
**Total Cost:** $0.17
**Result:** All 3 cycles completed successfully. System works end-to-end.

---

## 1. What This Test Validates

This is a **smoke test** — a minimal end-to-end run to verify every component of the RSI pipeline works together before committing real budget.

### Components exercised

| Component | What was tested |
|---|---|
| **GSM8K evaluator** | Downloads from HuggingFace, samples 20 problems, parses `####` answers, normalizes numeric comparison |
| **5 baseline programs** | All 5 loaded, executed, and scored against benchmark |
| **Cost-aware router** | Round-robin across 2 providers (Google + OpenAI), cheap vs. strong tier routing |
| **Failure analyzer** | Categorized 1 failure as `arithmetic_error` using GPT-4o |
| **Program mutator** | Generated 9 mutants (3 per cycle) using `prompt_mutation` and `strategy_injection` strategies |
| **Sandbox validator** | All 9 mutants passed 6-step validation (syntax, imports, interface, etc.) |
| **Pareto selector** | Pruned library from 5 baselines + mutants down to 3 survivors per cycle |
| **Cost tracker** | Tracked per-model spend, enforced $5.00 budget cap |
| **Results persistence** | Saved `cycles.jsonl`, `cost_log.jsonl`, `final_library.json` |

### What was NOT tested

- ARC-AGI benchmark (visual reasoning)
- Anthropic provider (no API key configured)
- Plateau detection (baselines already scored very high)
- Budget exhaustion behavior (spent only $0.17 of $5.00 cap)
- Large sample sizes (20 problems is too small for statistical significance)

---

## 2. Test Configuration

```bash
python run.py --benchmark gsm8k --samples 20 --cycles 3 --budget 5.0
```

| Parameter | Value |
|---|---|
| Benchmark | GSM8K (grade-school math, 1319 problems, sampled 20) |
| Samples | 20 problems (fixed seed=42 for reproducibility) |
| Cycles | 3 optimization cycles |
| Budget cap | $5.00 (hard stop) |
| Mutants per cycle | 3 candidates |
| Providers available | Google (Gemini 2.5 Flash + Pro), OpenAI (GPT-4o-mini + GPT-4o) |

---

## 3. Results

### Baseline Evaluation (Cycle 1)

| Program | Strategy | Score | Cost/Problem |
|---|---|---|---|
| direct | Single LLM call, no prompting | 19/20 (95%) | $0.000157 |
| **chain_of_thought** | "Think step by step" system prompt | **20/20 (100%)** | $0.000162 |
| decompose_solve | Break into sub-problems, solve together | 18/20 (90%) | higher |
| generate_verify | Generate + verify loop (up to 3 attempts) | evaluated | higher |
| self_refine | Self-critique and refine | evaluated | higher |

The baselines did show meaningful differentiation: direct (95%) < decompose_solve (90%) < chain_of_thought (100%). However, `chain_of_thought` hitting 100% on only 20 samples limits the headroom for improvement.

### Per-Cycle Evolution

| Cycle | Best Program | Best Score | Cost/Problem | Mutants | Valid | Total Spend |
|---|---|---|---|---|---|---|
| 1 | chain_of_thought | 100.0% | $0.000162 | 3 | 3/3 | $0.073 |
| 2 | chain_of_thought | 100.0% | $0.000162 | 3 | 3/3 | $0.120 |
| 3 | **improved_chain_of_thought** | 100.0% | $0.000161 | 3 | 3/3 | $0.167 |

### Final Library (3 surviving programs)

| Program | Type | Score | Cost/Problem |
|---|---|---|---|
| improved_chain_of_thought | **Generated mutant** (cycle 3) | 100.0% | $0.000161 |
| chain_of_thought | Baseline | 100.0% | $0.000162 |
| direct | Baseline | 95.0% | $0.000157 |

---

## 4. Does This Show RSI?

**Short answer: Partially. The system generated novel programs, but the test was too small to demonstrate meaningful self-improvement.**

### What the system DID do (mechanical RSI)

1. **Failure-driven mutation worked.** The analyzer identified `arithmetic_error` as the dominant failure class. The mutator then generated programs specifically targeting arithmetic verification — exactly as designed.

2. **Novel code was generated.** The winning mutant (`chain_of_thought_mut_18.py`) added a `_verify_arithmetic_steps()` function that the original baseline did not have. This is genuine program synthesis — the LLM wrote new Python code that was dynamically loaded and executed.

3. **Selection pressure was applied.** Pareto selection on the score-cost frontier pruned the library from 5+ programs down to 3 survivors each cycle, keeping only non-dominated solutions.

4. **The mutant survived selection.** `improved_chain_of_thought` replaced one of the baseline entries in the final library, demonstrating that the evolutionary loop can promote generated programs over hand-written ones.

### What limits the RSI claim

1. **Ceiling effect.** `chain_of_thought` already scored 100% in cycle 1. With a perfect score, there is no room for the meta-system to demonstrate improvement. The "improvement" in cycle 3 is only a marginal cost reduction ($0.000162 → $0.000161/problem), not a score gain.

2. **Sample size too small.** 20 problems is insufficient to differentiate programs statistically. A program scoring 95% vs 100% on n=20 is within noise (1 problem difference). Larger samples (100-200+) are needed for reliable signal.

3. **The mutant's verification is superficial.** Inspecting `chain_of_thought_mut_18.py`, the `_verify_arithmetic_steps()` function only checks for "half of" patterns — a narrow heuristic that would not generalize. It also only appends a note to the trace without re-prompting, so it cannot actually fix errors.

4. **No multi-generation compounding.** True RSI would show improvement compounding across generations (mutant of a mutant of a mutant). In 3 cycles with a ceiling at 100%, we cannot observe this.

### Verdict

The machinery for RSI is working — the analyze → mutate → evaluate → select loop runs correctly and produces novel programs. But this test does not demonstrate RSI in the meaningful sense (programs that improve themselves beyond human-written baselines on a challenging benchmark). That requires:
- A harder benchmark or larger sample where baselines don't hit 100%
- More cycles to observe compounding improvement
- Problems where generated strategies genuinely outperform hand-crafted ones

---

## 5. Cost Breakdown

### How costs are computed

Every LLM call returns a `(response_text, cost_usd)` tuple. Cost is calculated as:

```
cost = (input_tokens * cost_per_1k_input / 1000) + (output_tokens * cost_per_1k_output / 1000)
```

Token counts come from the provider API's usage metadata. The `CostTracker` accumulates these per-model and checks against the budget cap before each cycle.

### Cost by category

| Category | Model Used | Cost | Description |
|---|---|---|---|
| **Program evaluation** | gemini-2.5-flash + gpt-4o-mini | $0.075 | Running all programs on all 20 problems (cheap tier) |
| **Failure analysis** | gpt-4o | included in gpt-4o total | 3 calls to categorize failures (strong tier) |
| **Program mutation** | gemini-2.5-pro + gpt-4o | $0.042 + $0.051 | 9 mutation calls across 3 cycles (strong tier) |
| **Total** | | **$0.167** | |

### Cost by model

| Model | Tier | Price (input/output per 1M tokens) | Total Spend |
|---|---|---|---|
| gemini-2.5-flash | cheap | $0.15 / $0.60 | part of $0.075 eval |
| gpt-4o-mini | cheap | $0.15 / $0.60 | part of $0.075 eval |
| gemini-2.5-pro | strong | $1.25 / $10.00 | $0.042 |
| gpt-4o | strong | $2.50 / $10.00 | $0.051 |

### Cost per cycle

| Cycle | Incremental Cost | Cumulative | Notes |
|---|---|---|---|
| 1 | $0.073 | $0.073 | Full baseline eval (5 programs x 20 problems) + first mutation |
| 2 | $0.047 | $0.120 | Cached baselines, only eval new mutants + mutation |
| 3 | $0.047 | $0.167 | Same as cycle 2 (caching saves re-evaluation) |

Cycle 1 is most expensive because all 5 baselines must be evaluated from scratch. Cycles 2-3 benefit from caching — only new mutant programs are re-evaluated.

---

## 6. Issues Encountered

### `gemini-2.0-flash` deprecated (FIXED)

The original model configuration used `gemini-2.0-flash`, which Google has retired for new users (404 errors). Fixed by updating to `gemini-2.5-flash` in `src/models/google_provider.py`.

### OpenAI key active despite being commented out

The `.env` file has `OPENAI_API_KEY` commented out, but the system still used GPT-4o-mini and GPT-4o successfully. This means there's an `OPENAI_API_KEY` set in the system environment. Not a bug, but worth noting — the router distributed calls across both providers as designed.

---

## 7. Next Tests

### Test #002: GSM8K Large (establish real baselines)

```bash
python run.py --benchmark gsm8k --samples 200 --cycles 10 --budget 10.0
```

**Purpose:** With 200 problems, baselines will not hit 100%. This creates headroom for the meta-system to demonstrate actual score improvement across cycles.

**Expected cost:** $2-5 (200 problems x 5 baselines = 1000 eval calls + 30 mutation calls)

**What to look for:**
- Do baselines separate clearly? (e.g., direct ~60%, CoT ~80%, decompose ~75%)
- Does the best score increase across cycles?
- Do generated programs use genuinely novel strategies?
- Does cost/problem decrease while maintaining score?

### Test #003: ARC-AGI Smoke Test

```bash
python run.py --benchmark arc --samples 50 --cycles 3 --budget 5.0
```

**Purpose:** Verify the ARC-AGI evaluator works end-to-end. ARC tasks (visual grid transformations) are fundamentally harder than math — cheap models struggle, so there's more room for improvement.

**Expected cost:** $1-3

**What to look for:**
- Do baselines score above 0%? (ARC is very hard for LLMs)
- Does the evaluator correctly parse grid outputs?
- Can the mutator generate meaningful strategies for visual reasoning?

### Test #004: ARC-AGI Full Run

```bash
python run.py --benchmark arc --samples 100 --cycles 15 --budget 25.0
```

**Purpose:** The real test of RSI capability. ARC-AGI is where baseline LLMs fail badly and where novel reasoning strategies could make a difference.

**Expected cost:** $10-25

**What to look for:**
- Sustained improvement over 15 cycles
- Mutants that introduce genuinely new approaches (e.g., grid symmetry detection, pattern matching)
- Whether the system discovers strategies humans wouldn't hand-code

### Test #005: Multi-Provider Comparison

Add Anthropic API key, then re-run Test #002.

**Purpose:** Compare whether the cost-aware router makes good decisions when all 3 providers are available. Does it correctly use the cheapest models for eval and strongest for mutation?

---

## 8. Files Produced

```
results/gsm8k_20260227_133155/
  cycles.jsonl          # Per-cycle metrics (library size, best score, spend, duration)
  cost_log.jsonl        # Final cost breakdown by model
  final_library.json    # 3 surviving programs with descriptions and source paths

src/programs/generated/
  chain_of_thought_mut_1.py   # Cycle 1 mutant (not in final library)
  ...
  chain_of_thought_mut_18.py  # Cycle 3 winner (in final library)
```
