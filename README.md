# CodeRill

CodeRill is a Python coding agent built around recoverable long tasks. It keeps an append-only session log, pauses for file reviews, handles context limits, and resumes after interruptions. The CLI and optional Textual interface use the same async runtime.

[中文说明](README.zh-CN.md) · [Architecture and failure semantics](docs/architecture.md) · [Interview walkthrough](docs/CodeRill项目面试完整版.md) · [Evaluation](docs/evaluation.md) · [Implementation status](docs/implementation-status.md)

## Install and run

Python 3.10 or newer is required. For a local checkout:

```bash
pip install -e ".[tui,mcp]"
export OPENAI_API_KEY=...       # or set CODERILL_API_KEY / ANTHROPIC_API_KEY
coderill                     # interactive CLI
coderill "fix the test"      # one request
coderill --message "fix it"  # equivalent one-request form
coderill --tui               # optional Textual interface
```

On Windows, set the key in your shell or a project `.env` file. An OpenAI-compatible endpoint can be selected with `OPENAI_BASE_URL`; native Anthropic Messages uses `--provider anthropic` and `ANTHROPIC_API_KEY`.

```bash
coderill --cwd path/to/project --provider anthropic "explain this module"
coderill --attach screenshot.png --message "what is wrong here?"
coderill --list
coderill --resume SESSION_ID
```

Image attachment requires a model profile with `vision = true`. Text and image attachments are stored under the session so a restart can reconstruct the request. The model, provider, and vision capability are configured by `~/.coderill/config.toml`, a project `.coderill.toml`, environment variables, or CLI options, in that order of precedence.

```toml
provider = "openai"
model = "gpt-4o-mini"

[models.claude]
provider = "anthropic"
model = "claude-sonnet-4-5"
api_key_env = "ANTHROPIC_API_KEY"
vision = true
```

Select a profile in the REPL with `/model claude` or in the TUI model selector. Existing commands include `/new`, `/list`, `/resume`, `/fork`, `/share`, `/where`, `/cost`, `/plan`, `/approve`, `/mode`, `/undo`, `/reasoning`, `/mcp`, and `/exit`. New commands are `/attach`, `/model`, `/background`, and `/apply`.

## Runtime structure

| Package | Responsibility |
| --- | --- |
| `coderill/app/` | CLI/TUI commands, session selection, dependency assembly |
| `coderill/agent/runtime.py` | Async turn loop, tool settlement, pause/resume/cancel |
| `coderill/providers/` | Provider-neutral contract and OpenAI/Anthropic adapters |
| `coderill/session/` | Versioned JSONL events, replay, locking, attachments |
| `coderill/context/` | Request budgeting, trimming, archival, summaries |
| `coderill/permissions/` | Scoped grants and durable pre-write reviews |
| `coderill/runtime/` | Process cancellation and background task state |
| `coderill/tools/`, `coderill/mcp.py` | Built-in and MCP tools |

A pending review records its ID, tool arguments, diff, target path, and file digest. If the file changes before approval, CodeRill creates a new review. Tool calls started before a crash are recorded as result-unknown and are not automatically replayed. Old v1 sessions remain readable; new events use v2. The legacy synchronous Python `Agent` still exists for existing direct callers; the CLI/TUI use `AgentRuntime`.

Read-only tool calls in a consecutive batch run concurrently. Writes stay in order. Background child agents use independent sessions and budgets. A writable child runs in a Git worktree; `/apply CHILD_ID` previews and explicitly applies its diff. A worktree isolates the working directory, not the operating system.

See [architecture.md](docs/architecture.md) for the request sequence, approval state diagram, recovery details, and links from capabilities to tests.

## Tools and integrations

Built-in tools read, write, and edit workspace files; search by filename and content; run shell commands; manage a task plan; load skills on demand; retrieve archived output; and delegate bounded tasks. File paths are constrained to the workspace. Shell commands require review in standard mode and have timeout, cancellation, and output limits.

Optional MCP uses the official Python SDK for stdio and Streamable HTTP servers. Server configuration is read from `~/.coderill/mcp.json`. The optional Jev browser tool needs an OpenAI-compatible model, `pip install -e ".[browser]"`, `CODERILL_BROWSER_ENABLED=1`, and `TYPESAFE_*` settings. Its DOM reader is adapted from `browser-use/jev-ultrafast`; see [the retained license notice](coderill/browser_NOTICE.txt).

## Offline verification

```bash
pip install -e ".[dev,tui,mcp]"
pytest tests/ -q
ruff check .
python examples/failure_demo.py
python -m pip wheel . --no-deps --wheel-dir dist
```

CI runs tests, Ruff, CLI import/help, and wheel building on Linux and Windows. Core scenarios use fakes and need no API key. Real model, browser, and MCP server checks require explicit integration runs; the browser integration tests are opt-in.

## License

MIT. The Jev browser reader's third-party attribution remains in `coderill/browser_NOTICE.txt`.
