"""Pareto selector: keeps the Pareto-optimal programs on the score-cost frontier."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from src.programs.interface import ReasoningProgram

logger = logging.getLogger(__name__)


@dataclass
class ProgramStats:
    """Aggregated performance statistics for a single reasoning program."""

    program_id: int | str
    program: ReasoningProgram
    source_path: Path | None  # None for baseline programs
    avg_score: float          # 0.0 – 1.0
    avg_cost: float           # USD per problem
    n_evaluated: int


def _is_dominated(candidate: ProgramStats, others: list[ProgramStats]) -> bool:
    """
    Return True if *candidate* is Pareto-dominated by any program in *others*.

    A program A dominates program B when:
      - A.avg_score >= B.avg_score  AND  A.avg_cost <= B.avg_cost
      - At least one of those inequalities is strict.

    The candidate is NOT compared against itself.
    """
    for other in others:
        if other.program_id == candidate.program_id:
            continue
        score_at_least_as_good = other.avg_score >= candidate.avg_score
        cost_at_least_as_good = other.avg_cost <= candidate.avg_cost
        strictly_better_somewhere = (
            other.avg_score > candidate.avg_score
            or other.avg_cost < candidate.avg_cost
        )
        if score_at_least_as_good and cost_at_least_as_good and strictly_better_somewhere:
            return True
    return False


class ParetoSelector:
    """Select Pareto-optimal programs on the score-cost frontier."""

    def select(
        self,
        candidates: list[ProgramStats],
        max_library_size: int = 10,
    ) -> list[ProgramStats]:
        """
        Filter to Pareto-optimal programs.

        A program is Pareto-dominated if another program has BOTH:
        - Higher or equal avg_score
        - Lower or equal avg_cost
        (with at least one strictly better in at least one dimension)

        Always keep:
        - All non-dominated programs
        - The single highest-scoring program (even if dominated)

        If more than max_library_size programs remain after the above rules,
        keep the top ones by avg_score.

        Parameters
        ----------
        candidates:
            All ProgramStats to consider.
        max_library_size:
            Maximum number of programs to return.

        Returns
        -------
        Pruned list of ProgramStats, sorted by avg_score descending.
        """
        if not candidates:
            logger.info("ParetoSelector: no candidates provided.")
            return []

        logger.info(
            "ParetoSelector: evaluating %d candidates (max_library_size=%d).",
            len(candidates),
            max_library_size,
        )

        # --- Step 1: identify non-dominated programs ---
        non_dominated = [
            prog for prog in candidates
            if not _is_dominated(prog, candidates)
        ]
        logger.info(
            "ParetoSelector: %d non-dominated programs on the Pareto frontier.",
            len(non_dominated),
        )

        # --- Step 2: always include the highest-scoring program ---
        best_by_score = max(candidates, key=lambda p: p.avg_score)

        # Build the keep set, preserving insertion order
        kept_ids: set[int | str] = set()
        kept: list[ProgramStats] = []

        # Add non-dominated programs first
        for prog in non_dominated:
            if prog.program_id not in kept_ids:
                kept_ids.add(prog.program_id)
                kept.append(prog)

        # Ensure the best-scoring program is always included
        if best_by_score.program_id not in kept_ids:
            logger.info(
                "ParetoSelector: adding highest-scoring program '%s' "
                "(score=%.4f) even though it is dominated.",
                best_by_score.program.name,
                best_by_score.avg_score,
            )
            kept_ids.add(best_by_score.program_id)
            kept.append(best_by_score)

        # --- Step 3: trim to max_library_size by score ---
        if len(kept) > max_library_size:
            logger.info(
                "ParetoSelector: trimming from %d to %d programs by avg_score.",
                len(kept),
                max_library_size,
            )
            kept = sorted(kept, key=lambda p: p.avg_score, reverse=True)
            kept = kept[:max_library_size]
        else:
            kept = sorted(kept, key=lambda p: p.avg_score, reverse=True)

        logger.info(
            "ParetoSelector: keeping %d programs. "
            "Scores: %s",
            len(kept),
            [f"{p.program.name}={p.avg_score:.4f}" for p in kept],
        )
        return kept
