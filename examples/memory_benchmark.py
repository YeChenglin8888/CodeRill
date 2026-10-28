"""Run a small, reproducible retrieval and stale-memory evaluation."""

from __future__ import annotations

import argparse
import json
import tempfile
import time
from pathlib import Path

from coderill.memory import MemoryStore


def evaluate(cases: list[dict], mode: str) -> dict:
    if mode not in ("off", "lexical", "hybrid", "lifecycle"):
        raise ValueError("unknown memory mode")
    results = []
    with tempfile.TemporaryDirectory(prefix="coderill-memory-eval-") as temporary:
        for index, case in enumerate(cases):
            store = MemoryStore(Path(temporary) / str(index), Path(temporary) / "stores")
            if mode in ("off", "lexical"):
                store._model_checked = True
            for number, record in enumerate(case["records"]):
                store.add(key=record["key"], kind="decision", content=record["content"],
                          evidence=record["content"], source=f"fixture:{number}", source_type="user",
                          update=mode == "lifecycle" and record.get("update", False))
            start = time.perf_counter()
            found = [] if mode == "off" else store.search(case["query"], 5)
            elapsed = time.perf_counter() - start
            contents = [item["content"] for item in found]
            expected = case.get("expected")
            stale = case.get("stale", [])
            results.append({"id": case["id"], "expected": expected, "retrieved": contents,
                            "hit": expected in contents if expected else not contents,
                            "stale_used": any(item in contents for item in stale),
                            "semantic_enabled": store._model is not None,
                            "seconds": round(elapsed, 6)})
    total = len(results)
    return {"mode": mode, "cases": total, "retrieval_accuracy": sum(row["hit"] for row in results) / total if total else 0,
            "stale_cases": sum(row["stale_used"] for row in results), "results": results}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cases", type=Path, help="JSON list of memory evaluation cases")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    cases = json.loads(args.cases.read_text(encoding="utf-8"))
    report = {"type": "synthetic_retrieval_only", "model": "BAAI/bge-m3 if locally available",
              "conditions": [evaluate(cases, mode) for mode in ("off", "lexical", "hybrid", "lifecycle")]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
