# Interview walkthrough: reliable long-running coding tasks

## The problem

A coding agent needs more than a model loop: it must keep its place across context limits, approval delays, failed tools, and process restarts. CodeRill makes the session log the durable fact and rebuilds running state from it.

## The five-minute demonstration

Run `python examples/failure_demo.py` from an editable install. It needs no API key. Explain the four printed steps:

1. A proposed file write pauses with a persisted review ID and diff.
2. Someone edits the file while approval is pending. The old review is invalidated and a new ID and diff are produced.
3. Approving the current review applies the write and records the result.
4. A simulated process exit leaves a started tool without a result. Replay marks it `unknown` and asks the model to inspect reality; it does not replay the write.

Then show a session JSONL file and `docs/architecture.md`. In a live CLI or TUI run, `--resume` loads that projection, `/background` shows child tasks, and `/apply <child-id>` reviews a worktree diff before merging it into the parent workspace.

## Design decisions and tradeoffs

- **Provider boundary:** shared request/response/events make two vendors and fake tests use the same runtime. Adapters keep SDK-specific shapes private. Real API behavior still needs separate opt-in checks.
- **Event log:** replay provides resume, fork, audit, and pending review recovery. A partial last line is recoverable; middle corruption is fatal. External effects cannot be made exactly once by a JSONL log alone.
- **Approval:** a diff plus file digest is stored with the original call. Approval is checked against the current file. This catches edits between proposal and approval; concurrent mutation during the final file write is still a filesystem race, not a transaction.
- **Context:** old output is archived before trimming; the model can request archived content later. The budget is an estimate, so the runtime has one provider-overflow recovery path. Summary failure falls back to deterministic trimming.
- **Concurrency:** adjacent read-only calls can overlap; writes are sequenced. A task plan uses revision checks for atomic updates. Child agents have their own budgets and worktrees; a worktree is isolation for changes, not a security sandbox.

## What to claim accurately

The offline suite verifies state transitions, provider adapters with fake clients, storage, approval, context, and delegation behavior. It does not establish live API reliability, browser compatibility, or operation-system security isolation. Use the reproducible source and test counts in the final implementation report rather than treating line count as a quality measure.
