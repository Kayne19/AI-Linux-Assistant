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

cat > /home/eval/.openclaw/gateway.yaml <<YAML
gateway:
  http:
    host: "127.0.0.1"
    port: 18789
    endpoints:
      chatCompletions:
        enabled: true

  auth:
    mode: "token"
    token: "${OPENCLAW_EVAL_TOKEN}"

  expose: false

tools:
  exec:
    shell: true

agents:
  setup:
    name: "setup"
    description: "Applies planner-directed sabotage steps."
  verifier:
    name: "verifier"
    description: "Runs exact verification and repair-check commands."
  proxy:
    name: "proxy"
    description: "Acts as the blind benchmark user."
YAML

cat > /home/eval/.openclaw/agents/setup/SOUL.md <<'MD'
You are the setup agent for the eval harness.
MD

cat > /home/eval/.openclaw/agents/verifier/SOUL.md <<'MD'
You are the verifier agent for the eval harness.
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
ExecStart=/opt/openclaw/node_modules/.bin/openclaw gateway start --config /home/eval/.openclaw/gateway.yaml
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths=/home/eval
PrivateTmp=true

[Install]
WantedBy=multi-user.target
SERVICE

systemctl daemon-reload
systemctl enable openclaw-gateway.service

echo ">>> [02] OpenClaw installed under ${install_dir}."
