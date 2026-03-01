"""Simple budget / cost tracking utility."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class CostTracker:
    """
    Tracks cumulative LLM spending and enforces an optional budget cap.

    Usage::

        tracker = CostTracker(budget_usd=1.00)
        tracker.add(0.003)           # record a spend
        tracker.within_budget()      # True / False
        tracker.remaining_budget()   # USD remaining (None if no cap)
    """

    budget_usd: float | None = None  # None = no cap
    _total_spent: float = field(default=0.0, init=False)
    _per_model: dict[str, float] = field(default_factory=dict, init=False)

    def add(self, cost_usd: float, model_name: str = "unknown") -> None:
        """Record a spend amount against the running total."""
        self._total_spent += cost_usd
        self._per_model[model_name] = self._per_model.get(model_name, 0.0) + cost_usd
        logger.debug(
            "CostTracker: +$%.6f (model=%s) total=$%.4f",
            cost_usd,
            model_name,
            self._total_spent,
        )

    def within_budget(self) -> bool:
        """Return True if we are still within the configured budget (or no budget set)."""
        if self.budget_usd is None:
            return True
        return self._total_spent < self.budget_usd

    def remaining_budget(self) -> float | None:
        """Return USD remaining, or None if no budget cap was set."""
        if self.budget_usd is None:
            return None
        return max(0.0, self.budget_usd - self._total_spent)

    @property
    def total_spent(self) -> float:
        """Total USD spent so far."""
        return self._total_spent

    def per_model_breakdown(self) -> dict[str, float]:
        """Return a copy of the per-model spend dict."""
        return dict(self._per_model)

    def reset(self) -> None:
        """Reset all counters (useful between evaluation runs)."""
        self._total_spent = 0.0
        self._per_model.clear()

    def __repr__(self) -> str:
        budget_str = (
            f"/{self.budget_usd:.4f}" if self.budget_usd is not None else ""
        )
        return f"CostTracker(spent=${self._total_spent:.4f}{budget_str})"
