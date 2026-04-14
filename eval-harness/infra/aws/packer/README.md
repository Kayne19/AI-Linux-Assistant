# Golden AMI Build Notes

The golden AMI is intentionally stable and versioned. Do not install OpenClaw globally.
Do not rely on an unpinned registry install at boot time.

## Build Rules

- base OS: Debian 12
- include `amazon-ssm-agent`
- install OpenClaw into a dedicated directory such as `/opt/openclaw`
- ship a pinned local OpenClaw bundle or tarball in the AMI build context
- unpack the pinned bundle into `/opt/openclaw` during image build
- install the `openclaw.service` unit from this directory
- configure the service to bind the gateway to loopback only

Suggested install shape during image build:

```bash
mkdir -p /opt/openclaw
tar -xzf openclaw-<EXACT_VERSION>.tgz -C /opt/openclaw --strip-components=1
```

The systemd unit should launch the pinned local bundle entrypoint from `/opt/openclaw/bin/openclaw` or the equivalent vendored path.
