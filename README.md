```
  ____            __         ____    _      _      _
 / ___   ___    __| |   ___  |  _ \  (_)    | |    | |
| |      / _ \  / _  |  / _ \ | |_) |  | |    | |    | |
| |___  | (_) | | (_| | |  __/ |  _ <  | |    | |    | |
 \____  \___/  \__,_|  \___| |_| \_\  |_|    |_|    |_|
```

> **A coding agent small enough to actually read.**

Point it at a project, describe what you want, and it reads, writes, edits and runs
code on its own. In standard mode, it asks before running tools that change files,
run commands, or access the network.

[English](README.md) · [中文](README.zh-CN.md)

[![python](https://img.shields.io/badge/python-3.10%2B-3776ab)](https://www.python.org/)
[![CI](https://github.com/YeChenglin8888/CodeRill/actions/workflows/ci.yml/badge.svg)](https://github.com/YeChenglin8888/CodeRill/actions/workflows/ci.yml)
[![license](https://img.shields.io/badge/license-MIT-2ea44f)](LICENSE)

---

## The pitch

Coding agents are not scarce. Coding agents you can *read* are.

CodeRill is a few thousand lines of Python with focused tests. You can open
`agent.py` and follow a whole turn — build the request, stream
the reply, run the tools, loop — without crawling through five layers of
abstraction. Each subsystem is a handful of files, and each file is named after
the one thing it does.

That is the whole idea. Small is the feature, not the excuse.

```console
you > there's a bug in utils.py — parse() crashes on empty input

allow edit_file? {"file_path": "utils.py", ...}
--- a/utils.py
+++ b/utils.py
-    return json.loads(text)
+    return json.loads(text) if text else {}
  [y]es / [n]o / [a]lways > y

→ edit_file (file_path="utils.py")
→ bash (command="python -m pytest tests/test_utils.py")

Fixed. parse() returns {} on empty input now instead of raising.
```

## How it works

The agent is one loop. Each turn it streams a reply from the model; if the reply
asks for tools, the agent runs them and feeds the results back, then loops. When
the model answers without asking for a tool, the turn is over.

```
        user message
             |
             v
   +----------------------+
   | compress + build the |  <-- context.py, prompt.py, skills.py
   | request (skills, plan,|
   | todo, system prompt) |
   +----------------------+
             |
             v
   +----------------------+
   |  stream model reply  |  <-- llm.py (streaming + retry + deadlines)
   +----------------------+
             |
      tool calls?
        /        \
      yes         no --> done
       |
       v
   +-------------------------------------------------+
   |  hooks -> consent -> run tools -> post hooks     |  <-- agent.py
   |  (read-only tools run in parallel)               |
   +-------------------------------------------------+
             |
      append results to the session log, loop
```

## Decisions I'd defend in a code review

- **File tools stay in the workspace.** Their paths are resolved against the
  project root and rejected if they land outside — the check lives in one place,
  not in each tool. Shell commands are not sandboxed.
- **Consent is code, not a prompt.** Mutating calls go through a policy that
  classifies the action and either matches a saved grant or asks you, showing a
  diff for file writes. "Always" saves a *scoped* grant — a path, a command
  prefix, a host — never an open-ended yes.
- **The session log is append-only.** Compression writes a checkpoint and leaves
  the earlier events in place, so the audit trail survives and a session can be
  replayed, forked, or shared without touching the durable history.
- **Tool failures are data, not crashes.** A failed tool returns an error string
  the model can read and recover from, instead of unwinding the loop.
- **Read-only tools run in parallel; writes do not.** Concurrency where it is
  free, ordering where it matters.

## What's in the box

| File | Responsibility |
| --- | --- |
| `coderill/agent.py` | The loop: turns, parallel tools, hooks, consent, sub-agents, plan reconciliation. |
| `coderill/llm.py` | Model client: streaming, tool-call reassembly, retry, deadlines, usage. |
| `coderill/tools/` | The tools and the registry that dispatches them. |
| `coderill/permissions.py` | Consent: classification, modes, scoped persistent grants. |
| `coderill/review.py` | The trusted pre-write diff shown before a file changes. |
| `coderill/hooks.py` | User shell hooks around tool calls. |
| `coderill/skills.py` | File-based skills discovered and loaded on demand. |
| `coderill/taskplan.py` | The task plan, with revision-based concurrency. |
| `coderill/context.py` | Three-layer context compression. |
| `coderill/session.py` | Append-only JSONL event log, replay, fork and share. |
| `coderill/checkpoints.py` | Undo for the file-writing tools. |
| `coderill/mcp.py` | A distilled MCP stdio client. |
| `coderill/browser.py` | The optional Jev browser-task loop. |
| `coderill/cli.py` | The terminal interface. |
| `coderill/config.py` | Environment and settings. |

## The tools

- **read_file** / **write_file** / **edit_file** — read with line numbers, write,
  and replace an exact unique string (returning a unified diff).
- **bash** — run a shell command from the workspace root (Git Bash on Windows).
- **glob** / **grep** — find files by pattern, search contents by regex.
- **todo_write** — the agent's lightweight checklist.
- **task_list** / **task_create** / **task_update** / **task_revise** — a real task
  plan with stable IDs and revisions.
- **load_skill** — pull a skill's instructions into the conversation.
- **agent** — delegate a self-contained sub-task to a fresh sub-agent.
- **browser_task** — hand a whole web task to the Jev model (optional).

## Install

```bash
git clone https://github.com/YeChenglin8888/CodeRill
cd CodeRill
pip install -e .
cp .env.example .env      # then set OPENAI_API_KEY
```

Any OpenAI-compatible endpoint works — set `OPENAI_BASE_URL` for DeepSeek,
Moonshot, a local vLLM server, or whatever you use.

## Use

```bash
coderill                               # interactive session
coderill "add type hints to llm.py"    # one-shot
coderill --cwd ~/myproject             # run against another directory
coderill --plan                        # start in plan mode (read-only)
coderill --auto-approve                # no consent prompts
coderill --list                        # list saved sessions
coderill --resume 20260101-1200        # resume one
```

Inside the REPL: `/help`, `/new`, `/list`, `/resume <id>`, `/where`, `/cost`,
`/undo`, `/share`, `/fork`, `/plan`, `/approve`, `/mode`, `/reasoning`, `/mcp`,
`/exit`.

## Skills

Drop a `SKILL.md` (or a `<name>.md`) into `<project>/.agents/skills/`,
`<project>/skills/`, `~/.agents/skills/` or `~/.coderill/skills/`. The model sees
only each skill's name and one-line description; the body enters the conversation
only when it calls `load_skill`. Project skills override global ones of the same
name.

## Sessions

A conversation is a JSONL event log under `~/.coderill/sessions/<id>.jsonl`. The
message list is a projection replayed from that log, so you can resume it, `/fork`
it into a branch, or print a redacted `/share` transcript — none of which touch
the durable history. Task-plan updates are also logged, so resume and fork keep
the plan and its revision.

## The Jev browser task (optional)

`browser_task` delegates a whole web task to TypeSafe's Jev model, a sub-second
"System 1" decision model that returns typed choices instead of text. CodeRill
drives a real browser with Playwright, asks Jev for the next operation, and
returns JSON page evidence plus a stop reason; `model_done` is Jev's claim, not
independent verification.

Its DOM reader (`browser_snapshot.js`) is adapted from `browser-use/jev-ultrafast`;
see [`coderill/browser_NOTICE.txt`](coderill/browser_NOTICE.txt) for the MIT notice
and reference commit. Install with `pip install -e ".[browser]"`, then set
`CODERILL_BROWSER_ENABLED=1` and the `TYPESAFE_*` variables.

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `OPENAI_API_KEY` | — | Required. |
| `OPENAI_BASE_URL` | OpenAI | Any compatible endpoint. |
| `CODERILL_MODEL` | `gpt-4o-mini` | Model name. |
| `CODERILL_MAX_TOKENS` | `4096` | Per-response cap. |
| `CODERILL_TEMPERATURE` | `0` | Sampling temperature. |
| `CODERILL_MAX_TURNS` | `30` | Tool rounds per user turn. |
| `CODERILL_MAX_CONTEXT` | `128000` | Context budget before compression. |
| `CODERILL_BROWSER_ENABLED` | `0` | Register the browser tool. |
| `CODERILL_BROWSER_CHANNEL` | `msedge` | `msedge` or `chrome`. |
| `TYPESAFE_API_KEY` | — | Key for the Jev browser model. |
| `TYPESAFE_MODEL` | `jev-latest` | Jev model name. |

State lives in `~/.coderill/`: `hooks.json`, `mcp.json`, `permissions.json`,
`skills/`, `sessions/`.

## Tests

```bash
pip install -e ".[dev]"
pytest tests/ -q       # offline: no network, no API key
ruff check .
```

GitHub Actions runs these checks on Linux and Windows. The six real-browser
integration tests are skipped unless explicitly enabled.

The loop, tools, permissions, hooks, skills, task-plan, context, review and
session tests use fakes, so the suite runs without a network. Browser tests run
against a fake browser and fake Jev responses; the real-browser checks are opt-in
via `CODERILL_BROWSER_TEST=1`.

## What's next

- A provider layer with a first-class Anthropic adapter (today: any
  OpenAI-compatible endpoint via `OPENAI_BASE_URL`).
- Sandboxed command execution and multimodal attachments.
- A Textual TUI as a client of the same runtime.

## Acknowledgements

The Jev browser module adapts DOM-reading code from
[`browser-use/jev-ultrafast`](https://github.com/browser-use/jev-ultrafast) (MIT) —
the notice ships with the JS, in
[`coderill/browser_NOTICE.txt`](coderill/browser_NOTICE.txt).

## License

MIT — see [LICENSE](LICENSE).
