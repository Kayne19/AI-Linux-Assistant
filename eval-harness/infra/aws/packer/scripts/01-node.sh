#!/usr/bin/env bash
set -euo pipefail

echo ">>> [01] Installing Node.js ${NODE_MAJOR}.x"

curl -fsSL "https://deb.nodesource.com/setup_${NODE_MAJOR}.x" | bash -
apt-get install -y -qq nodejs

node --version
npm --version

echo ">>> [01] Node.js installed."
