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

## Judge Design

### Universal Rubric

Every transcript is graded against five fixed criteria applied universally across all scenarios:

1. **Diagnosis correctness** — did the assistant identify the actual root cause introduced by the scenario sabotage?
2. **Evidence-gathering discipline** — did it request the right diagnostic information before proposing changes?
3. **Repair safety & specificity** — were proposed commands targeted, free of destructive side effects, and runnable as written?
4. **User-proxy interaction quality** — were instructions clear and exact enough for a confused human user to follow without guessing?
5. **Outcome** — did the system end up repaired? (mechanically anchored to `repair_success`, not inferred)

Per-scenario planner rubric items are appended as a `[scenario]`-tagged block and graded alongside the universal items. The universal block (tagged `[universal]`) is what drives cross-subject and cross-scenario aggregates.

**0–4 anchored scale (absolute mode):**
- `0` wrong/harmful · `1` poor · `2` partial · `3` good · `4` excellent
- `Outcome` is mechanically enforced: forced to 4 if `repair_success=True`, forced to ≤2 if False.

Each scored criterion carries `rationale` (1–3 sentences) and `evidence` (quoted transcript span).

### Grading Modes

- `--mode absolute` (default): each transcript scored independently on the 0–4 scale. Use for per-subject dashboards and trend tracking.
- `--mode pairwise`: round-robin head-to-head over all subjects. Each unordered pair is judged twice with order swapped to cancel position bias, then aggregated into Bradley-Terry ratings. Use when N≥2 ablations need a ranking with CIs.

Both modes share the same rubric so absolute and pairwise signals are conceptually aligned.

### Multi-Judge Ensembling

Configure a `judges:` list (see Config Shape) to run multiple providers in parallel:
- **Absolute mode**: per-criterion scores are averaged (weighted).
- **Pairwise mode**: fractional-win aggregation before Bradley-Terry fitting.

Variance across judges is persisted as a calibration signal per criterion. A single-entry `judges:` list reproduces single-judge behavior.

**Defaults when no `judges:` is configured:**
- `--mode pairwise`: Claude Haiku 4.5 (`claude-haiku-4-5-20251001`) via `ANTHROPIC_API_KEY`.
- `--mode absolute`: Claude Sonnet 4.6 (`claude-sonnet-4-6`) via `ANTHROPIC_API_KEY`.

### Bradley-Terry Aggregation

Pairwise mode fits a Bradley-Terry model over the per-criterion win/loss matrix. Each criterion contributes a separate BT rating; an overall rating is the mean across criteria. Bootstrap resampling over scenarios produces 95% CIs. Raw per-pair win/tie/loss counts are persisted alongside BT ratings for transparency.

`--bootstrap-samples N` (default 200) and `--rng-seed N` make bootstrap reproducible.

### Calibrate-Judge Subcommand

Before replacing a strong judge with a cheaper one, use `calibrate-judge` to quantify agreement:

```bash
python -m eval_harness calibrate-judge \
  --config examples/aws_ai_linux_assistant_vs_chatgpt_config.json \
  --benchmark-run-id <bid> \
  --strong haiku \
  --candidate gpt-mini \
  --max-pairs 50
```

Outputs:
- **Pairwise mode**: per-criterion raw agreement on non-tie verdicts + Cohen's κ over `{A, B, tie}`.
- **Absolute mode**: mean absolute error per criterion + Pearson correlation per criterion.

Pass `--out PATH` to write the JSON report to disk. This subcommand reads the DB only; it never writes `judge_items` rows.

## Methodology

