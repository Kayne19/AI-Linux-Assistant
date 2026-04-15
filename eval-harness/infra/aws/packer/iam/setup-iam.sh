#!/usr/bin/env bash
set -euo pipefail

ROLE_NAME="EvalSSMRole"
PROFILE_NAME="EvalSSMInstanceProfile"
POLICY_ARN="arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"

TRUST_POLICY='{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": { "Service": "ec2.amazonaws.com" },
      "Action": "sts:AssumeRole"
    }
  ]
}'

aws iam create-role \
  --role-name "${ROLE_NAME}" \
  --assume-role-policy-document "${TRUST_POLICY}" \
  --description "Allows EC2 instances to communicate with SSM for eval harness" \
  2>/dev/null || true

aws iam attach-role-policy \
  --role-name "${ROLE_NAME}" \
  --policy-arn "${POLICY_ARN}" \
  2>/dev/null || true

aws iam create-instance-profile \
  --instance-profile-name "${PROFILE_NAME}" \
  2>/dev/null || true

aws iam add-role-to-instance-profile \
  --instance-profile-name "${PROFILE_NAME}" \
  --role-name "${ROLE_NAME}" \
  2>/dev/null || true

echo "Created or verified ${PROFILE_NAME}."
