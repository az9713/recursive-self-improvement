"""Cost-aware model router that selects the right LLM tier for each task."""

from __future__ import annotations

import logging
import random
from collections import defaultdict
from typing import Literal

from src.models.base import LLMProvider, ModelConfig, ModelRoster
from src.utils.cost_tracker import CostTracker

logger = logging.getLogger(__name__)

# Tasks that are explicitly "hard" get routed to the strong tier
_HARD_KEYWORDS = {"hard", "strong", "complex", "difficult", "advanced"}
_CHEAP_KEYWORDS = {"cheap", "easy", "routine", "simple", "basic"}

TierStr = Literal["cheap", "mid", "strong"]


def _difficulty_to_tier(task_difficulty: str) -> TierStr:
    """Map a free-form difficulty string to a model tier."""
    lowered = task_difficulty.lower().strip()
    if lowered in _HARD_KEYWORDS or lowered == "strong":
        return "strong"
    if lowered in _CHEAP_KEYWORDS or lowered == "cheap":
        return "cheap"
    # Anything unrecognised falls back to cheap (cost-conservative)
    logger.debug("Unrecognised difficulty '%s', defaulting to 'cheap'.", task_difficulty)
    return "cheap"


class ModelRouter:
    """
    Selects an (ModelConfig, LLMProvider) pair for a given task difficulty.

    Routing strategy
    ----------------
    - "cheap" / "easy" / "routine" → cheap tier
    - "hard" / "strong" / "complex" → strong tier
    - Anything else → cheap tier (conservative)

    Before routing, the CostTracker is checked; if the budget is exhausted
    the router falls back to the cheapest available model.

    Performance history (per-model success/fail counts) influences future
    routing: models with a poor success rate are deprioritised by picking
    them less often when multiple candidates exist in the same tier.
    """

    def __init__(
        self,
        roster: ModelRoster,
        cost_tracker: CostTracker | None = None,
    ) -> None:
        self.roster = roster
        self.cost_tracker = cost_tracker or CostTracker()

        # model_name -> {"success": int, "fail": int}
        self._history: dict[str, dict[str, int]] = defaultdict(
            lambda: {"success": 0, "fail": 0}
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def route(self, task_difficulty: str) -> tuple[ModelConfig, LLMProvider]:
        """
        Select the best (ModelConfig, LLMProvider) for *task_difficulty*.

        If the budget is exhausted, always routes to the cheapest tier.
        Raises RuntimeError if no models are available at all.
        """
        tier = _difficulty_to_tier(task_difficulty)

        # Budget guard: if we're out of budget, force cheap tier
        if not self.cost_tracker.within_budget():
            remaining = self.cost_tracker.remaining_budget()
            logger.warning(
                "Budget exhausted (remaining=$%.4f). Forcing 'cheap' tier.",
                remaining if remaining is not None else 0.0,
            )
            tier = "cheap"

        candidates = self.roster.by_tier(tier)

        # If no candidates in the target tier, fall back across tiers
        if not candidates:
            logger.warning(
                "No available models in tier '%s'. Falling back to any available model.",
                tier,
            )
            candidates = self.roster.all_available()

        if not candidates:
            raise RuntimeError(
                "ModelRouter: no LLM providers are available. "
                "Check your API keys and provider configuration."
            )

        config, provider = self._pick(candidates)
        logger.info("Router selected model '%s' (tier=%s)", config.name, config.tier)
        return config, provider

    def report_result(self, model_name: str, success: bool) -> None:
        """Record a success or failure for the given model."""
        key = "success" if success else "fail"
        self._history[model_name][key] += 1
        logger.debug(
            "Performance update — model=%s success=%s history=%s",
            model_name,
            success,
            dict(self._history[model_name]),
        )

    def success_rate(self, model_name: str) -> float:
        """Return the historical success rate for a model (0.0–1.0). Default 1.0."""
        h = self._history[model_name]
        total = h["success"] + h["fail"]
        if total == 0:
            return 1.0  # optimistic prior for unseen models
        return h["success"] / total

    def performance_summary(self) -> dict[str, dict[str, int | float]]:
        """Return a summary of per-model performance stats."""
        summary: dict[str, dict[str, int | float]] = {}
        for name, counts in self._history.items():
            summary[name] = {
                **counts,
                "success_rate": self.success_rate(name),
            }
        return summary

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _pick(
        self,
        candidates: list[tuple[ModelConfig, LLMProvider]],
    ) -> tuple[ModelConfig, LLMProvider]:
        """
        Choose one candidate from the list.

        Uses weighted random selection where the weight is the historical
        success rate. Falls back to uniform random if all rates are zero.
        """
        if len(candidates) == 1:
            return candidates[0]

        weights = [self.success_rate(c.name) for c, _ in candidates]

        # If every model has a zero rate (all failed), pick uniformly
        if sum(weights) == 0:
            return random.choice(candidates)

        # Weighted random choice
        chosen = random.choices(candidates, weights=weights, k=1)[0]
        return chosen
