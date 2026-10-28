# CodeRill

CodeRill is an open-source Python coding agent for working in a repository through a terminal or local web interface. It can inspect code, search files, make reviewed changes, run commands and use the results to continue a task. Its readable runtime also makes it a useful starting point for studying coding agents.

Long tasks are its focus: CodeRill records an append-only session log, supports review before changes, manages context limits, retrieves project memories and resumes after interruptions. The CLI, optional Textual interface and local web interface share the async runtime.

[中文说明](README.zh-CN.md) · [Architecture and failure semantics](docs/architecture.md) · [Evaluation](docs/evaluation.md) · [Harness² refinement](docs/harness2.md) · [Implementation status](docs/implementation-status.md)

Project-scoped memory, local retrieval setup, and management commands are documented in [Project memory](docs/memory.md).

## Latest changes

- **Project memory:** extract source-backed decisions and lessons, search across sessions, and use `/memory` to inspect, correct, delete or index records. Local semantic retrieval is optional; lexical search remains available without the model.
- **Capability routing:** search MCP tools and Skills with local ranking and optional configured embeddings; qualified light-model routing is available for selected read-only work.
- **Harness² refinement:** compare task runs, preserve evidence and reusable harnesses, and use independent evaluation before interactive adoption.
- **Tools and safety:** optional Windows desktop control, Docker command sandboxing, durable approvals and task recovery.
- **Evaluation:** synthetic routing and memory fixtures complement executable coding tasks; model, cost and verifier results should be reported separately from offline tests.

These changes are in the source checkout. The PyPI command below installs the latest published package, which may not include unreleased features. See [CHANGELOG](CHANGELOG.md) and [evaluation](docs/evaluation.md) for details and limitations.

## Quick start with pipx

