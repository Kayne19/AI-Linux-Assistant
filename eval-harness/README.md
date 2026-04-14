# Eval Harness

`eval-harness` is a standalone, generic-first scaffold for environment-grounded assistant evaluation.

V1 scope:
- AWS-first sandbox execution.
- SSM-only instance control.
- OpenClaw as the first sandbox controller implementation.
- AI Linux Assistant as the first solver adapter implementation.
- File-backed artifact capture for reproducible post-run analysis.

Out of scope for the core project:
- canonical scoring
- mandatory judge model selection
- product-specific memory seeding shortcuts

The core harness owns:
- scenario loading and validation
- staging setup and environment verification
- variant orchestration
- raw artifact capture
- cleanup metadata
- grading plugin hooks

The core harness does not require a built-in grader. An example grading plugin is included as a reference only.

## Project Layout

```text
eval-harness/
  infra/aws/                  Operator docs and systemd templates
  src/eval_harness/           Python package
  tests/                      Harness tests
```

## Scenario Contract

Runnable scenarios must include:
- a target image/runtime identifier
- setup steps
- at least one broken-state verification check
- at least one resolution verification check
- an opening user message
- a positive turn budget
- at least one variant

The generic scenario contract intentionally avoids product-specific fields such as direct database memory seeding. Adapter-specific transforms belong in the adapter layer.

## Plugin Boundary

Artifact capture is part of the core.
Scoring is not.

`plugins/example_grader.py` demonstrates how a consumer can read stored artifacts and emit optional metrics without changing the orchestrator or artifact schema.

## AI Linux Assistant Notes

The included HTTP adapter is designed for the current AI Linux Assistant durable run API:
- it captures `POST /chats/{chat_id}/runs` results and `/runs/{id}/events`
- it treats durable run events as the source of truth for artifacts
- it avoids inventing a fake static service token flow

If the target deployment enforces bearer auth, provide bearer tokens to the adapter. Per-variant credentials are supported so concurrent variant runs are not forced onto one user account.

## CLI

Validate a scenario:

```bash
cd eval-harness
python -m eval_harness validate-scenario examples/scenarios/nginx_service_repair.json
```

Dry-run the dependency wiring:

```bash
python -m eval_harness run-group \
  examples/scenarios/nginx_service_repair.json \
  --config examples/aws_ai_linux_assistant_config.json \
  --group-id demo-group \
  --dry-run
```

Run one group for real:

```bash
python -m eval_harness run-group \
  examples/scenarios/nginx_service_repair.json \
  --config examples/aws_ai_linux_assistant_config.json \
  --group-id demo-group \
  --artifacts-root artifacts
```

Run the optional example grader:

```bash
python -m eval_harness grade-artifact artifacts/demo-group/artifact-pack.json --artifacts-root artifacts
```

## Config Shape

The `run-group` command expects a JSON config with three top-level sections:

- `backend`: AWS EC2/AMI/SSM settings
- `controller`: OpenClaw transport settings
- `adapter`: AI Linux Assistant HTTP settings

String values may reference environment variables with `env:VAR_NAME`.

See [examples/aws_ai_linux_assistant_config.json](/home/kayne19/projects/AI-Linux-Assistant/eval-harness/examples/aws_ai_linux_assistant_config.json) for a concrete template.
