# AWS Operator Notes

V1 runtime assumptions:
- AWS only
- SSM only
- zero inbound SSH rules
- one golden AMI, one verified staging instance, one transient broken image per run group

## Required AWS Inputs

- `AWS_REGION`
- subnet with outbound access for SSM registration
- security group set appropriate for outbound-only instances
- IAM instance profile with `AmazonSSMManagedInstanceCore`
- golden AMI id

## Required Tagging

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
- deregister leaked transient AMIs and delete their snapshots

This keeps resource lifecycle responsibility outside the orchestrator.