With Python 3.10+ and [pipx](https://pipx.pypa.io/latest/how-to/install-pipx.html) installed, install the CLI from [PyPI](https://pypi.org/project/coderill/) in one command:

```bash
pipx install coderill
```

On first run, CodeRill copies its bundled environment template to `~/.coderill/.env`; uncomment `OPENAI_API_KEY` and fill in your key there, or set it in your environment. If the key is missing, CodeRill prints that path and the required variable. Then run `coderill "explain the failing tests"` from the directory you want to work on. The default workspace is the current directory. Use `coderill --help` to see more options. The core CLI needs no optional extras; TUI and MCP dependencies are described below.

## Local Docker deployment exercise

Run docker compose up -d --build --wait from the checkout and open http://127.0.0.1:18765/ . A sample workspace is bind-mounted and sessions use a named volume. The default placeholder key supports deployment checks only.

Run python scripts/check_docker.py --recreate when the demo has no active Agent task to verify persistence across container replacement, without calling a model. See the [deployment and interview guide](docs/docker-deployment.md) for configuration and the single-user scope.

## Install and run

### Optional Docker sandbox (source checkout)

Sandboxing is off by default. Build both local images and start Docker before enabling it:

```bash
docker build -f sandbox/Dockerfile --target sandbox -t coderill-sandbox:local .
docker build -f sandbox/Dockerfile --target gateway -t coderill-sandbox-gateway:local .
coderill --cwd /path/to/project --sandbox docker "run tests"
```

The Docker backend confines Shell, diagnostics, Git, and background processes to the selected workspace. It allows HTTPS to npm and PyPI domains by default; additional `host:443` targets require a one-command or active-session review. Use `--sandbox-profile read-only` or `--sandbox-network none` to tighten access. Configure protected workspace paths in `~/.coderill/config.toml`, for example `deny_read_paths = [".env"]`. For a local dev server, configure `--sandbox-port 5173:5173` and request `ports: [5173]` when starting the background process. Failures never retry a command on the host. File tools use application-level path checks; hooks, MCP, browser and existing web tools run outside this command sandbox. Windows Docker Desktop still needs a live validation pass for Unicode paths, sensitive-file masks and preview forwarding.

Python 3.10 or newer is required. For a local checkout:

```bash
pip install -e ".[tui,mcp]"
export OPENAI_API_KEY=...       # or set CODERILL_API_KEY / ANTHROPIC_API_KEY
coderill                     # interactive CLI
coderill "fix the test"      # one request
coderill --message "fix it"  # equivalent one-request form
coderill --tui               # optional Textual interface
```

If installed in the project's `.venv` on Windows, run `.\.venv\Scripts\coderill.exe` from the repository root in PowerShell. The bare `coderill` command requires an activated virtual environment or its `Scripts` directory on `PATH`. The current directory is the default workspace; use `--cwd` to choose another one.

On first run, CodeRill copies its bundled template to `~/.coderill/.env` (on Windows, `$HOME\.coderill\.env`). It also fills an existing empty file, while preserving any file with content. Add your key there to use CodeRill from any directory. Shell environment variables take precedence over a project `.env`, which takes precedence over the user-level `.env`. An OpenAI-compatible endpoint can be selected with `OPENAI_BASE_URL`; native Anthropic Messages uses `--provider anthropic` and `ANTHROPIC_API_KEY`.

```bash
coderill --cwd path/to/project --provider anthropic "explain this module"
coderill --attach screenshot.png --message "what is wrong here?"
coderill --list
coderill --resume SESSION_ID
```

For the local TypeScript browser UI, run `npm install && npm run build` in `web/`, then run `python -m coderill.app.web --cwd .` from the repository root and open `http://127.0.0.1:8765`. For UI development, run `npm run dev` in `web/` and open `http://127.0.0.1:5173`. The browser client reuses the CLI runtime for sessions, models, permissions, plans, attachments, cost, sharing, undo, MCP, background tasks, child worktree application, streaming, approvals, cancellation, and recovery. The `/` button lists CLI commands.

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

Select a profile in the REPL with `/model claude` or in the TUI model selector. Use `/mode` or Shift+Tab to cycle through manual approval, `acceptEdits` (built-in file changes only), read-only plan mode, and full access (`/mode extreme`, also `/mode bypass`), which skips tool approval prompts. `/mode dontAsk` denies unapproved actions. The same modes can be selected at startup with `--permission-mode` using Claude Code's names (`default`, `acceptEdits`, `plan`, `dontAsk`, `bypassPermissions`). Shell commands still ask in `acceptEdits`; remembered file-edit approval lasts only for the current session. Existing commands include `/new`, `/list`, `/resume`, `/fork`, `/share`, `/where`, `/cost`, `/plan`, `/approve`, `/mode`, `/undo`, `/reasoning`, `/mcp`, and `/exit`. New commands are `/attach`, `/model`, `/background`, and `/apply`.

In the TUI, Enter sends a message and Shift+Enter adds a line. Paste a file path on its own line or use `/attach path` to stage a file; F8 stages a PNG image from the system clipboard. The attachment list is shown above the composer until the turn is accepted.

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

A pending review records its ID, tool arguments, diff, target path, and file digest. If the file changes before approval, CodeRill creates a new review. Tool calls started before a crash are recorded as result-unknown and are not automatically replayed. Old v1 sessions remain readable; new events use v2. Python callers use the async `AgentRuntime`; the former synchronous `Agent` API has been removed.

Read-only tool calls in a consecutive batch run concurrently. Writes stay in order. Background child agents use independent sessions and budgets. A writable child runs in a Git worktree; `/apply CHILD_ID` previews and explicitly applies its diff. A worktree isolates the working directory, not the operating system.

See [architecture.md](docs/architecture.md) for the request sequence, approval state diagram, recovery details, and links from capabilities to tests.

## Tools and integrations

Built-in tools read, batch-read, write, edit, patch, and delete workspace files; search by filename and content; inspect Git status, diffs, and history; run shell commands and diagnostics; fetch public pages and search the web; manage a task plan; load skills on demand; retrieve archived output; and delegate bounded tasks. File paths are constrained to the workspace. Patches and deletion require review even in aggressive mode. Network tools reject local or private addresses and do not follow redirects. Shell and diagnostic commands have timeout, cancellation, and output limits.

Optional MCP uses the official Python SDK for stdio and Streamable HTTP servers. Server configuration is read from `~/.coderill/mcp.json`. Jev can also make typed choices in ordinary coding sessions: install `.[jev]`, set `TYPESAFE_API_KEY` and `CODERILL_JEV_DECISIONS=1`, then use the `jev_decide` tool when a fixed set of alternatives needs ranking. [Harness² refinement](docs/harness2.md) can use Jev to select the final artifact with `--decision-model jev`. The browser tool remains optional via `.[browser]` and `CODERILL_BROWSER_ENABLED=1`; its DOM reader is adapted from `browser-use/jev-ultrafast` and retains [its license notice](coderill/browser_NOTICE.txt).

MCP tools and Skills can be found by name or description through `mcp_tool_search` and `skill_search`. Optional routing settings belong in the user-level `~/.coderill/config.toml`; project configuration cannot set an external search address. Set `routing.enabled = false` to restore the previous MCP search. Set `[routing.embedding]` with `base_url`, `model`, and `api_key_env` to enable OpenAI-compatible semantic search; the service receives the search query (up to 2,000 characters) and capability names and descriptions, but no additional conversation or file content. A missing or failed service falls back to local search. An optional `light_profile` also requires a matching qualification report before it can handle task-boundary classification or read-only child work. See [routing evaluation](docs/evaluation.md#routing-retrieval-checks).

Windows desktop computer use is optional: install `.[computer]`, set `CODERILL_COMPUTER_ENABLED=1`, `CODERILL_VISION=1`, and `TYPESAFE_API_KEY`, then choose a vision-capable model. The `computer_use` tool captures the primary display and supports screenshots, clicks, dragging, scrolling, ASCII typing, keys, and short waits. Each call follows the current approval mode, including screenshots, and sends the new screen to the model. When several fixed next actions are plausible, the agent uses `jev_decide` to choose one. This runs on the host desktop, outside the Docker sandbox; keep private windows closed before approving a screenshot. Primary-display coordinates are used for input.

## Offline verification

```bash
pip install -e ".[dev,tui,mcp]"
pytest tests/ -q
ruff check .
python examples/failure_demo.py
python -m pip wheel . --no-deps --wheel-dir dist
```

CI runs tests, Ruff, CLI import/help, and wheel building on Linux and Windows. Core scenarios use fakes and need no API key. Real model, browser, and MCP server checks require explicit integration runs; the browser integration tests are opt-in.

Harbor preflight, the single-task command, and offline result reporting are documented in [docs/evaluation.md](docs/evaluation.md). No real Harbor task result is claimed by the offline suite.

## License

MIT. The Jev browser reader's third-party attribution remains in `coderill/browser_NOTICE.txt`.
