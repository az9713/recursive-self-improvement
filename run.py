"""RSI Proof-of-Concept — CLI entry point."""

import argparse
import asyncio
import logging
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv


def main():
    load_dotenv()

    parser = argparse.ArgumentParser(
        description="RSI PoC — Program-Genetic Self-Improvement"
    )
    parser.add_argument("--benchmark", choices=["gsm8k", "arc"], default="gsm8k")
    parser.add_argument("--samples", type=int, default=100, help="Number of benchmark tasks")
    parser.add_argument("--cycles", type=int, default=10, help="Max optimization cycles")
    parser.add_argument("--budget", type=float, default=25.0, help="Budget in USD")
    parser.add_argument("--mutants", type=int, default=3, help="Mutant programs per cycle")
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument(
        "--resume", type=str, default=None,
        help="Resume from a previous results directory (e.g. results/gsm8k_20260228_075055)",
    )
    args = parser.parse_args()

    # Setup logging
    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    # Determine results directory
    resume_dir = None
    start_cycle = 1
    initial_spend = 0.0
    if args.resume:
        resume_dir = Path(args.resume)
        if not resume_dir.exists():
            print(f"ERROR: Resume directory does not exist: {resume_dir}")
            sys.exit(1)
        results_dir = resume_dir
    else:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        results_dir = Path("results") / f"{args.benchmark}_{timestamp}"

    # Build model infrastructure
    from src.models import build_default_roster, ModelRouter
    from src.utils.cost_tracker import CostTracker

    cost_tracker = CostTracker(budget_usd=args.budget)
    roster = build_default_roster()
    router = ModelRouter(roster, cost_tracker)

    # Show available models
    available = roster.all_available()
    print(f"\nAvailable models: {[c.name for c, _ in available]}")
    if not available:
        print("ERROR: No LLM providers available. Check your .env file.")
        sys.exit(1)

    # Create evaluator
    if args.benchmark == "gsm8k":
        from src.evaluator.gsm8k import GSM8KEvaluator
        evaluator = GSM8KEvaluator()
    else:
        from src.evaluator.arc import ARCEvaluator
        evaluator = ARCEvaluator()

    # Override default sample size so the loop uses --samples
    original_load = evaluator.load_tasks
    evaluator.load_tasks = lambda n_samples=args.samples: original_load(n_samples)

    # Create and seed the RSI loop with baseline programs
    from src.meta.loop import RSILoop
    from src.programs.baselines import (
        DirectProgram,
        ChainOfThoughtProgram,
        DecomposeSolveProgram,
        GenerateVerifyProgram,
        EnsembleVoteProgram,
    )
    from src.utils.sandbox import load_program, GENERATED_DIR

    # Handle resume: read prior state and determine starting cycle
    if resume_dir:
        import json as _json
        cycles_path = resume_dir / "cycles.jsonl"
        if cycles_path.exists():
            cycle_entries = [_json.loads(l) for l in cycles_path.read_text().strip().splitlines()]
            if cycle_entries:
                last = cycle_entries[-1]
                start_cycle = last["cycle"] + 1
                initial_spend = last["total_spend"]
                print(f"\nResuming from cycle {start_cycle} (prior spend: ${initial_spend:.2f})")
            else:
                print("WARNING: cycles.jsonl is empty, starting from cycle 1")
        else:
            print("WARNING: No cycles.jsonl found, starting from cycle 1")

    # Set initial spend on cost tracker
    if initial_spend > 0:
        cost_tracker.add(initial_spend, model_name="_resume_prior_spend")

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

    if resume_dir:
        # Load generated programs that survived the Pareto frontier
        print("Loading generated programs...")
        loaded_names = set()
        for py_file in sorted(GENERATED_DIR.glob("*.py")):
            if py_file.name == "__init__.py":
                continue
            prog = load_program(py_file)
            if prog is not None:
                loop.add_program(prog, source_path=py_file)
                loaded_names.add(prog.name)
                print(f"  Loaded '{prog.name}' from {py_file.name}")
        # Always include direct baseline (cheapest on the frontier)
        direct = DirectProgram()
        loop.add_program(direct)
        print(f"  Loaded baseline 'direct'")
        print(f"Loaded {len(loaded_names) + 1} programs total")
    else:
        # Add all baselines for a fresh run
        baselines = [
            DirectProgram(),
            ChainOfThoughtProgram(),
            DecomposeSolveProgram(),
            GenerateVerifyProgram(),
            EnsembleVoteProgram(),
        ]
        for b in baselines:
            loop.add_program(b)

    print(
        f"\nStarting RSI loop: benchmark={args.benchmark}, samples={args.samples}, "
        f"cycles={args.cycles}, budget=${args.budget}"
    )
    print(f"Results will be saved to: {results_dir}\n")

    # Run the loop
    cycle_results = asyncio.run(loop.run())

    # Print final summary
    print("\n" + "=" * 60)
    print("FINAL RESULTS")
    print("=" * 60)

    if cycle_results:
        # Table of cycle results
        print(
            f"\n{'Cycle':>5} {'Best Score':>10} {'Best Program':>25} "
            f"{'Cost/Problem':>12} {'Spend':>8}"
        )
        print("-" * 65)
        for cr in cycle_results:
            print(
                f"{cr.cycle:>5} {cr.best_score*100:>9.1f}% {cr.best_program:>25} "
                f"${cr.best_cost:>10.4f} ${cr.total_spend:>7.2f}"
            )

        final = cycle_results[-1]
        print(
            f"\nBest program: '{final.best_program}' with {final.best_score*100:.1f}% accuracy"
        )
        print(f"Total cost: ${final.total_spend:.2f}")

    print(f"\nResults saved to: {results_dir}")


if __name__ == "__main__":
    main()
