# CodeRill evaluation

Keep three results separate: offline behavior tests, model task outcomes, and Harbor verifier rewards. A passing unit test checks a mechanism; it is not a task success rate.

## Routing retrieval checks

Run `python -m examples.routing_eval evals/routing.json --output output/routing-eval.json` to compare current MCP and Skill search with the previous MCP lexical search on a fixed synthetic catalogue. The report includes the dataset hash, 50 queries per category, macro Recall@8, negative-query hit rate and mean search latency. Add `--embedding-config PATH` with a JSON file containing `base_url`, `model`, and `api_key_env` to include a configured OpenAI-compatible embedding service. Do not treat this retrieval score as a coding-task success rate. For task quality, run `examples.benchmark_suite` twice with `--routing off` and `--routing on`, keeping the same model, task data, verifiers and budgets; record the pass-rate denominator and both reports. The off run is a same-revision routing toggle comparison, not a historical source revision.

`python -m examples.routing_qualification evals/routing_qualification.json --light-profile NAME --output output/routing-qualification.json` makes live provider calls to compare the selected light profile with the current primary model on 100 independent read-only cases and 50 task-boundary cases. Its report binds the dataset hash, provider, service address and profile settings. Only a passing report referenced by user-level `routing.qualification_report` enables automatic light-model selection. The bundled cases are synthetic extraction and boundary examples; passing them does not establish quality on real coding tasks. Before relying on the profile for production, replace or supplement the fixture with held-out representative tasks and compare end-to-end verifier outcomes.

On 2026-10-27, the bundled retrieval fixture (`output/routing-eval.json`) reached MCP Recall@8 0.94 and Skill Recall@8 0.96, each over 50 queries. Negative-query hit rate was 0.20 in each category. A same-revision `--routing off` / `--routing on` comparison on `evals/smoke.json` (SHA-256 `34a80b4e9badf4d5ddbc52b9e30e25ce172dd1e65d37376c1ad5521b8d0e1ce2`) used `deepseek-flash`, provider `openai`, `--no-docker`, the same verifier and matching settings. Both runs passed 6/6 tasks: 0 percentage-point decline. Off used 67,368 input and 9,851 output tokens over 82.89 case seconds; on used 69,397 input and 9,696 output tokens over 82.02 case seconds. The two reports are `output/routing-smoke-off.json` and `output/routing-smoke-on.json`. These six coding tasks do not exercise external embeddings or a qualified light model; neither was configured for this run.

## Memory retrieval checks

`python -m examples.memory_benchmark evals/memory.json --output output/memory-eval.json` compares memory-off, lexical, hybrid, and update-aware retrieval on a small synthetic set. It reports retrieval hits, stale hits, elapsed time and whether the semantic model was loaded; it does not run coding agents or establish task success. Hybrid modes use BGE-M3 only when the optional model is installed locally, otherwise they use lexical retrieval. See [project memory](memory.md) for setup and provenance.

For claims about coding improvement, use isolated multi-session tasks with executable verifiers, a separate holdout set and matching models and budgets. VibeMemBench and DreamBench-SWE provide relevant public benchmark designs; no results for those benchmarks are bundled here.

`examples.benchmark_suite` also accepts `--memory-mode off|lexical|hybrid|lifecycle` for executable tasks containing a `memory_history` list. For example, run `python -m examples.benchmark_suite evals/memory_tasks.json --memory-mode lifecycle --no-docker --output output/memory-task.json` and repeat with the other modes. These fixtures seed prior-session evidence; they do not measure automatic extraction. The included task is synthetic and too small to support a success-rate claim. Model calls require the configured provider, and the same task files, model and budgets must be used in every condition.

## Built-in smoke and accumulating critical sets

The repository includes six short model tasks in `evals/smoke.json` and eight critical seed tasks in `evals/critical.json`. They cover extraction, aggregation, fixes, command execution, multi-file changes, edit scope, data preservation, numeric boundaries, cwd independence, config preservation, untrusted repository instructions, path containment, and atomic batch updates. The seeds are synthetic, not claims of observed production incidents.

```sh
python -m pytest tests/test_eval_datasets.py tests/test_benchmark_suite.py -q
python -m examples.benchmark_suite evals/smoke.json --output evals/runs/smoke.json
python -m examples.benchmark_suite evals/critical.json --output evals/runs/critical.json
```

The first command checks the fixtures and verifiers offline; the other two call the configured model. Both sets require all tasks to pass. A report is written before the runner exits nonzero on any failed, unverified, or infrastructure-error task. `pass_rate` retains the all-attempts denominator, including infrastructure errors. Reports include effective model/budget settings and case metadata; preserve the repository revision and fixture/verifier snapshots separately. See [the dataset guide](../evals/README.md) for bounded runs, case coverage, the critical-case intake template, and verification limits.

To evaluate a Harness² result on the same fixed task suite, pass its selected `harnesses/<choice>` directory with `--harness`. The runner binds all eight harness components in a fresh copy for each case and records the harness content hash in the report. Compare a run without `--harness` against one with it using the same manifest, model, and budgets. Use cases other than the one that produced the harness when measuring transfer; `coderill-refine --eval-manifest ... --case-id ...` uses that case's verifier during refinement and can overfit it. See [Harness² usage](harness2.md#使用).

For a bounded local comparison, `--case-id` runs one task from a manifest. `--no-docker` uses file-only Agent tools and runs trusted verifier commands on the host; the interactive CLI's `/evaluate` command uses this path when Docker is unavailable.

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
