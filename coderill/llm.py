"""Compatibility imports for the model adapter."""

from .providers.openai_compatible import LLM, LLMError, Response, Usage, remaining_time

__all__ = ["LLM", "LLMError", "Response", "Usage", "remaining_time"]
