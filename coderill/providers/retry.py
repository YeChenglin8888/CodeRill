"""Bounded retries before any tool call or final response is committed."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import TypeVar

from .errors import RETRYABLE, ProviderError, ProviderErrorKind

T = TypeVar("T")


def remaining(deadline: float | None) -> float | None:
    if deadline is None:
        return None
    value = deadline - time.monotonic()
    if value <= 0:
        raise ProviderError(ProviderErrorKind.TIMEOUT, "model request deadline reached")
    return value


async def retry_async(action: Callable[[], Awaitable[T]], *, attempts: int, deadline: float | None) -> T:
    attempts = max(attempts, 1)
    for attempt in range(attempts):
        try:
            return await asyncio.wait_for(action(), timeout=remaining(deadline))
        except asyncio.TimeoutError as exc:
            error = ProviderError(ProviderErrorKind.TIMEOUT, "model request timed out")
            error.__cause__ = exc
        except ProviderError as exc:
            error = exc
        if error.kind not in RETRYABLE or attempt == attempts - 1:
            raise error
        delay = min(2**attempt, 8)
        left = remaining(deadline)
        if left is not None and left <= delay:
            raise error
        await asyncio.sleep(delay)
    raise AssertionError("unreachable")
