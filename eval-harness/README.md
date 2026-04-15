# Eval Harness

`eval-harness` is a standalone benchmark harness for environment-grounded troubleshooting evaluation.

The current design target is an AWS-first, Postgres-backed workflow with four explicit phases:

1. Planner generates a scenario candidate, including sabotage steps and objective verification probes.
2. Sabotage setup runs on a staging environment until the planner approves it or the correction limit is hit.
3. The approved broken environment is cloned once per benchmark subject.
4. Blind judging grades stored transcripts after the benchmark run completes.

Golden images are now target-image driven:
- scenarios name a `target_image`
- the AWS backend resolves that alias to the newest tagged golden AMI
- if the AMI is missing, `verify-scenario` auto-builds it with Packer and prints build progress to `stderr`
- if staging setup fails after launch, the harness captures backend diagnostics before teardown so setup-run metadata includes the failure context

## Methodology

V1 benchmark flow:
- planner LLM produces the troubleshooting scenario
- planner must define both sabotage and objective verification procedures
- OpenClaw Agent A applies sabotage on staging and runs the planner’s probes
- planner reviews raw probe output and either approves or issues a correction
- if the planner has to correct sabotage twice, the setup run fails
- only planner-approved broken environments are cloned
- OpenClaw Agent B is the user proxy and does not receive sabotage details
- each subject gets its own clone and its own transcript/event stream
- scenarios, setup runs, benchmark runs, evaluation runs, and judge outputs live in Postgres
- blind judging is post hoc and separate from objective repair checks

## Architecture

Core harness logic:
- scenario validation and lifecycle orchestration
- planner-driven sabotage verification loop
- benchmark run orchestration across subject clones
- blind judge job orchestration
- Postgres persistence and JSON artifact export

Adapter-specific logic:
- AWS EC2 + SSM resource lifecycle
- OpenClaw sandbox transport and command execution
- AI Linux Assistant durable-run HTTP integration
- planner/judge model transport

Out of core:
- canonical scoring
- subject-specific weighting or composite scores
- product-specific memory shortcuts

## Persistence Model

Postgres is the source of truth.

Primary tables:
- `scenarios`
- `scenario_revisions`
- `scenario_setup_runs`
- `scenario_setup_events`
- `benchmark_subjects`
- `benchmark_runs`
- `evaluation_runs`
- `evaluation_events`
- `judge_jobs`
- `judge_items`

JSON artifact files are export-only projections built from those records.

## Scenario Contract

Runnable scenarios must include:
- stable scenario name
- title and summary
- `what_it_tests`
- target image/runtime
- observable problem statement for the user proxy
- sabotage procedure
- verification probes with objective expectations
- repair checks with objective expectations
- judge rubric
- positive turn budget

The scenario contract is generic. It does not contain AI Linux Assistant-specific memory writes or product-internal shortcuts.

## Project Layout

```text
eval-harness/
  examples/                   Config and request templates
  infra/aws/                  AWS and AMI/operator notes
  src/eval_harness/           Python package
  tests/                      Harness tests
```

## CLI

For a repo-level convenience runner that does not require installing `eval_harness` into the current Python environment, you can also use:

```bash
python ../run_eval_harness.py smoke-test
```

Other convenience commands:

```bash
python ../run_eval_harness.py init-db
python ../run_eval_harness.py generate-scenario --output /tmp/nginx_scenario.json
python ../run_eval_harness.py verify-scenario --group-id demo-setup
```

Place env vars in:

- [eval-harness/.env](/home/kayne19/projects/AI-Linux-Assistant/eval-harness/.env)

The CLI autoloads that file from the harness root.

For the public AI Linux Assistant API path:

- point `EVAL_HARNESS_AI_API_BASE_URL` at the public backend hostname, for example `https://api.<your-domain>`
- use copied Auth0 user access tokens in `bearer_tokens_by_subject` or `default_bearer_token`
- do not rely on `legacy_bootstrap_usernames_by_subject` against a public deployment

Initialize the eval-harness schema:

```bash
cd eval-harness
python -m eval_harness init-db --config examples/aws_ai_linux_assistant_config.json
```

Generate a planner draft:

```bash
python -m eval_harness generate-scenario \
  --config examples/aws_ai_linux_assistant_config.json \
  --request examples/planner_requests/nginx_recovery_request.json \
  --output /tmp/nginx_scenario.json
```

Run planner-driven scenario setup and verification:

```bash
python -m eval_harness verify-scenario \
  --config examples/aws_ai_linux_assistant_config.json \
  --request examples/planner_requests/nginx_recovery_request.json \
  --group-id demo-setup
```

Run the benchmark against all configured active subjects:

```bash
python -m eval_harness run-benchmark \
  --config examples/aws_ai_linux_assistant_config.json \
  --setup-run-id <verified_setup_run_id>
```

Run blind judging:

```bash
python -m eval_harness run-judge-job \
  --config examples/aws_ai_linux_assistant_config.json \
  --benchmark-run-id <benchmark_run_id>
```

Export a JSON artifact pack from Postgres:

```bash
python -m eval_harness export-artifact-pack \
  --config examples/aws_ai_linux_assistant_config.json \
  --benchmark-run-id <benchmark_run_id> \
  --artifacts-root artifacts
```

Validate a static scenario JSON file:

```bash
python -m eval_harness validate-scenario examples/scenarios/nginx_service_repair.json
```

## Config Shape

The main config now uses these top-level sections:
- `database`
- `backend`
- `controller`
- `planner`
- `judge`
- `subject_adapters`
- `subjects`

String values may reference environment variables with `env:VAR_NAME`.

For the `ai_linux_assistant_http` subject adapter:

- the harness talks to the backend API only, not the React frontend
- the minimum public flow is create project, create chat, create run, poll run, and poll run events
- bearer-token auth is the recommended public path

See:
- [aws_ai_linux_assistant_config.json](/home/kayne19/projects/AI-Linux-Assistant/eval-harness/examples/aws_ai_linux_assistant_config.json)
- [nginx_recovery_request.json](/home/kayne19/projects/AI-Linux-Assistant/eval-harness/examples/planner_requests/nginx_recovery_request.json)
- [nginx_service_repair.json](/home/kayne19/projects/AI-Linux-Assistant/eval-harness/examples/scenarios/nginx_service_repair.json)

For the AWS backend:
- `backend.default_target_image` is the alias used when a scenario or request does not override it
- `backend.target_images` maps each supported alias to the canonical Packer template directory and distro var-file
- `backend.golden_ami_id` remains a legacy single-image override only; prefer tagged target images
- `controller.remote_port` should match the baked OpenClaw gateway port, `18789`