V1 benchmark flow:
- planner LLM produces the troubleshooting scenario
- planner must define both sabotage and objective verification procedures, including any prerequisite installation or provisioning needed to create the failure
- sabotage steps are stored as raw executable shell commands or shell snippets in `sabotage_procedure`; prose labels, markdown fences, and backticks are rejected during validation
- planner rectification commands use the same sabotage-step validation before execution, so prose-like corrections fail setup before anything reaches the shell
- the ScenarioBuilderFSM applies sabotage on staging via SSM shell commands and runs the planner’s probes
- the setup agent is explicitly told it is operating inside a disposable benchmark sandbox and must not refuse bounded sabotage on generic safety grounds
- the verifier runs the exact probe commands and returns structured results; both verifier and setup-agent command execution are exercised before setup continues
- planner reviews raw probe output plus the harness-computed probe pass/fail snapshot and either approves or issues a correction
- when the machine is in the intended broken state but a probe matcher is brittle, planner approval may rewrite `verification_probes` before the scenario revision is finalized
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
- if the proxy already told the subject it ran or retried a concrete command, it should remember that command and reuse it when the subject asks for the exact output from that same action
- if the subject asks for a file edit but does not specify the exact change, the proxy should inspect the file if needed and ask for clarification instead of guessing
- if the subject does not provide an exact command, the proxy should ask what exact command to run instead of guessing
- it should stay in first-person confused-user voice and must not flip into assistant phrasing such as "paste the output and I'll diagnose it"

The benchmark loop enforces those constraints and suppresses proxy turns that keep trying to run unrequested commands.

`user_proxy_llm.mode` controls how literal the proxy is:
- `strict_relay` keeps the old exact-command behavior
- `pragmatic_human` is the benchmark default and allows a narrow safe read-only fallback set when the assistant's intent is obvious but underspecified
- those fallback actions are limited to `read_file`, `cat`, `sed -n`, `file`, `ls -l`, and `readlink -f`
- after an assistant-prescribed repair, `pragmatic_human` may do a small amount of obvious follow-through on that same thing, like retrying the service or checking whether it came back
- `pragmatic_human` still does not infer edits, restarts, installs, or privileged commands

Benchmark verification is still objective:
- repair checks run after every subject turn
- if the proxy executes a potentially state-changing action such as `run_command`, `apply_text_edit`, or `interactive_send`, repair checks run again immediately instead of waiting for another subject turn
- soft closure messages from the proxy such as "looks good now, thanks" trigger one final verification pass before the harness spends another subject turn
- evaluation failure payloads now retain the last repair-check snapshot, passed-check count, failed-check names, and a short summary of the last subject reply so near-misses stay visible in Postgres artifacts

Repair success is not decided by the proxy. After each subject turn, the benchmark loop runs the scenario's objective `repair_checks` against the live sandbox. If every check passes, the evaluation completes with `repair_success=True`; otherwise the loop continues until the turn budget is exhausted or the proxy stalls repeatedly.

#### Proxy-relative native history

Each provider client builds a provider-native multi-turn conversation for the proxy, not a flat text blob. In benchmark perspective the proxy is the "user" and the subject is the "assistant"; the proxy's native view flips those roles — subject replies become `user` turns and prior proxy replies become `assistant` turns. The leading proxy turn (the opening user message before any subject reply) is skipped when building native history to avoid starting with an `assistant`-role message, which some provider APIs reject. The current subject reply is appended as the final `user` turn. This means the same subject reply is never passed twice: `benchmark.py` passes `transcript_pairs[:-1]` (excluding the current reply) and passes the reply separately as `assistant_reply`.

#### Cross-turn terminal memory

The benchmark loop maintains a bounded queue (`maxlen=5`) of `ProxyRecentAction` records — one per proxy tool execution — accumulated across all turns of a single subject run. Each record captures the tool name, turn index, command text, result output, exit code, and whether the command is safe to re-run. Before each proxy LLM call, the benchmark serializes these records into a compact text block and passes it to the FSM as `proxy_recent_memory`. The FSM exposes it to the LLM so the proxy can remember what it already ran and avoid re-running state-changing commands.

If the subject asks for the exact output of a command the proxy already ran in a prior turn, the FSM short-circuits the LLM entirely and returns the stored output directly without making an API call.

#### Always-on revision pass

