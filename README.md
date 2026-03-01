# Recursive Self-Improvement (RSI) — Proof of Concept

A program-genetic system that generates executable reasoning programs, evaluates them against LLM benchmarks, and optimizes through mutation and Pareto selection. **Programs** (not prompts) are the unit of optimization.

## Inspiration

This project was inspired by the YouTube video **["The Powerful Alternative To Fine-Tuning"](https://www.youtube.com/watch?v=UPGB-hsAoVY&t=4s)**, which presents the idea that a meta-system can generate reasoning strategies written in code — not just better prompts — to dramatically improve LLM performance on hard benchmarks without fine-tuning.

## How It Works

The system runs a 5-step optimization cycle:

```
evaluate → analyze failures → mutate → evaluate mutants → Pareto select
```

1. **Evaluate** — Run reasoning programs against a benchmark (GSM8K math, ARC-AGI visual reasoning)
2. **Analyze** — Cluster failures into pattern classes (arithmetic errors, misread problems, etc.)
3. **Mutate** — Generate new program variants via LLM-driven code mutation
4. **Evaluate mutants** — Score the new programs on the same benchmark
5. **Select** — Keep Pareto-optimal programs on the (accuracy, cost) frontier

Each cycle produces new Python modules that define how to decompose, route, and verify LLM calls. The "recursion" is that the meta-system reads its own previous best programs as building blocks for generating better ones.

## Results

On GSM8K (200 samples, 5 cycles, ~$2.94 total cost):

| Cycle | Best Score | Best Program | Cost/Problem |
|-------|-----------|-------------|-------------|
| 1 | 95.5% | enhanced_chain_of_thought | $0.0015 |
| 2 | 95.5% | enhanced_chain_of_thought | $0.0015 |
| 3 | 95.5% | enhanced_chain_of_thought | $0.0015 |
| 4 | 96.0% | enhanced_chain_of_thought_v2 | $0.0015 |
| 5 | 97.0% | enhanced_chain_of_thought_v3 | $0.0015 |

Baseline chain-of-thought started at 95.0%. The system autonomously improved to 97.0% through 3 generations of program mutation, generating 15 valid mutant programs with a 100% syntax validity rate.

## Quick Start

```bash
# Install
pip install -e ".[dev]"

# Set up API keys in .env (see .env.example)
# Requires at least one of: OPENAI_API_KEY, ANTHROPIC_API_KEY, GOOGLE_API_KEY

# Run RSI loop on GSM8K
python run.py --benchmark gsm8k --samples 20 --cycles 3 --budget 5.0

# Run on ARC-AGI
python run.py --benchmark arc --samples 50 --cycles 10 --budget 25.0
```

## Project Structure

```
src/
  meta/           # RSI loop, analyzer, mutator, selector
  models/         # LLM providers (OpenAI, Anthropic, Google) + cost-aware router
  programs/       # ReasoningProgram base + 5 baselines + generated/
  evaluator/      # Frozen eval layer (GSM8K, ARC-AGI)
  utils/          # Cost tracker, sandbox (dynamic program loading)
docs/             # Test reports, architecture docs, testing roadmap
```

## Documentation

- **[RSI Architecture](docs/rsi_architecture.md)** — Detailed system design with ASCII diagrams and code references
- **[Test Report: GSM8K Full](docs/test_report_002_gsm8k_full.md)** — Evidence-backed analysis of 7 RSI claims
- **[Testing Roadmap](docs/testing_roadmap.md)** — Completed tests, future plans, cost estimates
- **[Approaches Analysis](docs/rsi_approaches_analysis.md)** — Comparative analysis of RSI implementation strategies

## Models

The system supports 3 providers with cost-aware routing:

| Provider | Cheap Tier | Strong Tier |
|----------|-----------|------------|
| OpenAI | gpt-4o-mini | gpt-4o |
| Anthropic | claude-haiku-4-5 | claude-sonnet-4-6 |
| Google | gemini-2.5-flash | gemini-2.5-pro |

## License

MIT
