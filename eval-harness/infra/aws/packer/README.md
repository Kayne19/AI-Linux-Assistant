# Golden AMI Build Notes

The golden AMI is intentionally stable and versioned. Do not install OpenClaw globally.

## Build Rules

- base OS: Debian 12
- include `amazon-ssm-agent`
- install OpenClaw into a dedicated directory such as `/opt/openclaw`
- pin the exact OpenClaw version and keep the lockfile in the image build context
- install the `openclaw.service` unit from this directory
- configure the service to bind the gateway to loopback only

Suggested install shape during image build:

```bash
mkdir -p /opt/openclaw
cd /opt/openclaw
npm init -y
npm install --save-exact openclaw@<EXACT_VERSION>
```

The systemd unit should launch the local bundle entrypoint from `/opt/openclaw/node_modules/.bin/openclaw`.
