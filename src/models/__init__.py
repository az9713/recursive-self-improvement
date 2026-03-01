"""LLM provider abstractions and cost-aware routing."""

from src.models.anthropic_provider import AnthropicProvider
from src.models.base import LLMProvider, ModelConfig, ModelRoster
from src.models.google_provider import GoogleProvider
from src.models.openai_provider import OpenAIProvider
from src.models.router import ModelRouter

__all__ = [
    "ModelConfig",
    "LLMProvider",
    "ModelRoster",
    "OpenAIProvider",
    "AnthropicProvider",
    "GoogleProvider",
    "ModelRouter",
]


def build_default_roster() -> ModelRoster:
    """
    Convenience factory: instantiate all providers and return a fully
    populated ModelRoster.  Providers whose API keys are missing are
    registered but marked unavailable — they won't be selected by the router.
    """
    roster = ModelRoster()

    for provider_cls in (OpenAIProvider, AnthropicProvider, GoogleProvider):
        provider = provider_cls()
        for config in provider.models.values():
            roster.register(config, provider)

    return roster
