"""Google Gemini LLM provider implementation using the google-genai SDK."""

from __future__ import annotations

import asyncio
import logging
import os

from dotenv import load_dotenv

from src.models.base import LLMProvider, ModelConfig

logger = logging.getLogger(__name__)

# Model catalogue — prices in USD per 1,000 tokens
_GOOGLE_MODELS: list[ModelConfig] = [
    ModelConfig(
        name="gemini-2.5-flash",
        provider="google",
        cost_per_1k_input=0.15 / 1000,   # $0.15 / 1M  → /1k = $0.00015
        cost_per_1k_output=0.60 / 1000,  # $0.60 / 1M  → /1k = $0.0006
        tier="cheap",
    ),
    ModelConfig(
        name="gemini-2.5-pro",
        provider="google",
        cost_per_1k_input=1.25 / 1000,    # $1.25 / 1M  → /1k = $0.00125
        cost_per_1k_output=10.00 / 1000,  # $10.00 / 1M → /1k = $0.01
        tier="strong",
    ),
]


class GoogleProvider(LLMProvider):
    """
    Provider for Google Gemini models using the google-genai unified SDK.

    Uses `google.genai.Client` (pip package: google-genai).
    Falls back to running the sync call in a thread if async is unavailable.
    """

    def __init__(self) -> None:
        load_dotenv()
        self.models: dict[str, ModelConfig] = {m.name: m for m in _GOOGLE_MODELS}
        self.available = False
        self._client = None

        api_key = os.environ.get("GOOGLE_API_KEY", "")
        if not api_key:
            logger.warning(
                "GOOGLE_API_KEY not set — Google provider is unavailable."
            )
            return

        try:
            from google import genai  # noqa: PLC0415

            self._client = genai.Client(api_key=api_key)
            self.available = True
        except ImportError:
            logger.warning(
                "google-genai package not installed — Google provider is unavailable."
            )

    async def complete(
        self,
        prompt: str,
        system: str = "",
        temperature: float = 0.0,
        model_name: str | None = None,
    ) -> tuple[str, float]:
        """
        Send a completion request to Google Gemini.

        Returns (response_text, cost_usd).
        Raises RuntimeError if the provider is unavailable.
        """
        if not self.available or self._client is None:
            raise RuntimeError("Google provider is not available (check API key).")

        config = self.get_model(model_name)

        # Prepend system prompt as a prefixed user message when provided,
        # since google-genai's generate_content takes a simple contents string.
        contents = prompt
        if system:
            contents = f"{system}\n\n{prompt}"

        from google.genai import types  # noqa: PLC0415

        generation_config = types.GenerateContentConfig(temperature=temperature)

        try:
            # Prefer async if available; otherwise run sync in executor
            if hasattr(self._client.models, "generate_content_async"):
                response = await self._client.models.generate_content_async(
                    model=config.name,
                    contents=contents,
                    config=generation_config,
                )
            else:
                loop = asyncio.get_running_loop()
                response = await loop.run_in_executor(
                    None,
                    lambda: self._client.models.generate_content(
                        model=config.name,
                        contents=contents,
                        config=generation_config,
                    ),
                )
        except Exception as exc:
            logger.error("Google API error for model %s: %s", config.name, exc)
            raise

        text = ""
        if response.text:
            text = response.text

        # Calculate cost from usage_metadata
        cost = 0.0
        if response.usage_metadata:
            meta = response.usage_metadata
            input_tokens = getattr(meta, "prompt_token_count", 0) or 0
            output_tokens = getattr(meta, "candidates_token_count", 0) or 0
            cost = config.cost_for_tokens(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
            logger.debug(
                "Google %s — in=%d out=%d cost=$%.6f",
                config.name,
                input_tokens,
                output_tokens,
                cost,
            )
        else:
            logger.debug("Google %s — no usage_metadata in response", config.name)

        return text, cost
