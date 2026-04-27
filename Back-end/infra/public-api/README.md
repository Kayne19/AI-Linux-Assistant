# Public API Deployment

This directory contains the deployment artifacts for exposing the backend API to the internet on an existing host.

Current deployment target:

- FastAPI backend exposed publicly
- separate durable chat-run worker process
- Cloudflare Tunnel at the edge
- React frontend kept private for now, but easy to expose later on a second hostname

## Recommended Topology

Run these local services on the host:

- backend API on `127.0.0.1:8000`
- one or more chat-run workers
- optional Redis on the same private network if you want live fanout/SSE performance parity

Publish only the API hostname through Cloudflare Tunnel:

- `https://api.<your-domain>` -> `http://127.0.0.1:8000`

Reserve the frontend hostname now:

- `https://app.<your-domain>` -> later static frontend service such as `http://127.0.0.1:4173`

## Process Model

Do not use `run_dev.py` for the public deployment.

Run the backend as separate long-lived services:

1. FastAPI API service
2. chat-run worker service

For a temporary non-`systemd` launch from the repo root, use:

```bash
conda activate AI-Linux-Assistant
python run_public_api.py
```

Optional launcher env:

- `AILA_PUBLIC_API_HOST` default `127.0.0.1`
- `AILA_PUBLIC_API_PORT` default `8000`
- `AILA_PUBLIC_WORKER_PROCESS_COUNT` default `1`
- `AILA_PUBLIC_WORKER_CONCURRENCY` default `4`
- `AILA_PUBLIC_START_CLOUDFLARED=1` to launch `cloudflared` with the same command
- `AILA_CLOUDFLARED_CONFIG` default `~/.cloudflared/config.yml`

The provided systemd unit files are templates. Replace:

- `<REPO_ROOT>`
- `<DEPLOY_USER>`
- `<CONDA_PYTHON>`

That matches the durable-run ownership model described in:

- [Back-end/app/API.md](/home/kayne19/projects/AI-Linux-Assistant/Back-end/app/API.md)
- [Back-end/app/orchestration/RUNS.md](/home/kayne19/projects/AI-Linux-Assistant/Back-end/app/orchestration/RUNS.md)

## Required Backend Environment

Keep production secrets in `Back-end/.env`.

Minimum important values:

- `AUTH0_DOMAIN`
- `AUTH0_ISSUER`
- `AUTH0_AUDIENCE`
- `FRONTEND_ORIGINS=http://localhost:5173,https://app.<your-domain>`
- `ENABLE_LEGACY_BOOTSTRAP_AUTH=false`
- database connection env required by the backend
- optional `REDIS_URL`

Important:

- keep `ENABLE_LEGACY_BOOTSTRAP_AUTH=false` on any public deployment
- keep the API bound to loopback and let Cloudflare Tunnel handle public ingress

## Auth Model For Eval Harness

The supported public-benchmark auth path is Auth0 M2M (client-credentials grant). The harness obtains short-lived access tokens automatically and refreshes them mid-run — no manual token pasting is required.

Required harness env/config:

- `EVAL_HARNESS_AI_API_BASE_URL=https://api.<your-domain>`
- six M2M client credentials in `eval-harness/.env`:
  - `EVAL_HARNESS_REGULAR_CLIENT_ID` / `EVAL_HARNESS_REGULAR_CLIENT_SECRET`
  - `EVAL_HARNESS_MAGI_LITE_CLIENT_ID` / `EVAL_HARNESS_MAGI_LITE_CLIENT_SECRET`
  - `EVAL_HARNESS_MAGI_FULL_CLIENT_ID` / `EVAL_HARNESS_MAGI_FULL_CLIENT_SECRET`

Each client maps to one benchmark subject and becomes its own backend user. Create three M2M applications in the Auth0 dashboard and authorize each for the backend API audience.

Do not enable legacy bootstrap auth on a public API deployment.

## Cloudflare Tunnel

Use the template in [cloudflared/config.template.yml](/home/kayne19/projects/AI-Linux-Assistant/Back-end/infra/public-api/cloudflared/config.template.yml).

The intended pattern is:

- `api.<your-domain>` proxies to `http://127.0.0.1:8000`
- `app.<your-domain>` is reserved for later and currently points to an HTTP 404 placeholder

## Frontend Later

When you are ready to expose the web app:

1. build the frontend with `npm run build`
2. serve the built assets locally on the host
3. point `app.<your-domain>` at that local service
4. set `VITE_API_BASE_URL=https://api.<your-domain>`
5. add `https://app.<your-domain>` to Auth0 callback/logout/web-origin settings

The backend is already compatible with that split-origin setup through `FRONTEND_ORIGINS`.
