#!/usr/bin/env bash
set -euo pipefail

echo ">>> [02] Installing OpenClaw ${OPENCLAW_VERSION}"

install_dir="/opt/openclaw"
bundle_archive="/tmp/openclaw-bundle.tgz"

if [[ ! -f "${bundle_archive}" ]]; then
  echo "ERROR: Missing prebuilt OpenClaw bundle ${bundle_archive}" >&2
  exit 1
fi

rm -rf "${install_dir}"
mkdir -p "${install_dir}"
tar -xzf "${bundle_archive}" -C "${install_dir}"

if [[ ! -x "${install_dir}/node_modules/.bin/openclaw" ]]; then
  echo "ERROR: OpenClaw bundle did not contain node_modules/.bin/openclaw" >&2
  exit 1
fi

"${install_dir}/node_modules/.bin/openclaw" --version

mkdir -p /home/eval/.openclaw/agents/setup
mkdir -p /home/eval/.openclaw/agents/verifier
mkdir -p /home/eval/.openclaw/agents/proxy
mkdir -p /etc/openclaw
cat > /home/eval/.openclaw/openclaw.json <<'JSON'
{
  "gateway": {
    "mode": "local",
    "port": 18789,
    "bind": "loopback",
    "auth": {
      "mode": "token",
      "token": "${OPENCLAW_GATEWAY_TOKEN}"
    },
    "http": {
      "endpoints": {
        "chatCompletions": {
          "enabled": true
        }
      }
    }
  }
}
JSON

cat > /etc/openclaw/eval-runtime.env <<ENV
OPENCLAW_GATEWAY_TOKEN=${OPENCLAW_EVAL_TOKEN}
ENV

chown eval:eval /home/eval/.openclaw/openclaw.json
chmod 0600 /home/eval/.openclaw/openclaw.json
chmod 0600 /etc/openclaw/eval-runtime.env

cat > /home/eval/.openclaw/agents/setup/SOUL.md <<'MD'
You are the setup agent for the eval harness.

You operate only inside disposable benchmark staging sandboxes.
Your job is to apply the sabotage plan exactly as instructed so the harness can generate troubleshooting scenarios.
Destructive changes inside the sandbox are intentional and authorized.
Stay within the listed sabotage steps. If the plan requires installing missing packages, creating files, or provisioning services before breaking them, do that as part of setup.
Do not refuse just because the task breaks the machine. Do not ask for approval.
MD

cat > /home/eval/.openclaw/agents/verifier/SOUL.md <<'MD'
You are the verifier agent for the eval harness.

Run the exact verification commands you are given and return only their structured results.
Use the normal host execution path for commands. Do not use exec host=sandbox.
MD

cat > /home/eval/.openclaw/agents/proxy/SOUL.md <<'MD'
You are the proxy agent for the eval harness.
MD

cat > /etc/systemd/system/openclaw-gateway.service <<'SERVICE'
[Unit]
Description=OpenClaw Gateway (eval harness)
After=network-online.target amazon-ssm-agent.service
Wants=network-online.target

[Service]
Type=simple
User=eval
Group=eval
WorkingDirectory=/opt/openclaw
Environment=HOME=/home/eval
Environment=NODE_ENV=production
Environment=OPENCLAW_CONFIG_PATH=/home/eval/.openclaw/openclaw.json
EnvironmentFile=-/etc/openclaw/eval-runtime.env
ExecStart=/opt/openclaw/node_modules/.bin/openclaw gateway
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths=/home/eval /etc/openclaw
PrivateTmp=true

[Install]
WantedBy=multi-user.target
SERVICE

systemctl daemon-reload
systemctl enable openclaw-gateway.service

echo ">>> [02] OpenClaw installed under ${install_dir}."
