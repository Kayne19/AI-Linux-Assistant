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
- before the first command on staging or a benchmark clone, the backend verifies SSM availability on the instance
- before the verified broken-image snapshot is created, the backend signals staging teardown so it is not baked into the transient AMI
- after planner approval, setup runs enter a distinct broken-image creation phase while AWS snapshots the staging instance; during that phase the setup-run record persists the transient `broken_image_id` plus AMI state/progress metadata until the image becomes `available`
- if staging setup fails after launch, the harness captures backend diagnostics before teardown so setup-run metadata includes the failure context

## Methodology

V1 benchmark flow:
- planner LLM produces the troubleshooting scenario
- planner must define both sabotage and objective verification procedures, including any prerequisite installation or provisioning needed to create the failure
- sabotage steps are stored as raw executable shell commands or shell snippets in `sabotage_procedure`; prose labels, markdown fences, and backticks are rejected during validation
- planner rectification commands use the same sabotage-step validation before execution, so prose-like corrections fail setup before anything reaches the shell
- the ScenarioBuilderFSM applies sabotage on staging via SSM shell commands and runs the planner’s probes
- the setup agent is explicitly told it is operating inside a disposable benchmark sandbox and must not refuse bounded sabotage on generic safety grounds
- the verifier runs the exact probe commands and returns structured results; both verifier and setup-agent command execution are exercised before setup continues
- planner reviews raw probe output and either approves or issues a correction
- if the planner has to correct sabotage twice, the setup run fails
- only planner-approved broken environments are cloned
- each evaluation clone must still satisfy the scenario's `verification_probes` before the first subject turn; drifted clones fail immediately with `scenario_fidelity_failed`
- the UserProxyFSM drives the user proxy persona and does not receive sabotage details
- benchmark orchestration suppresses approval-loop leakage from subjects and treats all benchmark commands as pre-approved, but that policy is kept out of the API request content sent to the subject backend
- repair success is defined entirely by scenario-declared machine-checkable repair checks; the benchmark loop does not hard-code service-specific success rules
- repair checks may use positive and negative expectations, including exact matches, substrings, and regexes, so scenarios can define robust success conditions without teaching the orchestrator about a specific Linux subsystem
- the benchmark loop treats follow-mode command timeouts such as `tail -f` and `journalctl -f` as non-progress, and closure chatter such as "thanks, it's fixed" triggers one final objective verification instead of another full subject turn
- benchmark run status distinguishes a fully executed benchmark with subject failures (`completed_with_failures`) from infrastructure or adapter interruptions (`interrupted`)
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
- SSM-based sandbox command execution
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
- observable problem statement for planner/setup context
- canonical opening user message for benchmark turn 1, or enough information to generate and persist one during scenario design
- sabotage procedure as raw executable shell commands or shell snippets
- verification probes with objective expectations
- repair checks with objective expectations
- judge rubric
- positive turn budget

The scenario contract is generic. It does not contain AI Linux Assistant-specific memory writes or product-internal shortcuts.

Repair checks should describe the observable repaired state directly. They can combine:
- `expected_exit_code`
- `expected_substrings`
- `expected_regexes`
- `unexpected_substrings`
- `unexpected_regexes`
- `expected_exact_match`

That keeps Linux-specific knowledge in the scenario definition instead of the benchmark orchestrator.

Planner guidance should keep repair checks aligned to the real success condition:
- validate the repaired end state, not a stricter incidental administrative invariant
- include user-visible or symptom-level checks whenever the scenario has a user-visible success condition
- avoid checks that can fail on a repaired system because of missing privileges, stale runtime files, or execution context alone
- make privilege requirements explicit in the command itself when they are truly required

### Available Scenarios

Pre-built example scenarios live under `examples/scenarios/`:

- `examples/scenarios/nginx_service_repair.json` — nginx systemd unit override prevents service startup
- `examples/scenarios/ssh_config_repair.json` — invalid sshd_config directive prevents SSH daemon startup

To validate a scenario file without running it:

```bash
python -m eval_harness validate-scenario examples/scenarios/nginx_service_repair.json
python -m eval_harness validate-scenario examples/scenarios/ssh_config_repair.json
```

To pass a custom scenario JSON directly to the verify step:

```bash
python -m eval_harness validate-scenario path/to/my_scenario.json
python -m eval_harness verify-scenario --config examples/aws_ai_linux_assistant_config.json \
  --scenario path/to/my_scenario.json --group-id my-setup
```

### User Proxy Contract

