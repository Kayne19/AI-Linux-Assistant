#!/usr/bin/env bash
set -euo pipefail

echo ">>> [04] Creating eval user"

if ! id eval &>/dev/null; then
  useradd --system --create-home --home-dir /home/eval --shell /bin/bash eval
fi

chown -R eval:eval /home/eval
echo "eval ALL=(ALL) NOPASSWD: ALL" > /etc/sudoers.d/eval
chmod 0440 /etc/sudoers.d/eval

su - eval -c "/opt/openclaw/node_modules/.bin/openclaw --version" >/dev/null

echo ">>> [04] eval user created."