After the proxy LLM generates a reply, the FSM makes a second API call — `review_reply(...)` — using the same model and provider. The reviewer now returns structured JSON with a `verdict` of `accept`, `rewrite_text`, or `retry_with_tools`, plus the corrected `final_reply`, a short `reason`, and an internal `audit_json` block. The generator still decides whether to use tools or ask a human-style question. The reviewer only enforces that the proxy stayed in first-person confused-user voice and used tools appropriately for the assistant's request. `rewrite_text` means the generator's underlying action or question was fine but the wording slipped into assistant/helpdesk voice, so the FSM uses the corrected `final_reply` without retrying the turn. If the reviewer says `retry_with_tools`, the FSM performs one bounded corrective retry before finalizing the turn, carrying the same turn's tool outputs into the retry context. The review payloads are persisted in `evaluation_events` as internal `proxy_review` and `proxy_review_retry_decision` records; they are not rendered as transcript turns.

Character fidelity is a first-class review criterion:
- the proxy is reminded that it is the human user with terminal access
- the subject is treated as a text-only assistant that cannot run commands or inspect the machine directly
- replies that tell the assistant what to run, ask it to paste output, or otherwise switch into helpdesk voice are review failures rather than acceptable clarifications

Review activity is also visible live during benchmark execution:
- the stderr progress sink renders `proxy_review` and `proxy_review_retry_decision` events with the verdict, short reason, and compact reviewer reasoning
- the full internal `audit_json`, including reviewer reasoning and character-analysis fields, still lives in `evaluation_events.payload_json` for DB inspection

#### Stall behavior

When the proxy cannot produce a meaningful reply (empty content, no tool calls, or the reply is an exact repeat of a prior proxy message), the FSM stalls and returns a fixed fallback clarification — `"I'm not sure what you need me to do exactly — can you be more specific?"` — instead of resending the opening message unchanged. The benchmark loop uses this fallback as the next user turn rather than re-sending the opener, which would create an infinite stall loop.

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
python ../run_eval_harness.py generate-scenario --output /tmp/linux_scenario.json
python ../run_eval_harness.py verify-scenario --group-id demo-setup
```

Place env vars in:

- [eval-harness/.env](eval-harness/.env)

The CLI autoloads that file from the harness root.

For the public AI Linux Assistant API path:

- point `EVAL_HARNESS_AI_API_BASE_URL` at the public backend hostname, for example `https://api.<your-domain>`
- authenticate using Auth0 M2M credentials — the harness fetches and refreshes tokens automatically, so no manual JWT pasting is needed
- do not rely on legacy bootstrap auth against a public deployment

### M2M Auth Setup

The harness uses the Auth0 client-credentials grant, with one M2M application per benchmark subject. This keeps each subject isolated as its own backend user and avoids sharing the per-user active-run cap.

**Auth0 steps (one-time):**

1. In the Auth0 Dashboard, create three Machine to Machine applications:
   - `eval-regular`
   - `eval-magi-lite`
   - `eval-magi-full`
2. Under each application's APIs tab, authorize it for the backend API audience (the value in `AUTH0_AUDIENCE` on the backend).
3. Copy each application's `client_id` and `client_secret` into `eval-harness/.env`.

**Required env vars in `eval-harness/.env`:**

```
EVAL_HARNESS_REGULAR_CLIENT_ID=<set-me>
EVAL_HARNESS_REGULAR_CLIENT_SECRET=<set-me>
EVAL_HARNESS_MAGI_LITE_CLIENT_ID=<set-me>
EVAL_HARNESS_MAGI_LITE_CLIENT_SECRET=<set-me>
EVAL_HARNESS_MAGI_FULL_CLIENT_ID=<set-me>
EVAL_HARNESS_MAGI_FULL_CLIENT_SECRET=<set-me>
```

These are referenced from the example configs via `env:EVAL_HARNESS_*` placeholders. Do not commit real secrets.

**Token lifecycle:** the harness caches each token and re-fetches it automatically when it is within `refresh_skew_seconds` (default 60) of expiry. Long benchmark runs survive token-expiry boundaries without intervention.

