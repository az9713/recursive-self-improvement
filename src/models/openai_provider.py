"""OpenAI LLM provider implementation."""

from __future__ import annotations

import logging
import os

from dotenv import load_dotenv

from src.models.base import LLMProvider, ModelConfig

logger = logging.getLogger(__name__)

# Model catalogue — prices in USD per 1,000 tokens
_OPENAI_MODELS: list[ModelConfig] = [
    ModelConfig(
        name="gpt-4o-mini",
        provider="openai",
        cost_per_1k_input=0.15 / 1000,   # $0.15 / 1M  → /1k = $0.00015
        cost_per_1k_output=0.60 / 1000,  # $0.60 / 1M  → /1k = $0.00060
        tier="cheap",
    ),
    ModelConfig(
        name="gpt-4o",
        provider="openai",
        cost_per_1k_input=2.50 / 1000,    # $2.50 / 1M  → /1k = $0.0025
        cost_per_1k_output=10.00 / 1000,  # $10.00 / 1M → /1k = $0.01
        tier="strong",
    ),
]


class OpenAIProvider(LLMProvider):
    """Async OpenAI provider wrapping the official openai SDK."""

    def __init__(self) -> None:
        load_dotenv()
        self.models: dict[str, ModelConfig] = {m.name: m for m in _OPENAI_MODELS}
        self.available = False
        self._client = None

        api_key = os.environ.get("OPENAI_API_KEY", "")
        if not api_key:
            logger.warning(
                "OPENAI_API_KEY not set — OpenAI provider is unavailable."
            )
            return

        try:
            import openai  # noqa: PLC0415

            self._client = openai.AsyncOpenAI(api_key=api_key)
            self.available = True
        except ImportError:
            logger.warning(
                "openai package not installed — OpenAI provider is unavailable."
            )

    async def complete(
        self,
        prompt: str,
        system: str = "",
        temperature: float = 0.0,
        model_name: str | None = None,
    ) -> tuple[str, float]:
        """
        Send a completion request to OpenAI.

        Returns (response_text, cost_usd).
        Raises RuntimeError if the provider is unavailable.
        """
        if not self.available or self._client is None:
            raise RuntimeError("OpenAI provider is not available (check API key).")

        config = self.get_model(model_name)

        messages: list[dict] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        try:
            response = await self._client.chat.completions.create(
                model=config.name,
                messages=messages,
                temperature=temperature,
            )
        except Exception as exc:
            logger.error("OpenAI API error for model %s: %s", config.name, exc)
            raise

        text = response.choices[0].message.content or ""

        # Calculate cost from actual usage tokens
        usage = response.usage
        cost = config.cost_for_tokens(
            input_tokens=usage.prompt_tokens,
            output_tokens=usage.completion_tokens,
        )

        logger.debug(
            "OpenAI %s — in=%d out=%d cost=$%.6f",
            config.name,
            usage.prompt_tokens,
            usage.completion_tokens,
            cost,
        )
        return text, cost
