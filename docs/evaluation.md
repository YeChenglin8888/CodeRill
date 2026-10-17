# CodeRill evaluation

Keep three results separate: offline behavior tests, model task outcomes, and Harbor verifier rewards. A passing unit test checks a mechanism; it is not a task success rate.

## Fixed local manifest

`examples/benchmark_suite.py` copies each task workspace into a temporary directory, runs one non-interactive CodeRill turn, then runs explicit verifier commands. A task without verifiers is `unverified`, not passed. Invalid setup and verifier failures have different outcomes.

```json
[
  {
    "id": "sample-fix",
    "workspace": "fixtures/sample-project",
    "prompt": "Fix the failing test without changing the test.",
    "verify": [["python", "-m", "pytest", "-q"]]
  }
]
```

Run `python -m examples.benchmark_suite tasks.json --output run.json` after configuring the model provider. Preserve the exact task files, repository commit, model, provider, budgets, approval policy and output report when comparing runs. The runner does not read hidden tests or reuse a modified task workspace across cases.

## Optional Harbor adapter

`examples.harbor_agent:CodeRillHarborAgent` implements Harbor's installed-agent interface. It stages only `pyproject.toml`, `README.md` and `coderill/`, installs them in the task container, and invokes the CLI once with benchmark completion checks in the task container's work directory. The adapter does not inspect Harbor verifier files. Its local test checks source staging and instruction quoting with a fake Harbor interface; it does not start Docker or call a model.

Install Harbor in a separate Python 3.12+ evaluation environment, as described by the [official Harbor quickstart](https://github.com/harbor-framework/docs/blob/main/quickstart.mdx). The task image must have Python 3.10+ with `venv` and `pip`; this adapter does not install an interpreter. Set `CODERILL_API_KEY`, `CODERILL_MODEL`, `CODERILL_PROVIDER` (`openai` or `anthropic`), and optional `CODERILL_BASE_URL` in the host environment or the project-root `.env` file. The runner passes variable references to Harbor, never the key value in command arguments.

For the official DeepSeek API, set `CODERILL_PROVIDER=openai`, `CODERILL_MODEL=deepseek-flash` (or `deepseek-v4-pro`), and `CODERILL_BASE_URL=https://api.deepseek.com`. CodeRill enables thinking mode and persists each assistant response's `reasoning_content` so subsequent tool requests can replay it. Start a new session when switching an earlier non-thinking DeepSeek conversation to this behavior.

Run the preflight first, then one task in the later evaluation session:

```sh
python -m examples.harbor_run preflight
python -m examples.harbor_run run --task chess-best-move --output harbor-runs/coderill-smoke
python -m examples.harbor_run report --output harbor-runs/coderill-smoke
```

Preflight checks the local package, Harbor CLI, Docker daemon, and model variables without calling the provider. The runner requests one attempt with one concurrent task. `report` reads trial `result.json` files beneath Harbor job directories offline and counts explicit positive rewards, explicit zero rewards, missing rewards, timeouts, and the phase at which a trial stopped. A missing reward never enters the explicit-reward denominator. Keep the run directory, dataset/task, model settings, and Harbor version with the report. No CodeRill Harbor reward has been produced here.

Report the denominator, model, task set, verifier, permitted feedback attempts, completion failures and infrastructure errors alongside any future success rate.
