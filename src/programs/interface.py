"""Base interface for all reasoning programs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from src.models.base import ModelRoster, LLMProvider

if TYPE_CHECKING:
    from src.models.router import ModelRouter


@dataclass
class Solution:
    """The result returned by any ReasoningProgram."""

    answer: str   # The final answer string
    cost: float   # Total API cost in USD for this solution
    trace: str    # Log of all LLM calls made during solving


class ReasoningProgram:
    """
    Abstract base class for all reasoning programs.

    Each concrete subclass implements a distinct strategy for solving
    benchmark tasks (GSM8K math problems, ARC reasoning tasks, etc.).

    Subclasses must set `name` and `description` as class attributes and
    override `solve`.
    """

    name: str
    description: str

    async def solve(
        self,
        problem: dict,
        models: ModelRoster,
        router: ModelRouter,
    ) -> Solution:
        """Solve a benchmark task.

        Parameters
        ----------
        problem:
            A dict with at least:
            - "question": str — the problem text
            - "context": dict | None — optional additional context
              (e.g., ARC train example pairs)
        models:
            The full roster of available models.
        router:
            Cost-aware router that selects a (ModelConfig, LLMProvider)
            pair for a requested difficulty tier.

        Returns
        -------
        Solution
            The final answer, total cost, and a human-readable trace of
            every LLM call made.
        """
        raise NotImplementedError(
            f"{type(self).__name__} must implement solve()"
        )
