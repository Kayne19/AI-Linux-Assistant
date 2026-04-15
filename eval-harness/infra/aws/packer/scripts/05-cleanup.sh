#!/usr/bin/env bash
set -euo pipefail

echo ">>> [05] Cleaning up"

apt-get clean
apt-get autoremove -y
rm -rf /var/lib/apt/lists/*
rm -rf /tmp/* /var/tmp/*
rm -f /root/.bash_history /home/*/.bash_history
journalctl --rotate --vacuum-time=0 2>/dev/null || true
cloud-init clean --logs 2>/dev/null || true

echo ">>> [05] Cleanup done."
