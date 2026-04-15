#!/usr/bin/env bash
set -euo pipefail

echo ">>> [02] Installing OpenClaw ${OPENCLAW_VERSION}"

install_dir="/opt/openclaw"
install_log="/var/log/openclaw-install.log"
mkdir -p "${install_dir}"
cd "${install_dir}"

echo ">>> [02] Writing install log to ${install_log}"
export npm_config_audit=false
export npm_config_fund=false
export npm_config_loglevel=info

install_status=0
timeout --signal=TERM 20m env OPENCLAW_DISABLE_BUNDLED_PLUGIN_POSTINSTALL=1 \
  npm install --omit=dev --no-package-lock "openclaw@${OPENCLAW_VERSION}" 2>&1 | tee "${install_log}" || install_status=$?
if [[ "${install_status}" -ne 0 ]]; then
  echo ">>> [02] OpenClaw install failed with status ${install_status}" >&2
  tail -n 200 "${install_log}" >&2 || true
  exit "${install_status}"
fi

/opt/openclaw/node_modules/.bin/openclaw --version

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
