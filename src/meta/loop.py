"""RSI loop orchestrator — the core optimization cycle."""

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

from src.evaluator.base import BenchmarkTask, EvalResult, Evaluator
from src.meta.analyzer import FailureAnalyzer
from src.meta.mutator import ProgramMutator
from src.meta.selector import ParetoSelector, ProgramStats
from src.models.base import ModelRoster
from src.models.router import ModelRouter
from src.programs.interface import ReasoningProgram, Solution
from src.utils.cost_tracker import CostTracker
from src.utils.sandbox import run_program_safe, load_program, GENERATED_DIR

logger = logging.getLogger(__name__)


@dataclass
class CycleResult:
    """Summary of one optimization cycle."""

    cycle: int
    library_size: int
    best_score: float
    best_program: str
    best_cost: float
    cheapest_program: str
    cheapest_cost: float
    cheapest_score: float
    total_spend: float
    n_mutants_generated: int
    n_mutants_valid: int
    duration_seconds: float


class RSILoop:
    """
    Program-Genetic RSI orchestrator.

    Runs the full optimization cycle:
    1. Evaluate all programs in library against benchmark sample
    2. Analyze failures across all programs
    3. Generate N mutant programs from top performers + failure analysis
    4. Evaluate mutants
    5. Pareto-select: merge mutants into library, prune dominated programs
    6. Log cycle stats
    7. Check budget + plateau
    """

    def __init__(
        self,
        evaluator: Evaluator,
        models: ModelRoster,
        router: ModelRouter,
        cost_tracker: CostTracker,
        max_cycles: int = 10,
        n_mutants_per_cycle: int = 3,
        plateau_patience: int = 3,
        results_dir: Path | None = None,
        start_cycle: int = 1,
    ):
        self.evaluator = evaluator
        self.models = models
        self.router = router
        self.cost_tracker = cost_tracker
        self.max_cycles = max_cycles
        self.n_mutants_per_cycle = n_mutants_per_cycle
        self.plateau_patience = plateau_patience
        self.results_dir = results_dir or Path("results")
        self.start_cycle = start_cycle

        self.analyzer = FailureAnalyzer()
        self.mutator = ProgramMutator()
        self.selector = ParetoSelector()

        # Program library: list of (program, source_path_or_None)
        self.library: list[tuple[ReasoningProgram, Path | None]] = []
        self.cycle_results: list[CycleResult] = []
        # Cache: id(program) -> list[EvalResult], avoids re-evaluating unchanged programs
        self._eval_cache: dict[int, list[EvalResult]] = {}
        # Per-problem telemetry log file handle (opened in run())
        self._eval_log_fh = None

    def add_program(self, program: ReasoningProgram, source_path: Path | None = None):
        """Add a program to the library (used to seed baselines)."""
        self.library.append((program, source_path))

    async def evaluate_program(
        self,
        program: ReasoningProgram,
        tasks: list[BenchmarkTask],
        cycle: int = 0,
    ) -> list[EvalResult]:
        """Evaluate a single program on a list of tasks."""
        results = []
        for task in tasks:
            problem = {
                "question": task.question,
                "context": task.context,
            }

            solution = await run_program_safe(
                program, problem, self.models, self.router
            )

            # Track the cost
            self.cost_tracker.add(solution.cost, model_name="program_eval")

            correct = self.evaluator.check_answer(solution.answer, task.expected_answer)

            result = EvalResult(
                program_id=program.name,
                task_id=task.task_id,
                correct=correct,
                score=1.0 if correct else 0.0,
                cost=solution.cost,
                trace=solution.trace,
            )
            results.append(result)

            self._log_eval_result(cycle, program.name, task, result, solution.answer)

        return results

    def _log_eval_result(
        self,
        cycle: int,
        program_name: str,
        task: BenchmarkTask,
        result: EvalResult,
        predicted: str,
    ):
        """Write one per-problem telemetry record to eval_log.jsonl."""
        if self._eval_log_fh is None:
            return
        record = {
            "cycle": cycle,
            "program": program_name,
            "task_id": task.task_id,
            "question": task.question,
            "expected": task.expected_answer,
            "predicted": predicted,
            "correct": result.correct,
            "score": result.score,
            "cost": result.cost,
            "trace": result.trace,
        }
        self._eval_log_fh.write(json.dumps(record) + "\n")
        self._eval_log_fh.flush()

    async def run(self) -> list[CycleResult]:
        """Run the full RSI optimization loop."""
        # Setup results directory
        self.results_dir.mkdir(parents=True, exist_ok=True)

        # Open per-problem telemetry log
        self._eval_log_fh = open(
            self.results_dir / "eval_log.jsonl", "a", encoding="utf-8"
        )

        # Load benchmark tasks
        logger.info("Loading %s benchmark tasks...", self.evaluator.name)
        tasks = self.evaluator.load_tasks()
        logger.info("Loaded %d tasks", len(tasks))

        best_score_ever = 0.0
        cycles_without_improvement = 0

        for cycle in range(self.start_cycle, self.max_cycles + 1):
            cycle_start = time.time()
            logger.info("=" * 60)
            logger.info("CYCLE %d / %d", cycle, self.max_cycles)
            logger.info("=" * 60)

            # 1. Evaluate all programs in library (with caching)
            logger.info("Evaluating %d programs...", len(self.library))
            all_results: dict[int, list[EvalResult]] = {}

            for program, _ in self.library:
                prog_id = id(program)
                if prog_id in self._eval_cache:
                    logger.info("  '%s' — using cached results", program.name)
                    all_results[prog_id] = self._eval_cache[prog_id]
                    n_correct = sum(1 for r in all_results[prog_id] if r.correct)
                    logger.info(
                        "    Score: %d/%d (%.1f%%)",
                        n_correct, len(all_results[prog_id]),
                        100 * n_correct / len(all_results[prog_id]),
                    )
                    continue

                logger.info("  Evaluating '%s'...", program.name)
                results = await self.evaluate_program(program, tasks, cycle=cycle)
                all_results[prog_id] = results
                self._eval_cache[prog_id] = results

                n_correct = sum(1 for r in results if r.correct)
                logger.info(
                    "    Score: %d/%d (%.1f%%)",
                    n_correct,
                    len(results),
                    100 * n_correct / len(results),
                )

            # 2. Analyze failures (across all program results)
            all_failures = [
                r for results_list in all_results.values() for r in results_list if not r.correct
            ]
            failure_classes, failure_summary = await self.analyzer.analyze(
                all_failures, self.models, self.router, cost_tracker=self.cost_tracker
            )
            logger.info(
                "Failure analysis: %s",
                {k: len(v) for k, v in failure_classes.items()},
            )

            # 3. Generate mutants from the best-performing program
            stats = self._compute_stats(all_results)
            stats_sorted = sorted(stats, key=lambda s: s.avg_score, reverse=True)

            best_stat = stats_sorted[0] if stats_sorted else None
            n_generated = 0
            n_valid = 0

            if best_stat and self.cost_tracker.within_budget():
                # Find the best program and its source
                best_prog, best_path = next(
                    (p, path) for p, path in self.library
                    if id(p) == best_stat.program_id
                )

                # Get source code
                if best_path and best_path.exists():
                    source = best_path.read_text(encoding="utf-8")
                else:
                    # For baselines, read the full module file (not just the class)
                    import inspect
                    source = Path(inspect.getfile(type(best_prog))).read_text(encoding="utf-8")

                logger.info(
                    "Mutating best program '%s' (score=%.1f%%)...",
                    best_prog.name,
                    best_stat.avg_score * 100,
                )

                valid_paths = await self.mutator.mutate(
                    parent_program=best_prog,
                    parent_source=source,
                    failure_summary=failure_summary,
                    models=self.models,
                    router=self.router,
                    n_candidates=self.n_mutants_per_cycle,
                    cost_tracker=self.cost_tracker,
                )

                n_generated = self.n_mutants_per_cycle
                n_valid = len(valid_paths)

                # 4. Load and evaluate new programs
                for path in valid_paths:
                    new_prog = load_program(path)
                    if new_prog is None:
                        logger.warning(
                            "Skipping mutant at '%s': load_program returned None.", path
                        )
                        continue

                    logger.info("  Evaluating mutant '%s'...", new_prog.name)
                    results = await self.evaluate_program(new_prog, tasks, cycle=cycle)
                    prog_id = id(new_prog)
                    all_results[prog_id] = results
                    self._eval_cache[prog_id] = results
                    self.library.append((new_prog, path))

                    n_correct = sum(1 for r in results if r.correct)
                    logger.info(
                        "    Score: %d/%d (%.1f%%)",
                        n_correct,
                        len(results),
                        100 * n_correct / len(results),
                    )

            # 5. Pareto-select
            stats = self._compute_stats(all_results)
            selected = self.selector.select(stats)
            selected_ids = {s.program_id for s in selected}

            # Prune library to only selected programs
            self.library = [
                (p, path) for p, path in self.library
                if id(p) in selected_ids
            ]

            # 6. Log cycle stats
            best = max(selected, key=lambda s: s.avg_score) if selected else None
            cheapest = min(selected, key=lambda s: s.avg_cost) if selected else None

            cycle_result = CycleResult(
                cycle=cycle,
                library_size=len(self.library),
                best_score=best.avg_score if best else 0.0,
                best_program=best.program.name if best else "none",
                best_cost=best.avg_cost if best else 0.0,
                cheapest_program=cheapest.program.name if cheapest else "none",
                cheapest_cost=cheapest.avg_cost if cheapest else 0.0,
                cheapest_score=cheapest.avg_score if cheapest else 0.0,
                total_spend=self.cost_tracker.total_spent,
                n_mutants_generated=n_generated,
                n_mutants_valid=n_valid,
                duration_seconds=time.time() - cycle_start,
            )
            self.cycle_results.append(cycle_result)

            self._log_cycle(cycle_result)
            self._save_cycle(cycle_result)

            # 7. Check budget
            if not self.cost_tracker.within_budget():
                logger.warning(
                    "Budget exhausted ($%.2f spent). Stopping.",
                    self.cost_tracker.total_spent,
                )
                break

            # 8. Check plateau
            if best and best.avg_score > best_score_ever:
                best_score_ever = best.avg_score
                cycles_without_improvement = 0
            else:
                cycles_without_improvement += 1

            if cycles_without_improvement >= self.plateau_patience:
                logger.info(
                    "No improvement for %d cycles. Stopping.", self.plateau_patience
                )
                break

        self._save_final_report()
        return self.cycle_results

    def _compute_stats(self, all_results: dict[int, list[EvalResult]]) -> list[ProgramStats]:
        """Compute ProgramStats for each program from evaluation results."""
        stats = []
        for program, path in self.library:
            prog_id = id(program)
            results = all_results.get(prog_id, [])
            if not results:
                continue
            avg_score = sum(r.score for r in results) / len(results)
            avg_cost = sum(r.cost for r in results) / len(results)
            stats.append(ProgramStats(
                program_id=prog_id,
                program=program,
                source_path=path,
                avg_score=avg_score,
                avg_cost=avg_cost,
                n_evaluated=len(results),
            ))
        return stats

    def _log_cycle(self, cr: CycleResult):
        """Print cycle summary to log."""
        logger.info("-" * 40)
        logger.info("Cycle %d Summary:", cr.cycle)
        logger.info("  Library size: %d", cr.library_size)
        logger.info(
            "  Best: '%s' (score=%.1f%%, cost=$%.4f/problem)",
            cr.best_program,
            cr.best_score * 100,
            cr.best_cost,
        )
        logger.info(
            "  Cheapest: '%s' (score=%.1f%%, cost=$%.4f/problem)",
            cr.cheapest_program,
            cr.cheapest_score * 100,
            cr.cheapest_cost,
        )
        logger.info(
            "  Mutants: %d generated, %d valid",
            cr.n_mutants_generated,
            cr.n_mutants_valid,
        )
        logger.info("  Total spend: $%.4f", cr.total_spend)
        logger.info("  Duration: %.1fs", cr.duration_seconds)

    def _save_cycle(self, cr: CycleResult):
        """Append cycle result to JSONL log."""
        log_path = self.results_dir / "cycles.jsonl"
        with open(log_path, "a") as f:
            f.write(json.dumps({
                "cycle": cr.cycle,
                "library_size": cr.library_size,
                "best_score": cr.best_score,
                "best_program": cr.best_program,
                "best_cost": cr.best_cost,
                "cheapest_program": cr.cheapest_program,
                "cheapest_cost": cr.cheapest_cost,
                "cheapest_score": cr.cheapest_score,
                "total_spend": cr.total_spend,
                "n_mutants_generated": cr.n_mutants_generated,
                "n_mutants_valid": cr.n_mutants_valid,
                "duration_seconds": cr.duration_seconds,
            }) + "\n")

    def _save_final_report(self):
        """Save a summary report and the cost log."""
        # Close telemetry log
        if self._eval_log_fh is not None:
            self._eval_log_fh.close()
            self._eval_log_fh = None

        # Save cost breakdown
        cost_path = self.results_dir / "cost_log.jsonl"
        with open(cost_path, "w") as f:
            breakdown = self.cost_tracker.per_model_breakdown()
            for model, cost in breakdown.items():
                f.write(json.dumps({"model": model, "total_cost": cost}) + "\n")

        # Save final library info
        library_path = self.results_dir / "final_library.json"
        library_info = []
        for program, path in self.library:
            library_info.append({
                "name": program.name,
                "description": program.description,
                "source_path": str(path) if path else None,
            })
        with open(library_path, "w") as f:
            json.dump(library_info, f, indent=2)

        logger.info("Results saved to %s", self.results_dir)