**Rotating a client secret:** update the secret in the Auth0 Dashboard, then update the corresponding `EVAL_HARNESS_*_CLIENT_SECRET` in `eval-harness/.env`. The running harness picks up the new secret on the next token refresh cycle. No benchmark restart is needed mid-run as long as the old secret is still valid when the harness last fetched a token.

Initialize the eval-harness schema:

```bash
cd eval-harness
python -m eval_harness init-db --config examples/aws_ai_linux_assistant_config.json
```

Generate a planner draft:

```bash
python -m eval_harness generate-scenario \
  --config examples/aws_ai_linux_assistant_config.json \
  --request examples/planner_requests/general_linux_troubleshooting_request.json \
  --output /tmp/linux_scenario.json
```

Planner, judge, and `user_proxy_llm` config sections now select their model backend with `provider: "openai" | "anthropic" | "google"` plus provider-specific credentials such as `api_key`.

OpenAI planner defaults:
- when `planner.reasoning_effort` is omitted, the OpenAI planner uses `xhigh`
- `planner.web_search_enabled` defaults to `true` on the OpenAI planner path and enables Responses web search for scenario generation, validation repair, sabotage review, and rectification planning
- when `planner.request_timeout_seconds` is omitted, planner requests run without a client-side timeout
- broad web search is the current v1 behavior; non-OpenAI planner providers ignore that toggle

Benchmark subject turn limits are scenario-first:
- `subjects[].adapter_config.max_turns` is optional
- when omitted, the scenario's `turn_budget` is the effective cap
- when present, it acts as an explicit lower bound for cheaper capped runs

Run planner-driven scenario setup and verification:

```bash
python -m eval_harness verify-scenario \
  --config examples/aws_ai_linux_assistant_config.json \
  --request examples/planner_requests/general_linux_troubleshooting_request.json \
  --group-id demo-setup
```

Run the benchmark against all configured active subjects:

```bash
python -m eval_harness run-benchmark \
  --config examples/aws_ai_linux_assistant_config.json \
  --setup-run-id <verified_setup_run_id>
```

Run blind judging (absolute mode, default):

```bash
python -m eval_harness run-judge-job \
  --config examples/aws_ai_linux_assistant_config.json \
  --benchmark-run-id <benchmark_run_id>
```

Run blind judging in pairwise mode:

```bash
python -m eval_harness run-judge-job \
  --config examples/aws_ai_linux_assistant_vs_chatgpt_config.json \
  --benchmark-run-id <benchmark_run_id> \
  --mode pairwise \
  --bootstrap-samples 500 \
  --rng-seed 42
```

Pairwise against a single anchor subject (cheaper "everyone vs baseline" run):

```bash
python -m eval_harness run-judge-job \
  --config examples/aws_ai_linux_assistant_vs_chatgpt_config.json \
  --benchmark-run-id <benchmark_run_id> \
  --mode pairwise \
  --anchor-subject chatgpt-baseline
```

Calibrate a candidate judge against a stronger reference:

