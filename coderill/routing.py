"""Rank capability descriptions without exposing full tool or Skill bodies."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import time
from dataclasses import replace
from pathlib import Path


def _terms(value: str) -> set[str]:
    value = value.casefold().replace("_", " ").replace("-", " ")
    terms = set(re.findall(r"[a-z0-9]+|[\u3400-\u9fff]+", value))
    for word in list(terms):
        if re.fullmatch(r"[\u3400-\u9fff]+", word):
            terms.update(word[index:index + 2] for index in range(len(word) - 1))
            terms.update(word)
    return terms


def _local_rank(query: str, items: list[dict[str, str]]) -> list[dict[str, str]]:
    words = _terms(query)
    ranked = []
    for item in items:
        name = _terms(item["name"])
        description = _terms(item["description"])
        score = 10 * len(words & name) + len(words & description)
        if score:
            ranked.append((-score, item["name"].casefold(), item))
    ranked.sort()
    return [item for _, _, item in ranked]


def _semantic_rank(query: str, items: list[dict[str, str]], config: dict[str, str],
                   deadline: float | None = None) -> list[dict[str, str]]:
    from openai import OpenAI

    key = os.getenv(config["api_key_env"])
    if not key:
        return []
    timeout = min(2.0, deadline - time.monotonic()) if deadline is not None else 2.0
    if timeout <= 0:
        return []
    client = OpenAI(api_key=key, base_url=config["base_url"], timeout=timeout, max_retries=0)
    texts = [query[:2000], *(f'{item["name"]}: {item["description"][:240]}' for item in items)]
    response = client.embeddings.create(model=config["model"], input=texts)
    vectors = [entry.embedding for entry in sorted(response.data, key=lambda entry: entry.index)]
    if len(vectors) != len(texts) or not vectors[0]:
        return []
    width = len(vectors[0])
    norms = [math.sqrt(sum(value * value for value in vector)) for vector in vectors]
    if any(len(vector) != width or not math.isfinite(norm) or norm == 0 for vector, norm in zip(vectors, norms)):
        return []
    ranked = []
    for item, vector, norm in zip(items, vectors[1:], norms[1:]):
        similarity = sum(a * b for a, b in zip(vectors[0], vector)) / (norms[0] * norm)
        ranked.append((-similarity, item["name"].casefold(), item))
    ranked.sort()
    return [item for _, _, item in ranked]


def search_capabilities(query: str, items: list[dict[str, str]], embedding: dict[str, str] | None = None,
                        metadata: dict[str, str] | None = None,
                        deadline: float | None = None) -> list[dict[str, str]]:
    if not query.strip():
        raise ValueError("search query must not be blank")
    local = _local_rank(query, items)
    if metadata is not None:
        metadata["source"] = "local"
    if not embedding or not items:
        return local[:8]
    try:
        semantic = _semantic_rank(query, items, embedding, deadline)
    except Exception as exc:  # noqa: BLE001 - unavailable optional service leaves local discovery usable
        if metadata is not None:
            metadata.update(source="fallback", reason=type(exc).__name__)
        return local[:8]
    if not semantic:
        if metadata is not None:
            metadata.update(source="fallback", reason="empty_embedding")
        return local[:8]
    if metadata is not None:
        metadata["source"] = "hybrid"
    scores: dict[str, float] = {}
    for ranked in (local, semantic):
        for position, item in enumerate(ranked, 1):
            scores[item["name"]] = scores.get(item["name"], 0.0) + 1 / (60 + position)
    return sorted((item for item in items if item["name"] in scores),
                  key=lambda item: (-scores[item["name"]], item["name"].casefold()))[:8]


def qualified_light_config(config):
    """Use only a measured, compatible profile for bounded read-only work."""
    routing = config.routing
    name = routing.get("light_profile") if routing.get("enabled", True) else None
    profile = config.profiles.get(name) if isinstance(name, str) else None
    report_path = routing.get("qualification_report")
    if not isinstance(profile, dict) or not isinstance(report_path, str):
        return None
    if (profile.get("provider", config.provider) != config.provider or
            (profile.get("base_url") or config.base_url or "").rstrip("/") != (config.base_url or "").rstrip("/") or
            type(profile.get("max_context_tokens")) is not int or profile["max_context_tokens"] <= 0 or
            not isinstance(profile.get("vision"), bool)):
        return None
    model = profile.get("model")
    if not isinstance(model, str) or not model or (config.vision and not profile["vision"]):
        return None
    try:
        report = json.loads(Path(report_path).expanduser().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(report, dict) or not isinstance(report.get("base_url"), str):
        return None
    dataset = report.get("dataset")
    try:
        dataset_hash = hashlib.sha256(Path(dataset).expanduser().read_bytes()).hexdigest()
    except (OSError, TypeError, ValueError):
        return None
    profile_hash = light_profile_hash(profile, config)
    rates = [report.get(key) for key in ("primary_pass_rate", "light_pass_rate",
                                           "primary_boundary_error_rate", "light_boundary_error_rate")]
    if not all(isinstance(rate, (int, float)) and math.isfinite(rate) and 0 <= rate <= 1 for rate in rates):
        return None
    if not isinstance(report.get("read_only_count"), int) or not isinstance(report.get("boundary_count"), int):
        return None
    if any((
        report.get("status") != "passed",
        report.get("provider") != config.provider,
        report["base_url"].rstrip("/") != (config.base_url or "").rstrip("/"),
        report.get("primary_model") != config.model,
        report.get("light_model") != model,
        report.get("read_only_count", 0) < 100,
        report.get("boundary_count", 0) < 50,
        report.get("dataset_sha256") != dataset_hash,
        report.get("profile_sha256") != profile_hash,
        rates[1] < rates[0] - 0.02,
        rates[3] > rates[2],
    )):
        return None
    return replace(config, model=model, vision=profile["vision"],
                   max_context_tokens=profile["max_context_tokens"],
                   thinking_enabled=profile.get("thinking_enabled", config.thinking_enabled))


def light_profile_hash(profile: dict, primary) -> str:
    fields = {"light": {"model": profile["model"], "vision": profile["vision"],
                        "max_context_tokens": profile["max_context_tokens"],
                        "thinking_enabled": profile.get("thinking_enabled", primary.thinking_enabled)},
              "primary": {key: getattr(primary, key) for key in (
                  "provider", "base_url", "model", "temperature", "max_tokens", "max_turns",
                  "max_tool_rounds", "max_turn_seconds", "request_timeout", "max_context_tokens",
                  "thinking_enabled", "vision")}}
    return hashlib.sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest()
