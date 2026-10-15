"""Error categories that the runtime can handle without SDK imports."""

from enum import Enum


class ProviderErrorKind(str, Enum):
    AUTH = "auth"
    RATE_LIMIT = "rate_limit"
    TIMEOUT = "timeout"
    PROMPT_TOO_LONG = "prompt_too_long"
    UNAVAILABLE = "unavailable"
    INVALID_REQUEST = "invalid_request"


class ProviderError(RuntimeError):
    def __init__(self, kind: ProviderErrorKind, message: str) -> None:
        super().__init__(message)
        self.kind = kind


RETRYABLE = {ProviderErrorKind.RATE_LIMIT, ProviderErrorKind.TIMEOUT, ProviderErrorKind.UNAVAILABLE}


def classify_error(exc: Exception) -> ProviderError:
    if isinstance(exc, ProviderError):
        return exc
    status = getattr(exc, "status_code", None)
    name = type(exc).__name__.lower()
    detail = str(exc)
    if status in (401, 403) or "authentication" in name:
        kind = ProviderErrorKind.AUTH
    elif status == 429 or "ratelimit" in name:
        kind = ProviderErrorKind.RATE_LIMIT
    elif "timeout" in name:
        kind = ProviderErrorKind.TIMEOUT
    elif status in (408, 409, 500, 502, 503, 504) or "connection" in name:
        kind = ProviderErrorKind.UNAVAILABLE
    elif status in (400, 413) and any(term in detail.lower() for term in ("context", "too long", "token limit")):
        kind = ProviderErrorKind.PROMPT_TOO_LONG
    else:
        kind = ProviderErrorKind.INVALID_REQUEST
    return ProviderError(kind, detail)