```bash
python -m eval_harness calibrate-judge \
  --config examples/aws_ai_linux_assistant_vs_chatgpt_config.json \
  --benchmark-run-id <benchmark_run_id> \
  --strong haiku \
  --candidate gpt-mini \
  --max-pairs 50 \
  --out /tmp/calibration_report.json
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
- `judge` (legacy single-judge block; still accepted)
- `judges` (preferred; list of judge entries, each with `name`, `provider`, `model`, `api_key`, optional `weight` and `request_timeout_seconds`)
- `judge_default_mode` (`"absolute"` or `"pairwise"`; default `"absolute"`)
- `subject_adapters`
- `subjects`

String values may reference environment variables with `env:VAR_NAME`.

**Back-compat:** if `judge:` is set and `judges:` is not, the single block is wrapped into a one-element list internally. Existing configs require no changes.

**Multi-judge example** (from `aws_ai_linux_assistant_vs_chatgpt_config.json`):

```json
"judges": [
  {
    "name": "haiku",
    "provider": "anthropic",
    "model": "claude-haiku-4-5-20251001",
    "api_key": "env:ANTHROPIC_API_KEY",
    "weight": 1.0
  },
  {
    "name": "gpt-mini",
    "provider": "openai",
    "model": "gpt-5.4-mini",
    "api_key": "env:EVAL_HARNESS_JUDGE_API_KEY",
    "weight": 1.0
  }
]
```

For the `ai_linux_assistant_http` subject adapter:

- the harness talks to the backend API only, not the React frontend
- the minimum public flow is create project, create chat, create run, poll run, and poll run events
- Auth0 M2M (client-credentials) is the supported public auth path; configure `auth0_m2m` in the adapter config and supply credentials via env vars

For the `openai_chatgpt` subject adapter:

- the harness uses the local OpenAI Responses client already used by the planner, judge, and user proxy code
- configure it with `model` and `api_key`, plus optional `base_url`, `request_timeout_seconds`, `max_output_tokens`, `reasoning_effort`, and `instructions`
- `conversation_state_mode` defaults to `conversation`; set it to `response_chain` only when you explicitly need `previous_response_id` chaining instead of Conversations API state
- `web_search_enabled` defaults to `true` to mirror chatgpt.com's browser defaults; `code_interpreter_enabled` also defaults to `true` and appends the OpenAI-hosted `code_interpreter` tool with `container.type="auto"`
- other web-search parity knobs: `web_search_allowed_domains`, `web_search_user_location`, `web_search_include_sources`, and `web_search_search_context_size`
- when `web_search_include_sources` is enabled, the adapter appends a compact `Sources:` block to the assistant reply so blind judging sees the cited answer rather than hidden debug metadata only
- when `instructions` is omitted, the adapter auto-injects a minimal ChatGPT-style system preamble (`"You are ChatGPT, a large language model trained by OpenAI.\nCurrent date: YYYY-MM-DD"`) so the baseline is grounded in the current date the way the chatgpt.com browser UI is; setting `instructions` explicitly overrides the auto-preamble
- any `system`-role entries in `subjects[].context_seed` are merged into `instructions` rather than injected as conversation items, because the Responses API treats the top-level `instructions` field as the model's base system prompt
- `truncation` defaults to `"auto"` so long multi-turn runs do not overflow the context window
- when `request_timeout_seconds` is omitted, the client runs without a client-side timeout, matching the planner path
- every response request carries a stable `user` identifier (`eval-harness:<benchmark_run_id>:<subject_name>`) and a `metadata` block with `benchmark_run_id`, `subject_name`, and `turn_index` for abuse-monitoring parity and forensic lookup in the OpenAI dashboard
- `subjects[].adapter_config` can override any of those values per subject when you want to compare multiple ChatGPT baselines in the same run

See:
- [aws_ai_linux_assistant_config.json](eval-harness/examples/aws_ai_linux_assistant_config.json)
- [aws_ai_linux_assistant_vs_chatgpt_config.json](eval-harness/examples/aws_ai_linux_assistant_vs_chatgpt_config.json)
- [general_linux_troubleshooting_request.json](eval-harness/examples/planner_requests/general_linux_troubleshooting_request.json)
- [nginx_recovery_request.json](eval-harness/examples/planner_requests/nginx_recovery_request.json)
- [nginx_service_repair.json](eval-harness/examples/scenarios/nginx_service_repair.json)

For the AWS backend:
- `backend.default_target_image` is the alias used when a scenario or request does not override it (canonical alias: `debian-12-ssm-golden`)
- `backend.target_images` maps each supported alias to the canonical Packer template directory and distro var-file
- `backend.golden_ami_id` remains a legacy single-image override only; prefer tagged target images

For the SSM controller:
- `controller.type` must be `"ssm"`
- `controller.aws_region` is the region where instances run
- `controller.command_timeout_seconds` controls how long the harness waits for an SSM shell command to complete; `600` is the default
