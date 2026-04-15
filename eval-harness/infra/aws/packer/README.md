# Golden AMI Build Notes

This directory is the canonical golden-AMI build surface for the eval harness.

Current supported auto-build target:
- `debian-12-openclaw-golden`

The harness uses these assets in two ways:
- automatically during `verify-scenario` when the requested target image has no tagged golden AMI yet
- manually by operators when they want to pre-build or refresh the golden image

## Build Rules

- base OS: Debian 12
- include `amazon-ssm-agent`
- install OpenClaw into `/opt/openclaw`
- pin the OpenClaw version during image build
- build a preinstalled OpenClaw bundle on the machine running Packer and upload that bundle to the builder instead of resolving OpenClaw live inside the EC2 instance
- bind the gateway to loopback only on port `18789`
- bake three named agents: `setup`, `verifier`, and `proxy`
- require an IAM instance profile with `AmazonSSMManagedInstanceCore`

## Auto-Build Inputs

When the harness auto-builds a missing golden image, it generates the Packer variable file from runtime config:
- `backend.region`
- `backend.subnet_id`
- `backend.instance_profile_name`
- `backend.instance_type`
- `controller.token`
- the selected target-image entry under `backend.target_images`

The generated build tags the AMI with:
- `EvalHarness=true`
- `EvalImageRole=golden`
- `EvalTargetImage=debian-12-openclaw-golden`
- `OpenClawVersion=<version>`
- `ManagedBy=eval-harness`

## Manual Build

Prerequisites:
- `packer`
- `aws`
- AWS credentials that can create AMIs and IAM instance profiles
- Session Manager prerequisites for the build subnet

One-time IAM setup:

```bash
cd eval-harness/infra/aws/packer/iam
bash setup-iam.sh
```

Manual build:

```bash
cd eval-harness/infra/aws/packer
cp variables.pkrvars.hcl.example variables.pkrvars.hcl
packer init .
packer build \
  -var-file="variables.pkrvars.hcl" \
  -var-file="distros/debian-12.pkrvars.hcl" \
  .
```

The resulting AMI will be discoverable by the harness through its tags; you do not need to copy the AMI id back into the harness config.
