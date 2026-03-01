"""Anthropic LLM provider implementation."""

from __future__ import annotations

import logging
import os

from dotenv import load_dotenv

from src.models.base import LLMProvider, ModelConfig

logger = logging.getLogger(__name__)

# Model catalogue — prices in USD per 1,000 tokens
_ANTHROPIC_MODELS: list[ModelConfig] = [
    ModelConfig(
        name="claude-haiku-4-5-20251001",
        provider="anthropic",
        cost_per_1k_input=0.80 / 1000,   # $0.80 / 1M  → /1k = $0.0008
        cost_per_1k_output=4.00 / 1000,  # $4.00 / 1M  → /1k = $0.004
        tier="cheap",
    ),
    ModelConfig(
        name="claude-sonnet-4-6",
        provider="anthropic",
        cost_per_1k_input=3.00 / 1000,    # $3.00 / 1M  → /1k = $0.003
        cost_per_1k_output=15.00 / 1000,  # $15.00 / 1M → /1k = $0.015
        tier="strong",
    ),
]

# Maximum output tokens to request (Anthropic requires an explicit limit)
_MAX_TOKENS = 4096


class AnthropicProvider(LLMProvider):
    """Async Anthropic provider wrapping the official anthropic SDK."""

    def __init__(self) -> None:
        load_dotenv()
        self.models: dict[str, ModelConfig] = {m.name: m for m in _ANTHROPIC_MODELS}
        self.available = False
        self._client = None

        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            logger.warning(
                "ANTHROPIC_API_KEY not set — Anthropic provider is unavailable."
            )
            return

        try:
            import anthropic  # noqa: PLC0415

            self._client = anthropic.AsyncAnthropic(api_key=api_key)
            self.available = True
        except ImportError:
            logger.warning(
                "anthropic package not installed — Anthropic provider is unavailable."
            )

    async def complete(
        self,
        prompt: str,
        system: str = "",
        temperature: float = 0.0,
        model_name: str | None = None,
    ) -> tuple[str, float]:
        """
        Send a completion request to Anthropic.

        Returns (response_text, cost_usd).
        Raises RuntimeError if the provider is unavailable.
        """
        if not self.available or self._client is None:
            raise RuntimeError("Anthropic provider is not available (check API key).")

        config = self.get_model(model_name)

        # Build kwargs — system is a top-level field in the Anthropic API
        kwargs: dict = {
            "model": config.name,
            "max_tokens": _MAX_TOKENS,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
        }
        if system:
            kwargs["system"] = system

        try:
            response = await self._client.messages.create(**kwargs)
        except Exception as exc:
            logger.error("Anthropic API error for model %s: %s", config.name, exc)
            raise

        # Extract text from the first content block
        text = ""
        if response.content:
            block = response.content[0]
            if hasattr(block, "text"):
                text = block.text

        # Calculate cost from actual usage tokens
        usage = response.usage
        cost = config.cost_for_tokens(
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
        )

        logger.debug(
            "Anthropic %s — in=%d out=%d cost=$%.6f",
            config.name,
            usage.input_tokens,
            usage.output_tokens,
            cost,
        )
        return text, cost
