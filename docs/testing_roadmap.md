# RSI Testing Roadmap: Past, Present, and Future

**Date:** 2026-03-01

This document covers what tests have been run, what went wrong with the incomplete test #002, what tests should come next (including ARC-AGI), and cost/time projections for each.

---

## Table of Contents

1. [Tests Completed](#1-tests-completed)
2. [Test #002 Is Incomplete — What Happened](#2-test-002-is-incomplete--what-happened)
3. [Completing Test #002](#3-completing-test-002)
4. [Next Tests: ARC-AGI](#4-next-tests-arc-agi)
5. [Beyond: Advanced Experiments](#5-beyond-advanced-experiments)
6. [Cost and Time Reference](#6-cost-and-time-reference)
7. [Model Inventory](#7-model-inventory)

---

## 1. Tests Completed

### Test #001: GSM8K Smoke Test

| Field | Value |
|-------|-------|
| **Date** | 2026-02-27 |
| **Command** | `python run.py --benchmark gsm8k --samples 20 --cycles 3 --budget 5.0` |
| **Samples** | 20 GSM8K problems |
| **Cycles** | 3 (completed) |
| **Cost** | $0.17 |
| **Wall time** | 34 minutes |
| **Result** | All systems functional. Baseline `chain_of_thought` hit 100% on 20 samples — no headroom for improvement. |
| **Report** | `docs/test_report_001_gsm8k_small.md` |

**Purpose:** Validate that every component of the RSI pipeline works end-to-end (evaluator, analyzer, mutator, selector, sandbox, cost tracker, router) before committing real budget.

**Outcome:** The machinery works. 9 mutants generated (3/3 valid per cycle). Pareto selection pruned correctly. But 20 samples is too small — baselines scored 95-100%, leaving no room for the system to demonstrate improvement.

**Key lesson:** Sample size must be large enough that baselines don't hit the ceiling. 200+ samples needed for GSM8K.

---

### Test #002: GSM8K Full Run (200 Samples)

| Field | Value |
|-------|-------|
| **Date** | 2026-02-28 |
| **Command** | `python run.py --benchmark gsm8k --samples 200 --cycles 10 --budget 5.0` |
| **Samples** | 200 GSM8K problems (seed=42, fixed) |
| **Cycles completed** | 5 of 10 (plus partial cycle 6 re-evaluation) |
| **Cost** | ~$2.94 total ($1.64 cycles 1-5 + ~$1.30 cycle 6 re-eval) |
| **Wall time** | ~8.3 hours (cycles 1-5) + ~4 hours (cycle 6) |
| **Result** | Best accuracy improved from 95.0% (baseline) to 97.0% (generated). RSI demonstrated. |
| **Status** | **INCOMPLETE** — stopped during cycle 6. See [section 2](#2-test-002-is-incomplete--what-happened). |
| **Report** | `docs/test_report_002_gsm8k_full.md` |
| **Data** | `results/gsm8k_20260228_075055/` (cycles.jsonl, eval_log.jsonl) |
| **Backup** | `backups/gsm8k_test002_cycles1to5_backup/` (eval_log.jsonl for cycles 1-5) |

**Purpose:** Run a statistically meaningful RSI test. With 200 samples, baselines don't hit 100%, creating headroom for the system to demonstrate improvement across cycles.

**Outcome (cycles 1-5):**

```
Accuracy
  97%  │                                          ●  cycle 5 (v3)
  96%  │                               ●  cycle 4 (v2)
  95.5%│     ●─────────●─────────●  cycles 1-3 (plateau)
  95%  │  ●  baseline (chain_of_thought)
  94%  │  ○  baseline (direct)
       └──────────────────────────────────────────
              1     2     3     4     5     Cycle
```

- **15 mutants generated** (3/3 valid per cycle, 100% validity rate)
- **Best score improved:** 95.0% → 95.5% → 95.5% → 95.5% → 96.0% → 97.0%
- **Winning lineage:** `chain_of_thought` → `enhanced_chain_of_thought` → `v2` → `v3`
- **Library grew:** 3 → 3 → 3 → 4 → 5 programs on the Pareto frontier
- **All improvements were prompt engineering** — single-call architecture, no architectural innovation

---

## 2. Test #002 Is Incomplete — What Happened

### 2.1 The Sequence of Events

```
Timeline:
─────────────────────────────────────────────────────────────
Phase 1: Initial run (cycles 1-5)
─────────────────────────────────────────────────────────────
  python run.py --benchmark gsm8k --samples 200 --cycles 10 --budget 5.0

  Cycle 1: 10,429s  │ Eval 8 programs × 200 tasks. Best: 95.5%
  Cycle 2:  4,202s  │ Eval 3 mutants (cache rest). Best: 95.5% (plateau)
  Cycle 3:  4,730s  │ Eval 3 mutants. Best: 95.5% (plateau, 2 cycles)
  Cycle 4:  6,830s  │ Eval 3 mutants. Best: 96.0% (breakthrough!)
  Cycle 5:  3,634s  │ Eval 3 mutants. Best: 97.0% (new best!)

  ──── STOPPED after cycle 5 ────
  Reason: Plateau patience = 3. After cycle 5 broke through,
  the loop had reset its plateau counter. But the run was
  stopped here (either manually or the process was interrupted)
  before cycle 6 could begin normally.
  Total spend: $1.64 of $5.00 budget.

─────────────────────────────────────────────────────────────
Phase 2: Resumed run (cycle 6 re-evaluation)
─────────────────────────────────────────────────────────────
  python run.py --resume results/gsm8k_20260228_075055 \
      --cycles 15 --budget 5.0

  What happened:
  - The resume code loaded ALL 36 generated .py files from disk
    (not just the 5 Pareto survivors from cycle 5)
  - Plus the `direct` baseline → 37 programs total
  - Cycle 6 began evaluating all 37 programs × 200 tasks
  - This produced 3,541 eval records (vs. normal ~600)
  - Cost: ~$1.07 for cycle 6 alone

  ──── STOPPED during cycle 6 ────
  The evaluation of all 37 programs was not fully completed.
  Evidence: `advanced_chain_of_thought_v1` has only 5 eval
  records (vs. expected 200). The run was interrupted —
  either manually stopped, or the budget was exhausted
  ($1.64 prior + $1.07 cycle 6 = $2.71, still under $5.00,
  so likely a manual stop or process crash).

  Total eval records: 7,541 (4,205 from cycles 1-5 + 3,336 from cycle 6)
```

### 2.2 Why Cycle 6 Was Different

The `--resume` code path in `run.py` (lines 122-138) loads programs differently than a normal cycle:

```python
# run.py, lines 122-138 — Resume mode
for py_file in sorted(GENERATED_DIR.glob("*.py")):
    prog = load_program(py_file)
    if prog is not None:
        loop.add_program(prog, source_path=py_file)
# Always include direct baseline
loop.add_program(DirectProgram())
```

This loads **every `.py` file** in `src/programs/generated/`, including programs that were Pareto-eliminated in earlier cycles. In a normal cycle, only 5 programs (the Pareto survivors) plus 3 new mutants would be evaluated. In the resumed cycle 6, 37 programs were loaded — a **7x increase** in evaluation work.

### 2.3 The Name Collision Problem

Because `load_program()` loads every file from disk, and many files share the same runtime `name` (e.g., 8 files all named `"enhanced_chain_of_thought"`), the eval cache keyed by `id(program)` correctly distinguishes them but the `eval_log.jsonl` records only `program.name`. This makes it impossible to trace which specific file produced a given cycle 6 eval record.

### 2.4 What the Cycle 6 Data Tells Us

Despite being incomplete, cycle 6 produced useful cross-validation data:

| Program | Cycle 6 Evals | Accuracy | Notes |
|---------|--------------|----------|-------|
| `restate_and_solve` | 200 | **96.0%** | Best reliable performer in cycle 6 |
| `improved_chain_of_thought` | 336 | **95.2%** | Strong newcomer |
| `decomposition_verification` | 200 | **94.5%** | Multi-call, most expensive |
| `chain_of_thought` | 1,200 | **94.1%** | 6 evals per task (consistency check) |
| `enhanced_chain_of_thought_v4` | 200 | **89.0%** | Underperformed |
| `enhanced_chain_of_thought` | 800 | **53.0%** | Degraded (some instances buggy) |
| `improved_reasoning_with_verification` | 200 | **26.5%** | Consistently broken |
| `advanced_chain_of_thought_v1` | 5 | **100.0%** | Only 5/200 evals — meaningless |

### 2.5 What Was Lost

The incomplete cycle 6 means:

1. **`advanced_chain_of_thought_v1` was never fully evaluated.** Its 100% on 5 samples is statistically meaningless. This program has the most sophisticated prompt in the collection (restatement + validation + strategy + examples), and could potentially be the new best.

2. **No mutation happened in cycle 6.** The cycle never reached step 3 (mutation), so no new programs were generated from the cycle 5 winners.

3. **No Pareto selection ran in cycle 6.** The library was never pruned after the mass re-evaluation, so we don't know which programs would have survived.

4. **Cycles 7-10 never ran.** The original run was configured for 10 cycles. Only 5 completed normally.

---

## 3. Completing Test #002

### Option A: Continue the Resumed Run (Recommended)

```bash
python run.py --resume results/gsm8k_20260228_075055 \
    --samples 200 --cycles 15 --budget 10.0
```

**What this does:**
- Starts from cycle 7 (cycle 6 already logged)
- Loads all programs from `generated/`
- Evaluates everything fresh (cache is empty after restart)
- Runs cycles 7-15 normally (eval → analyze → mutate → select)
- Higher budget ($10) gives room for 8+ more cycles

**Cost estimate:** $3-5 for cycles 7-15 (see [section 6](#6-cost-and-time-reference))
**Time estimate:** 6-12 hours

**What to watch for:**
- Does the best score break above 97.0%?
- Does the system plateau at 97%? (would confirm the cheap-model ceiling)
- Do any mutants discover architectural innovations (multi-call, strong-tier)?

### Option B: Clean Restart with Curated Library

Manually select the best programs and start a clean run:

```bash
# 1. Delete all generated programs except the proven winners
# 2. Keep: enhanced_chain_of_thought_v2_mut_3.py (v3, 97%)
#          enhanced_chain_of_thought_mut_8.py (v2, 96%)
#          chain_of_thought_mut_11.py (restate_and_solve, 96%)
#          chain_of_thought_mut_21.py (advanced_v1, untested)
# 3. Run fresh with curated seed
python run.py --resume results/gsm8k_20260228_075055 \
    --samples 200 --cycles 15 --budget 10.0
```

**Pros:** Avoids re-evaluating 30+ redundant programs. Focuses mutation budget on the best candidates.
**Cons:** Requires manual curation. Loses the ability to discover sleeper programs.

### Option C: Full Re-evaluation First

Run a dedicated cycle to evaluate `advanced_chain_of_thought_v1` fully (200 samples), then decide:

```bash
# Quick targeted evaluation (modify run.py or write a script)
# Evaluate just advanced_chain_of_thought_v1 on 200 problems
# Cost: ~$0.05-0.10
# Time: ~20-30 minutes
```

If `advanced_chain_of_thought_v1` scores >97%, it becomes the new parent for mutation. If not, continue with `enhanced_chain_of_thought_v3` as the parent.

---

## 4. Next Tests: ARC-AGI

### 4.1 What Is ARC-AGI?

ARC-AGI (Abstraction and Reasoning Corpus) is a visual reasoning benchmark:

```
Training Example:                    Test:
  Input:     Output:                 Input:     Output: ???
  0 0 0      0 0 0                   0 0 0 0
  0 1 0  →   1 1 1                   0 1 1 0    → predict this
  0 0 0      0 0 0                   0 0 0 0
```

Each task provides 2-5 training (input, output) pairs. The program must discover the transformation rule and apply it to the test input. Evaluation is **exact grid match** — dimensions and every cell value must be correct.

**Why ARC-AGI matters:**
- LLMs famously struggle with ARC (state-of-the-art is ~30-40% on the full test set)
- Cheap models will score very low (likely 5-15%), giving massive headroom for improvement
- The task requires genuine reasoning, not just math — pattern recognition, spatial reasoning, abstraction
- Novel strategies (grid analysis, symmetry detection, template matching) could emerge

### 4.2 How the ARC Evaluator Works

**File:** `src/evaluator/arc.py`

```
Data pipeline:
  1. Download ARC-AGI dataset from GitHub (400 training tasks)
  2. Cache locally in data/arc/
  3. Sample N tasks (seed=42 for reproducibility)
  4. Format each task as a few-shot prompt with grid examples
  5. Evaluate: extract grid from LLM output, exact match comparison
```

The evaluator formats tasks as natural-language prompts:

```
You are solving an ARC (Abstraction and Reasoning Corpus) task.
Each task shows input/output grid pairs as demonstrations.
Your job is to figure out the transformation rule and apply it
to the test input.

--- Training Example 1 ---
Input:
0 1 2
3 4 5
Output:
5 4 3
2 1 0

--- Test Input ---
0 0 1
1 0 0

Respond with ONLY the output grid, one row per line,
values space-separated.
```

Answer checking uses a two-stage parser:
1. Try JSON parsing (`[[1,2],[3,4]]`)
2. Fall back to plain-text parsing (`1 2\n3 4`)
3. Exact grid comparison (dimensions + all cell values)

### 4.3 Planned ARC Tests

#### Test #003: ARC-AGI Smoke Test

| Field | Value |
|-------|-------|
| **Command** | `python run.py --benchmark arc --samples 30 --cycles 3 --budget 5.0` |
| **Samples** | 30 ARC tasks |
| **Cycles** | 3 |
| **Estimated cost** | $0.50-1.50 |
| **Estimated time** | 30-60 minutes |
| **Purpose** | Verify ARC evaluator works end-to-end. Establish baseline accuracy. |

**What to look for:**
- Do baselines score above 0%? (Expected: 3-15% with cheap models)
- Does the grid parser correctly extract predictions?
- Does the mutator generate meaningful ARC-specific strategies?
- Are prompts too long for cheap models? (ARC prompts with 4-5 training examples can be 500+ tokens)

**Expected baseline performance on ARC (cheap models):**

| Program | Expected Accuracy | Reasoning |
|---------|------------------|-----------|
| `direct` | 0-5% | No strategy for visual reasoning |
| `chain_of_thought` | 5-10% | CoT may help on simple patterns |
| `decompose_solve` | 3-8% | Decomposition may help identify sub-patterns |
| `generate_verify` | 2-7% | Verification unlikely to help with wrong answers |
| `ensemble_vote` | 3-10% | Multiple attempts may catch different patterns |

ARC is dramatically harder than GSM8K. The baselines that scored 73-95% on math will likely score single digits on visual reasoning.

#### Test #004: ARC-AGI Full Run

| Field | Value |
|-------|-------|
| **Command** | `python run.py --benchmark arc --samples 100 --cycles 15 --budget 25.0` |
| **Samples** | 100 ARC tasks |
| **Cycles** | 15 |
| **Estimated cost** | $8-20 |
| **Estimated time** | 12-24 hours |
| **Purpose** | The real RSI test on a hard benchmark. |

**What to watch for:**
- **Novel strategy discovery:** Does the mutator invent grid-specific reasoning steps? (e.g., analyze symmetry, count colors, detect patterns before solving)
- **Architectural innovation:** ARC may force the system to generate multi-call programs that actually help (unlike GSM8K where single-call dominated)
- **Sustained improvement:** With baselines at 5-10%, there's 90% headroom. Can the system reach 20%? 30%?
- **Strong-tier exploration:** The mutator may generate programs that use `router.route("strong")` for ARC, since cheap models will fail badly

#### Test #005: ARC-AGI Extended

| Field | Value |
|-------|-------|
| **Command** | `python run.py --benchmark arc --samples 200 --cycles 25 --budget 50.0` |
| **Samples** | 200 ARC tasks |
| **Cycles** | 25 |
| **Estimated cost** | $20-45 |
| **Estimated time** | 24-48 hours |
| **Purpose** | Deep exploration of ARC. Maximum cycles to test if the system can discover non-obvious strategies. |

---

## 5. Beyond: Advanced Experiments

### 5.1 Multi-Parent Mutation

**Problem:** Currently only the single best-scoring program is mutated each cycle. This limits diversity.

**Experiment:** Modify `loop.py` to mutate the top 3 programs (1 candidate each instead of 3 from one parent).

**Cost impact:** Neutral (still 3 mutations per cycle).
**Expected benefit:** Broader search. Programs with different strengths (cheap + accurate, multi-call + robust) could cross-pollinate.

### 5.2 Strong-Tier Program Evaluation

**Problem:** All programs use `router.route("cheap")`. The cheap-model ceiling appears to be ~97% on GSM8K.

**Experiment:** Add a baseline that uses `router.route("strong")` and see if it breaks through.

```bash
# Estimated cost: $2-5 for one strong-tier program × 200 tasks
# Strong models cost ~10-50x more per call
```

**Expected result:** Strong models (GPT-4o, Claude Sonnet, Gemini Pro) should score 98-100% on GSM8K, confirming the ceiling is a model capability issue, not a strategy issue.

### 5.3 Crossover Mutation

**Problem:** Each mutation starts from a single parent. No genetic crossover.

**Experiment:** Implement a third mutation strategy that combines two parent programs:

```
Strategy: CROSSOVER
"Here are two parent programs. Parent A excels at preventing
arithmetic errors. Parent B excels at avoiding misread problems.
Combine their best ideas into a single improved program."
```

**Cost impact:** Same (1 strong LLM call per mutation).
**Expected benefit:** Combine complementary strategies that emerged in different lineages.

### 5.4 Programmatic Self-Modification

**Problem:** All winning mutations were prompt changes. No program has added meaningful Python logic.

**Experiment:** Add an explicit mutation instruction:

```
"You MUST add at least one new Python function (not just prompt
changes) that implements programmatic verification, answer
parsing, or reasoning logic."
```

**Expected outcome:** Programs that add `eval()`-based arithmetic checking, regex answer extraction, or grid analysis for ARC.

### 5.5 Multi-Benchmark Co-Evolution

**Problem:** Programs are optimized for one benchmark. A program good at GSM8K may fail on ARC.

**Experiment:** Run the RSI loop with both benchmarks simultaneously:

```
Evaluate each program on: 100 GSM8K + 50 ARC tasks
Fitness = weighted average: 0.5 * gsm8k_score + 0.5 * arc_score
```

**Cost impact:** ~2x per cycle (two benchmark evaluations).
**Expected benefit:** Programs that generalize across task types. Tests whether RSI can discover universal reasoning strategies.

### 5.6 Head-to-Head: RSI vs. Manual Prompt Engineering

**Experiment:** Have a human expert spend 2 hours manually writing the best possible GSM8K program. Compare against the RSI-generated winner.

**Purpose:** Quantify whether automated RSI can match or exceed careful human engineering.

---

## 6. Cost and Time Reference

### 6.1 Cost Model

Costs are driven by two factors: **evaluation calls** (cheap tier) and **meta-system calls** (strong tier).

**Per-evaluation cost (cheap tier):**

| Model | Input ($/1M tokens) | Output ($/1M tokens) | Avg cost/GSM8K eval | Avg cost/ARC eval |
|-------|---------------------|----------------------|--------------------|-------------------|
| `gpt-4o-mini` | $0.15 | $0.60 | ~$0.00020 | ~$0.00050 |
| `gemini-2.5-flash` | $0.15 | $0.60 | ~$0.00020 | ~$0.00050 |
| `claude-haiku-4-5` | $0.80 | $4.00 | ~$0.00080 | ~$0.00200 |

ARC evaluations cost ~2.5x more than GSM8K due to longer prompts (few-shot grid examples).

**Per-mutation cost (strong tier):**

| Model | Input ($/1M tokens) | Output ($/1M tokens) | Avg cost/mutation |
|-------|---------------------|----------------------|-------------------|
| `gpt-4o` | $2.50 | $10.00 | ~$0.03-0.05 |
| `gemini-2.5-pro` | $1.25 | $10.00 | ~$0.02-0.04 |
| `claude-sonnet-4-6` | $3.00 | $15.00 | ~$0.04-0.06 |

**Per-analysis cost (strong tier):** ~$0.02-0.05 per cycle (1 strong LLM call).

### 6.2 Per-Cycle Cost Formula

```
Cycle 1 (all programs evaluated fresh):
  eval_cost = (n_baselines + n_mutants) × n_samples × avg_cost_per_eval
  meta_cost = 1 × analysis_cost + n_mutants × mutation_cost
  total = eval_cost + meta_cost

Cycle N>1 (cached programs skip evaluation):
  eval_cost = n_mutants × n_samples × avg_cost_per_eval
  meta_cost = 1 × analysis_cost + n_mutants × mutation_cost
  total = eval_cost + meta_cost
```

### 6.3 Projected Costs

**GSM8K (avg $0.00030/eval with gpt-4o-mini + gemini-2.5-flash):**

| Scenario | Samples | Cycles | Cycle 1 Cost | Per-Cycle (2+) | Total Cost | Wall Time |
|----------|---------|--------|-------------|----------------|------------|-----------|
| Smoke test | 20 | 3 | $0.07 | $0.05 | **$0.17** | 30 min |
| Standard | 200 | 10 | $0.50 | $0.35 | **$3.65** | 10-15 hrs |
| Extended | 200 | 20 | $0.50 | $0.35 | **$7.15** | 20-30 hrs |
| Exhaustive | 500 | 25 | $1.25 | $0.65 | **$16.85** | 48-72 hrs |

**ARC-AGI (avg $0.00075/eval due to longer prompts):**

| Scenario | Samples | Cycles | Cycle 1 Cost | Per-Cycle (2+) | Total Cost | Wall Time |
|----------|---------|--------|-------------|----------------|------------|-----------|
| Smoke test | 30 | 3 | $0.25 | $0.20 | **$0.65** | 30-60 min |
| Standard | 100 | 15 | $0.80 | $0.40 | **$6.40** | 12-24 hrs |
| Extended | 200 | 25 | $1.55 | $0.65 | **$17.15** | 24-48 hrs |

**Notes:**
- Wall time assumes sequential evaluation (current implementation). Parallel evaluation would reduce time by 3-5x.
- ARC evaluations are slower per-task because prompts are longer (more tokens to generate and parse).
- Strong-tier costs (mutation, analysis) are ~15-25% of total — most cost goes to cheap-tier evaluation.
- Budget headroom recommended: set budget to 2x estimated cost to avoid mid-run interruption.

### 6.4 Cost Breakdown from Actual Test #002

From the completed 5-cycle run ($1.64 total):

```
Evaluation (cheap tier):    ~$1.19  (73% of total)
├── gpt-4o-mini:             $0.56
└── gemini-2.5-flash:        $0.63

Meta-system (strong tier):  ~$0.45  (27% of total)
├── Failure analysis:        ~$0.15  (5 cycles × 1 call)
└── Program mutation:        ~$0.30  (5 cycles × 3 calls)
```

### 6.5 Time Per Cycle (from Test #002)

| Cycle | Duration | Programs Evaluated | Notes |
|-------|----------|-------------------|-------|
| 1 | 2h 54m | 8 (5 baselines + 3 mutants) | Longest — all fresh evaluations |
| 2 | 1h 10m | 3 (mutants only, 5 cached) | Normal cycle |
| 3 | 1h 19m | 3 | Normal cycle |
| 4 | 1h 54m | 3 | Longer — multi-call mutants are slower |
| 5 | 1h 01m | 3 | Normal cycle |

**Average cycle time (after cycle 1):** ~1h 20m for 200 samples.

Sequential evaluation is the bottleneck. Each of 200 tasks is evaluated one-at-a-time with one LLM call. With `asyncio.gather()` parallelism, cycles could complete in 10-20 minutes.

---

## 7. Model Inventory

These are the models configured in the system and used across all tests:

### 7.1 Cheap Tier (Used for Program Evaluation)

| Model | Provider | Input $/1M | Output $/1M | Notes |
|-------|----------|-----------|------------|-------|
| `gpt-4o-mini` | OpenAI | $0.15 | $0.60 | Most frequently selected in test #002 |
| `gemini-2.5-flash` | Google | $0.15 | $0.60 | Roughly equal usage to gpt-4o-mini |
| `claude-haiku-4-5-20251001` | Anthropic | $0.80 | $4.00 | Available but never selected (more expensive) |

The router selects between cheap-tier models using weighted random choice (default: equal weights). In practice, `gpt-4o-mini` and `gemini-2.5-flash` split calls roughly 50/50 because they have identical pricing. `claude-haiku` was never selected because its higher price gives it a lower implicit weight when all success rates are equal.

**Actual model usage from test #002 (7,541 evals):**

| Model | Calls | Percentage | Total Cost |
|-------|-------|-----------|------------|
| `gpt-4o-mini` | 4,777 | 51.2% | $1.04 |
| `gemini-2.5-flash` | 4,550 | 48.8% | $1.15 |
| `claude-haiku-4-5` | 0 | 0% | $0.00 |

### 7.2 Strong Tier (Used for Analysis + Mutation)

| Model | Provider | Input $/1M | Output $/1M | Notes |
|-------|----------|-----------|------------|-------|
| `gpt-4o` | OpenAI | $2.50 | $10.00 | Used for failure analysis and program generation |
| `gemini-2.5-pro` | Google | $1.25 | $10.00 | Used for failure analysis and program generation |
| `claude-sonnet-4-6` | Anthropic | $3.00 | $15.00 | Available but usage depends on router selection |

Strong-tier models are called:
- **Once per cycle** by the failure analyzer (temperature=0.0, deterministic)
- **Three times per cycle** by the program mutator (temperature=0.7, creative)

### 7.3 Model Availability

| Provider | API Key Required | Status in Test #002 |
|----------|-----------------|-------------------|
| OpenAI | `OPENAI_API_KEY` | Active (system env) |
| Google | `GOOGLE_API_KEY` | Active (.env file) |
| Anthropic | `ANTHROPIC_API_KEY` | Active (.env file) |

All three providers were available. Missing keys are handled gracefully — the provider is marked `available=False` and the router skips it.

---

## Summary: Recommended Next Steps

```
Priority   Test                     Cost    Time      Purpose
────────   ─────────────────────    ─────   ────────  ──────────────────────
   1       Complete test #002       $3-5    6-12 hrs  Confirm 97% ceiling
           (Option A: continue                        or break through
            from cycle 7)

   2       ARC-AGI smoke test       $0.65   30-60 min Verify ARC pipeline
           (#003: 30 samples,                         works end-to-end
            3 cycles)

   3       ARC-AGI full run         $6-14   12-24 hrs Real RSI test on
           (#004: 100 samples,                        a hard benchmark
            15 cycles)

   4       ARC-AGI extended         $17-35  24-48 hrs Deep exploration,
           (#005: 200 samples,                        maximum cycles
            25 cycles)

   5       Strong-tier GSM8K        $2-5    2-4 hrs   Confirm ceiling is
           (200 samples, 1 eval                       model capability,
            with strong models)                       not strategy
```

Total budget for the full test suite: **$30-60** across all experiments.
