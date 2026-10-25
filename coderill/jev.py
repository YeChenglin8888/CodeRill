"""Typed, bounded decisions through the existing Jev System One endpoint."""

from __future__ import annotations

import math
import re
from typing import Any

from .browser import TYPESAFE_ENDPOINT

_OPTION = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,31}\Z")


def choose(state: Any, question: str, options: dict[str, str], *, key: str,
           model: str = "jev-latest", timeout: float = 30) -> dict:
    """Return a validated choice and probabilities; never turn a failed call into a decision."""
    import httpx

    if not key:
        raise ValueError("TYPESAFE_API_KEY is required for Jev decisions")
    if not isinstance(question, str) or not question.strip() or len(question) > 1000:
        raise ValueError("decision question must be 1-1000 characters")
    if not isinstance(options, dict) or not 2 <= len(options) <= 8 or any(
        not isinstance(name, str) or not _OPTION.fullmatch(name) or
        not isinstance(description, str) or len(description) > 2000
        for name, description in options.items()
    ):
        raise ValueError("decision needs 2-8 named options with short descriptions")
    request = {"model": model, "state": state, "questions": {"decision": {
        "type": "choice", "instructions": question,
        "criteria": options,
    }}}
    with httpx.Client(headers={"Authorization": f"Bearer {key}"}, follow_redirects=False) as client:
        response = client.post(TYPESAFE_ENDPOINT, json=request, timeout=timeout)
        response.raise_for_status()
    body = response.json()
    try:
        answer = body["answers"]["decision"]
        probabilities = answer["probabilities"]
        choice = answer["choice"]
        confidence = answer["confidence"]
        if (answer["type"] != "choice" or set(probabilities) != set(options) or
            choice not in options or not all(type(p) in (int, float) and math.isfinite(p)
                and 0 <= p <= 1 for p in probabilities.values()) or
            abs(sum(probabilities.values()) - 1) >= 0.02 or
            probabilities[choice] != max(probabilities.values()) or
            type(confidence) not in (int, float) or not math.isfinite(confidence)
            or not 0 <= confidence <= 1):
            raise ValueError("invalid choice")
        return {"choice": choice, "probabilities": probabilities,
                "confidence": confidence, "model": body["model"], "usage": body["usage"]}
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Jev returned an invalid decision response") from exc
