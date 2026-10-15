"""Model protocol and OpenAI-compatible adapter."""

from .anthropic_adapter import AnthropicChatProvider
from .base import ChatProvider
from .openai_adapter import OpenAIChatProvider
from .openai_compatible import LLM, LLMError, Response, Usage, remaining_time

__all__ = [
    "LLM",
    "AnthropicChatProvider",
    "ChatProvider",
    "LLMError",
    "OpenAIChatProvider",
    "Response",
    "Usage",
    "create_provider",
    "remaining_time",
]


def create_provider(config):
    """Instantiate the selected vendor behind the common async contract."""
    options = {
        "model": config.model,
        "api_key": config.api_key,
        "base_url": config.base_url,
        "vision": config.vision,
        "attempts": min(max(config.max_retries, 1), 5),
    }
    if config.provider == "anthropic":
        return AnthropicChatProvider(**options)
    if config.provider == "openai":
        return OpenAIChatProvider(**options)
    raise ValueError(f"unknown provider '{config.provider}'")