The user proxy plays a human user at a Linux terminal who does not know why the machine is broken. During scenario design, the planner may use hidden scenario details to draft and self-review a realistic opening user message; that final `initial_user_message` is then persisted on the scenario revision and reused for every benchmark subject. During benchmark execution, the proxy sees only the stored opening message plus the live transcript. It never receives the sabotage procedure or repair checks during normal turns.

`observable_problem_statement` remains part of the scenario contract for planning/setup and as a fallback if first-turn generation fails, but benchmark turn 1 prefers the stored `initial_user_message` when present. If planner review updates the observable problem statement during setup, the harness keeps the stored opener in sync so later benchmark loads do not use stale text.

The proxy itself is driven by a provider-backed tool loop. OpenAI uses the Responses API, Anthropic uses Messages tool use, and Google uses Gemini function calling. When the AI subject asks the user to run a command, the proxy model can call the `run_command` function tool, the harness executes that command on the sandbox clone, and the real command output is returned to the proxy using the provider's native tool-result format. The proxy system prompt is seeded from the same visible opening user message that the subject sees, not from a more revealing hidden problem statement, which keeps the proxy context from leaking extra scenario detail.

For Phase 1 file editing, the proxy may also use bounded file tools:
- `read_file(path)` to inspect a regular UTF-8 text file
- `apply_text_edit(path, old_text, new_text)` to perform precise surgical edits

For Phase 2, if the proxy is asked to use an interactive program like `nano` or `vim`, it initiates a persistent terminal session via `tmux` and uses:
- `interactive_send(input_text?, control_keys?)` to inject literal text and/or named control keys
- `interactive_read()` to capture the current screen buffer

Interactive sessions are keyed per evaluation-run terminal and reused across follow-up proxy tool calls rather than being recreated on every send/read operation.

Those tools fail closed:
- only regular files are allowed
- binary or non-UTF-8 files are rejected
- oversized files are rejected instead of being silently truncated
- `apply_text_edit` succeeds only when `old_text` matches exactly one literal occurrence

The proxy is not a diagnostician:
- it should only relay exact commands the subject explicitly requested
- it should not add `sudo`, extra flags, extra subcommands, or a more specific variant on its own
- it should not bundle multiple commands unless the subject explicitly requested multiple commands
- if the subject asks for a file edit but does not specify the exact change, the proxy should inspect the file if needed and ask for clarification instead of guessing
- if the subject does not provide an exact command, the proxy should ask what exact command to run instead of guessing

The benchmark loop enforces those constraints and suppresses proxy turns that keep trying to run unrequested commands.

`user_proxy_llm.mode` controls how literal the proxy is:
- `strict_relay` keeps the old exact-command behavior
- `pragmatic_human` is the benchmark default and allows a narrow safe read-only fallback set when the assistant's intent is obvious but underspecified
- those fallback actions are limited to `read_file`, `cat`, `sed -n`, `file`, `ls -l`, and `readlink -f`
- `pragmatic_human` still does not infer edits, restarts, installs, or privileged commands

Benchmark verification is still objective:
- repair checks run after every subject turn
- if the proxy executes a potentially state-changing action such as `run_command`, `apply_text_edit`, or `interactive_send`, repair checks run again immediately instead of waiting for another subject turn
- soft closure messages from the proxy such as "looks good now, thanks" trigger one final verification pass before the harness spends another subject turn
- evaluation failure payloads now retain the last repair-check snapshot, passed-check count, failed-check names, and a short summary of the last subject reply so near-misses stay visible in Postgres artifacts

Repair success is not decided by the proxy. After each subject turn, the benchmark loop runs the scenario's objective `repair_checks` against the live sandbox. If every check passes, the evaluation completes with `repair_success=True`; otherwise the loop continues until the turn budget is exhausted or the proxy stalls repeatedly.

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
- bearer tokens that decode to an expired JWT `exp` are rejected during adapter session startup, before the benchmark fan-out begins
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

Planner, judge, and `user_proxy_llm` config sections now select their model backend with `provider: "openai" | "anthropic" | "google"` plus provider-specific credentials such as `api_key`.

Benchmark subject turn limits are scenario-first:
- `subjects[].adapter_config.max_turns` is optional
- when omitted, the scenario's `turn_budget` is the effective cap
- when present, it acts as an explicit lower bound for cheaper capped runs

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
- `backend.default_target_image` is the alias used when a scenario or request does not override it (canonical alias: `debian-12-ssm-golden`)
- `backend.target_images` maps each supported alias to the canonical Packer template directory and distro var-file
- `backend.golden_ami_id` remains a legacy single-image override only; prefer tagged target images

For the SSM controller:
- `controller.type` must be `"ssm"`
- `controller.aws_region` is the region where instances run
- `controller.command_timeout_seconds` controls how long the harness waits for an SSM shell command to complete; `600` is the default
