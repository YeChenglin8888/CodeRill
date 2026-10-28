"""The fixed retrieval report must keep its denominator and legacy baseline."""

from pathlib import Path

import pytest

from examples.routing_eval import evaluate, evaluate_group


def test_fixed_routing_dataset_has_required_cases_and_reports_recall():
    dataset = Path(__file__).parents[1] / "evals" / "routing.json"
    report = evaluate(dataset)
    assert report["dataset_sha256"]
    for category in ("mcp", "skill"):
        assert report["current"][category]["queries"] == 50
        assert report["current"][category]["macro_recall_at_8"] >= 0.90
    assert report["legacy_mcp"]["queries"] == 50


def test_fixture_rejects_missing_target():
    with pytest.raises(ValueError, match="missing targets"):
        evaluate_group({"items": [{"name": "a", "description": "a"}],
                        "queries": [{"target": "b", "texts": ["b"] * 5}],
                        "negative_queries": ["none"]}, lambda _q, _items: [])
