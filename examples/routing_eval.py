"""Deterministic capability-retrieval evaluation; no model calls by default."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
from pathlib import Path

from coderill.routing import search_capabilities


def legacy_mcp_search(query: str, items: list[dict[str, str]]) -> list[dict[str, str]]:
    words = set(re.findall(r"\w+", query.casefold()))
    ranked = []
    for item in items:
        name = item["name"].casefold()
        description = item["description"].casefold()
        score = sum(10 if word in name else 1 if word in description else 0 for word in words)
        if score:
            ranked.append((-score, name, item))
    ranked.sort()
    return [item for _, _, item in ranked[:8]]


def evaluate_group(group: dict, search) -> dict:
    items = group["items"]
    names = {item["name"] for item in items}
    if len(names) != len(items) or not all(row["target"] in names and len(row["texts"]) == 5
                                               for row in group["queries"]):
        raise ValueError("routing fixture has duplicate names, missing targets or incomplete query groups")
    recalls = []
    latencies = []
    for row in group["queries"]:
        hits = 0
        for query in row["texts"]:
            started = time.monotonic()
            results = search(query, items)
            latencies.append((time.monotonic() - started) * 1000)
            hits += any(item["name"] == row["target"] for item in results[:8])
        recalls.append(hits / len(row["texts"]))
    negatives = group["negative_queries"]
    false_hits = sum(bool(search(query, items)) for query in negatives)
    return {"queries": sum(len(row["texts"]) for row in group["queries"]),
            "macro_recall_at_8": sum(recalls) / len(recalls),
            "negative_queries": len(negatives), "negative_hit_rate": false_hits / len(negatives),
            "mean_latency_ms": sum(latencies) / len(latencies)}


def evaluate(path: Path, embedding: dict[str, str] | None = None) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    for name in ("mcp", "skill"):
        if len(data[name]["queries"]) < 10:
            raise ValueError(f"{name} requires at least 10 query groups")
    return {
        "dataset": str(path.resolve()), "dataset_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "current": {name: evaluate_group(data[name], lambda q, items: search_capabilities(q, items, embedding))
                    for name in ("mcp", "skill")},
        "legacy_mcp": evaluate_group(data["mcp"], legacy_mcp_search),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Measure CodeRill capability discovery on fixed queries.")
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--embedding-config", type=Path, help="JSON with base_url, model and api_key_env")
    args = parser.parse_args()
    embedding = json.loads(args.embedding_config.read_text(encoding="utf-8")) if args.embedding_config else None
    report = evaluate(args.dataset, embedding)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["current"], ensure_ascii=False))


if __name__ == "__main__":
    main()
