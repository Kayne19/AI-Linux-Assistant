#!/usr/bin/env bash
set -euo pipefail

echo ">>> [03] Installing SSM Agent"

ARCH=$(uname -m)
case "${ARCH}" in
  x86_64) SSM_ARCH="amd64" ;;
  aarch64) SSM_ARCH="arm64" ;;
  *) echo "ERROR: Unsupported arch ${ARCH}" >&2; exit 1 ;;
esac

TMP_DEB=$(mktemp /tmp/ssm-agent-XXXXXX.deb)
curl -fsSL \
  "https://s3.amazonaws.com/ec2-downloads-windows/SSMAgent/latest/debian_${SSM_ARCH}/amazon-ssm-agent.deb" \
  -o "${TMP_DEB}"
dpkg -i "${TMP_DEB}" || apt-get install -f -y
rm -f "${TMP_DEB}"

systemctl enable amazon-ssm-agent
systemctl start amazon-ssm-agent || true

echo ">>> [03] SSM Agent installed."
