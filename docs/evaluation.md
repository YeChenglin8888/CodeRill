# CodeRill evaluation

Keep three results separate: offline behavior tests, model task outcomes, and Harbor verifier rewards. A passing unit test checks a mechanism; it is not a task success rate.

## Built-in smoke and accumulating critical sets

The repository includes six short model tasks in `evals/smoke.json` and eight critical seed tasks in `evals/critical.json`. They cover extraction, aggregation, fixes, command execution, multi-file changes, edit scope, data preservation, numeric boundaries, cwd independence, config preservation, untrusted repository instructions, path containment, and atomic batch updates. The seeds are synthetic, not claims of observed production incidents.

```sh
python -m pytest tests/test_eval_datasets.py tests/test_benchmark_suite.py -q
python -m examples.benchmark_suite evals/smoke.json --output evals/runs/smoke.json
python -m examples.benchmark_suite evals/critical.json --output evals/runs/critical.json
```

The first command checks the fixtures and verifiers offline; the other two call the configured model. Both sets require all tasks to pass. A report is written before the runner exits nonzero on any failed, unverified, or infrastructure-error task. `pass_rate` retains the all-attempts denominator, including infrastructure errors. Reports include effective model/budget settings and case metadata; preserve the repository revision and fixture/verifier snapshots separately. See [the dataset guide](../evals/README.md) for bounded runs, case coverage, the critical-case intake template, and verification limits.

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

Install Harbor in a separate Python 3.12+ evaluation environment, as described by the [official Harbor quickstart](https://github.com/harbor-framework/docs/blob/main/quickstart.mdx). The task image must have Python 3.10+ with `pip`; this adapter installs dependencies into an isolated target directory and does not install an interpreter. Set `CODERILL_API_KEY`, `CODERILL_MODEL`, `CODERILL_PROVIDER` (`openai` or `anthropic`), and optional `CODERILL_BASE_URL` in the host environment or the project-root `.env` file. The runner passes variable references to Harbor, never the key value in command arguments.

For the official DeepSeek API, set `CODERILL_PROVIDER=openai`, `CODERILL_MODEL=deepseek-flash` (or `deepseek-v4-pro`), and `CODERILL_BASE_URL=https://api.deepseek.com`. CodeRill enables thinking mode and persists each assistant response's `reasoning_content` so subsequent tool requests can replay it. Start a new session when switching an earlier non-thinking DeepSeek conversation to this behavior.

Run the preflight first, then one task in the later evaluation session:

```sh
python -m examples.harbor_run preflight
python -m examples.harbor_run run --task chess-best-move --output harbor-runs/coderill-smoke
python -m examples.harbor_run report --output harbor-runs/coderill-smoke
```

Preflight checks the local package, Harbor CLI, Docker daemon, and model variables without calling the provider. The runner requests one attempt with one concurrent task. Choose a task whose input format the configured model can inspect; `chess-best-move` uses a PNG board and is unsuitable for a text-only model. `report` reads trial `result.json` files beneath Harbor job directories offline and counts explicit positive rewards, explicit zero rewards, missing rewards, timeouts, and the phase at which a trial stopped. A missing reward never enters the explicit-reward denominator. Keep the run directory, dataset/task, model settings, and Harbor version with the report. A local text-only Harbor smoke task passed with reward 1/1; this is not a public benchmark success rate.

Report the denominator, model, task set, verifier, permitted feedback attempts, completion failures and infrastructure errors alongside any future success rate.
