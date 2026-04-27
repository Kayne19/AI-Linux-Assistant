# AWS Operator Notes

V1 runtime assumptions:
- AWS only
- SSM only
- zero inbound SSH rules
- one target-image alias resolved to one tagged golden AMI, one verified staging instance, one transient broken image per run group

## AWS Profile Resolution

The harness resolves the active AWS profile in this order:

1. `EVAL_HARNESS_AWS_PROFILE` — harness-specific override; takes precedence over everything else
2. `AWS_PROFILE` — standard AWS SDK env var
3. boto3 default credential chain (instance role, `~/.aws/credentials`, etc.)

Set `EVAL_HARNESS_AWS_PROFILE` in `eval-harness/.env` when you need the harness to use a named profile without disturbing other tools that read `AWS_PROFILE`.

The resolved profile name and region are logged at harness startup so operators can confirm which identity is in use.

## AWS Preflight Checks

Before any EC2, SSM, or Packer work begins, the harness runs a preflight sequence and fails fast if any check fails. Checks run in this order:

1. **Profile + region resolution** — the resolved profile name and region are surfaced in logs.
2. **`sts:GetCallerIdentity`** — confirms the resolved credentials are valid. If the SSO session has expired, the harness prints:
   ```
   AWS SSO session expired or missing for profile '<name>'. Run: aws sso login --profile <name>
   ```
   and exits immediately. No EC2 or Packer activity starts before this passes.
3. **`aws` CLI on PATH** — required for AMI builds (Packer uses `aws configure export-credentials`).
4. **`packer` on PATH** — required for AMI builds only.

The harness does not auto-run interactive `aws sso login`. It prints the exact command and exits.

**Note on IAM Identity Center (SSO) session lifetime:** SSO sessions expire on their configured schedule regardless of harness activity. An active SSO session grants temporary credentials to boto3 automatically, but once it expires you must log in again manually. The harness preflight catches this before any resources are touched.

## Required AWS Inputs

- `AWS_REGION` (or `EVAL_HARNESS_AWS_REGION` in `eval-harness/.env`)
- subnet with outbound access for SSM registration
- security group set appropriate for outbound-only instances
- IAM instance profile with `AmazonSSMManagedInstanceCore`
- target-image config in the harness backend config
- `packer`, `aws`, and Session Manager tooling on the operator machine if auto-build is enabled

## Required Tagging

Every golden AMI should include at least:
- `EvalHarness=true`
- `EvalImageRole=golden`
- `EvalTargetImage=<target image alias>`

Every transient resource should include at least:
- `EvalHarness=true`
- `EvalGroupId=<group id>`
- `EvalScenarioId=<scenario id>`
- `EvalRole=<staging|broken-image|subject-clone>`
- `EvalSubject=<subject>` on subject clone instances

## Cleanup Model

Worker cleanup is necessary but not sufficient.

Also operate a separate reaper:
- find instances tagged `EvalHarness=true`
- terminate instances older than the allowed TTL
- find golden AMIs by `EvalImageRole=golden` and `EvalTargetImage=*` when auditing image inventory
- deregister leaked transient AMIs and delete their snapshots

This keeps resource lifecycle responsibility outside the orchestrator.
