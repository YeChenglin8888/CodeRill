# Implementation status

Run `python scripts/code_stats.py` to reproduce source counts. The counter includes `.py` files under `coderill/` and `tests/`; effective lines exclude blank lines, comments, and docstrings. At this checkpoint, production code has **6,581 physical lines** and **5,257 effective lines** across 63 files; tests have **2,865 physical lines** and **2,225 effective lines** across 31 files. Examples, scripts, and documentation are excluded. The proposed 15,000–20,000 production-line estimate has not been reached; no duplicate implementation was added to meet a line target.

## Delivered by stage

| Stage | Delivered | Evidence |
| --- | --- | --- |
| 0 | Repaired partial package split and startup imports | Offline suite, Ruff, CLI help, wheel build |
| 1 | Shared provider protocol, OpenAI compatible and native Anthropic adapters, layered config and profiles | `tests/test_provider_contract.py`, `tests/test_config.py` |
| 2 | v1/v2 event replay, pure projection, durable pending reviews and tool state, async runner, process lock | `tests/test_session.py`, `tests/test_runtime.py` |
| 3 | Scoped review and grants, stale diff refresh, command timeouts and cancellation, safe undo check | `tests/test_review_manager.py`, `tests/test_process_runtime.py`, `tests/test_checkpoints.py` |
| 4 | Full request budget estimate, task-boundary checkpoint trigger, stale-read hints, type-aware old-output previews, archival, summary fallback, revisioned task plan | `tests/test_context_builder.py`, `tests/test_runtime.py`, `tests/test_taskplan.py` |
| 5 | CLI and Textual TUI on one runner; persisted text/image attachments and capability check | `tests/test_tui.py`, `tests/test_attachments.py`, `tests/test_app_state.py` |
| 6 | Official MCP SDK transports and per-turn tool search; pinned Skill snapshots; durable background status and plan reconciliation; long-running process logs; bounded child agents and worktree review/apply | `tests/test_mcp_sdk.py`, `tests/test_skills.py`, `tests/test_services.py`, `tests/test_background.py`, `tests/test_subagents.py` |
| 7 | Architecture and interview walkthrough, offline failure demo, optional real-model smoke, isolated manifest evaluation and a Harbor installed-agent adapter, Linux/Windows CI workflow and reproducible line count | `docs/architecture.md`, `docs/CodeRill项目面试完整版.md`, `docs/evaluation.md`, `examples/failure_demo.py`, `examples/live_eval.py`, `examples/benchmark_suite.py`, `tests/test_harbor_adapter.py`, `.github/workflows/ci.yml` |

## Verification boundary

The latest local Windows run has **194 passed, 6 skipped** and Ruff passed. Six real-browser tests are skipped unless explicitly enabled. Linux CI is configured but has not run locally. Provider adapters use fake SDK clients in core tests; no live model API, MCP server, Harbor job, or browser session was exercised for this checkpoint. `examples/benchmark_suite.py` can run a fixed manifest with isolated workspace copies and explicit verifier commands; no public success rate has been produced. The legacy synchronous `Agent` remains for direct Python callers; the CLI and TUI run through `AgentRuntime`.

## Remaining gaps against the original plan

- The 15,000–20,000 production-line estimate remains unmet. Line count alone is not an acceptance test.
- Context previews are type-aware, but they do not equal FirstCoder's full per-content L2 algorithms or a measured compression-quality benchmark. External file changes are not automatically marked as stale reads.
- The benchmark manifest runner and a Harbor adapter are available. The adapter has a fake-interface contract test; no actual Harbor job or fixed CodeRill benchmark run has completed. FirstCoder's reported reward numbers are not CodeRill results.
- Long-running process logs survive restart; a detached process is deliberately not stoppable through an old PID. No OS sandbox or exactly-once external side effect guarantee exists.
