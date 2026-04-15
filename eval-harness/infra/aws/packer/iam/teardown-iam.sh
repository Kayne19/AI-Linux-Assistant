#!/usr/bin/env bash
set -euo pipefail

ROLE_NAME="EvalSSMRole"
PROFILE_NAME="EvalSSMInstanceProfile"
POLICY_ARN="arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"

aws iam remove-role-from-instance-profile \
  --instance-profile-name "${PROFILE_NAME}" \
  --role-name "${ROLE_NAME}" \
  2>/dev/null || true

aws iam delete-instance-profile \
  --instance-profile-name "${PROFILE_NAME}" \
  2>/dev/null || true

aws iam detach-role-policy \
  --role-name "${ROLE_NAME}" \
  --policy-arn "${POLICY_ARN}" \
  2>/dev/null || true

aws iam delete-role \
  --role-name "${ROLE_NAME}" \
  2>/dev/null || true
