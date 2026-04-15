#!/usr/bin/env bash
set -euo pipefail

echo ">>> [00] Installing base packages"

export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq \
  curl wget ca-certificates gnupg lsb-release \
  git jq unzip sudo systemd \
  build-essential python3 python3-pip tar gzip

echo ">>> [00] Base packages done."
