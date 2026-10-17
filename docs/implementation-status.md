# Implementation status

Run `python scripts/code_stats.py` to reproduce source counts. The counter includes `.py` files under `coderill/` and `tests/`; effective lines exclude blank lines, comments, and docstrings. At this checkpoint, production code has **6,776 physical lines** and **5,480 effective lines** across 65 files; tests have **3,037 physical lines** and **2,379 effective lines** across 31 files. Examples, scripts, and documentation are excluded. No duplicate implementation was added to meet a line target.

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
| 7 | Architecture and interview walkthrough, offline failure demo, optional real-model smoke, isolated manifest evaluation, Harbor adapter with preflight, one-task entry and result classification, Linux/Windows CI workflow | `docs/evaluation.md`, `examples/benchmark_suite.py`, `examples/harbor_agent.py`, `examples/harbor_run.py`, `tests/test_harbor_adapter.py`, `tests/test_harbor_run.py` |

## Verification boundary

The latest local Windows run has **215 passed** with real-browser and MCP transport tests enabled, and Ruff passed. Linux CI is configured but has not run locally. A real DeepSeek model completed a read-only CLI task and an isolated file-repair task with a passing verifier. A local text-only Harbor smoke task earned reward 1/1 after the adapter was installed in Docker. The Terminal-Bench chess task reached agent execution but exhausted the request budget because the configured model could not inspect its PNG input; it earned no reward. Real stdio and Streamable HTTP MCP servers, plus a third-party filesystem server, were exercised with temporary configuration. `examples/benchmark_suite.py` uses isolated workspace copies and explicit verifier commands; no public success rate has been produced. Python, CLI and TUI callers use `AgentRuntime`; the old synchronous `Agent` API was removed.

## Remaining gaps against the original plan

- The context pipeline has content-specific offline tests but no measured compression-quality benchmark. External file changes are not automatically marked as stale reads.
- Harbor preflight and result classification have offline tests. The local Harbor smoke task passed; the image-based Terminal-Bench chess task remains unsuitable for the configured text-only model.
- Long-running process logs survive restart; a detached process is deliberately not stoppable through an old PID. No OS sandbox or exactly-once external side effect guarantee exists.
