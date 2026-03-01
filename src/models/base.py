"""Base abstractions for LLM providers and model configuration."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Literal

logger = logging.getLogger(__name__)


@dataclass
class ModelConfig:
    """Configuration for a single LLM model."""

    name: str
    provider: str
    cost_per_1k_input: float   # USD per 1,000 input tokens
    cost_per_1k_output: float  # USD per 1,000 output tokens
    tier: Literal["cheap", "mid", "strong"]

    def cost_for_tokens(self, input_tokens: int, output_tokens: int) -> float:
        """Calculate cost in USD for the given token counts."""
        return (input_tokens / 1000) * self.cost_per_1k_input + (
            output_tokens / 1000
        ) * self.cost_per_1k_output


class LLMProvider(ABC):
    """Abstract base class for all LLM providers."""

    # Subclasses populate this during __init__ via _register_models()
    models: dict[str, ModelConfig] = {}
    available: bool = True

    @abstractmethod
    async def complete(
        self,
        prompt: str,
        system: str = "",
        temperature: float = 0.0,
        model_name: str | None = None,
    ) -> tuple[str, float]:
        """
        Run a completion request.

        Parameters
        ----------
        prompt:
            The user-facing prompt text.
        system:
            Optional system / instruction preamble.
        temperature:
            Sampling temperature (0 = deterministic).
        model_name:
            Which model to use. If None, uses provider default.

        Returns
        -------
        (response_text, cost_usd)
        """

    def get_model(self, model_name: str | None = None) -> ModelConfig:
        """Return the ModelConfig for *model_name*, or the first registered model."""
        if not self.models:
            raise RuntimeError(f"{type(self).__name__} has no registered models.")
        if model_name is None:
            return next(iter(self.models.values()))
        if model_name not in self.models:
            raise ValueError(
                f"Model '{model_name}' not found in {type(self).__name__}. "
                f"Available: {list(self.models.keys())}"
            )
        return self.models[model_name]


@dataclass
class ModelRoster:
    """
    Central registry of all available ModelConfig instances and their providers.

    Usage::

        roster = ModelRoster()
        roster.register(config, provider)
        cheap_models = roster.by_tier("cheap")
    """

    _entries: list[tuple[ModelConfig, LLMProvider]] = field(default_factory=list)

    def register(self, config: ModelConfig, provider: LLMProvider) -> None:
        """Add a (config, provider) pair to the roster."""
        self._entries.append((config, provider))

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    def by_tier(self, tier: Literal["cheap", "mid", "strong"]) -> list[tuple[ModelConfig, LLMProvider]]:
        """Return all (config, provider) pairs for the given tier."""
        return [(c, p) for c, p in self._entries if c.tier == tier and p.available]

    def by_provider(self, provider_name: str) -> list[tuple[ModelConfig, LLMProvider]]:
        """Return all (config, provider) pairs for the named provider."""
        return [
            (c, p)
            for c, p in self._entries
            if c.provider == provider_name and p.available
        ]

    def all_available(self) -> list[tuple[ModelConfig, LLMProvider]]:
        """Return all entries whose provider is currently available."""
        return [(c, p) for c, p in self._entries if p.available]

    def get(self, model_name: str) -> tuple[ModelConfig, LLMProvider] | None:
        """Look up a specific model by name. Returns None if not found."""
        for config, provider in self._entries:
            if config.name == model_name:
                return config, provider
        return None

    def names(self) -> list[str]:
        """Return all registered model names."""
        return [c.name for c, _ in self._entries]

    def __len__(self) -> int:
        return len(self._entries)

    def __repr__(self) -> str:
        names = [c.name for c, _ in self._entries]
        return f"ModelRoster({names})"
