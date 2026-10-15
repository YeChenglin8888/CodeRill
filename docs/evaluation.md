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

`examples.harbor_agent:CodeRillHarborAgent` implements Harbor's installed-agent interface. It stages only `pyproject.toml`, `README.md` and `coderill/`, installs them in the task container, and invokes the CLI once with the task instruction in the container's work directory. The adapter does not inspect Harbor verifier files. Its local test checks source staging and instruction quoting with a fake Harbor interface; it does not start Docker or call a model.

Install Harbor in a separate Python 3.12+ evaluation environment, as described by the [official Harbor quickstart](https://github.com/harbor-framework/docs/blob/main/quickstart.mdx). The task image must have Python 3.10+ with `venv` and `pip`; this adapter does not install an interpreter. Configure `CODERILL_API_KEY`, `CODERILL_MODEL` and any provider endpoint through Harbor's agent environment, then select the adapter with `--agent examples.harbor_agent:CodeRillHarborAgent`. A Harbor result requires an actual dataset, task image and verifier run; no CodeRill Harbor reward has been produced here.

Report the denominator, model, task set, verifier, permitted feedback attempts, completion failures and infrastructure errors alongside any future success rate. FirstCoder's published or local rewards must never be attributed to CodeRill.
