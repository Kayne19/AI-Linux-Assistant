# AI Linux Assistant

AI Linux Assistant is a custom, project-scoped Linux support system with:

- a Python backend built around an explicit router/FSM
- persistent users, projects, chats, and memory
- retrieval-augmented answering over an ingested document corpus
- a React frontend
- streaming chat responses, Magi/council deliberation, and live backend status updates

This repo is organized as a real application, not a prompt wrapper.

## Layout

- `Back-end/`
  - Python runtime, API, orchestration, ingestion, retrieval, persistence
- `Front-end/`
  - React + TypeScript web client
- `run_dev.py`
  - starts backend, four chat worker processes by default, and frontend together for local development

## Start The App

The project expects the `AI-Linux-Assistant` conda environment for backend Python tooling.

From the repo root:

```bash
conda activate AI-Linux-Assistant
python run_dev.py
```

That starts:

- FastAPI backend on port `8000`
- four chat worker processes by default in dev (`CHAT_RUN_WORKER_PROCESS_COUNT`, defaults to `4`)
- Vite frontend on port `5173`

For a temporary public API-only stack without `systemd`:

```bash
conda activate AI-Linux-Assistant
python run_public_api.py
```

That starts:

- FastAPI backend on `127.0.0.1:8000` by default
- one or more chat worker processes
- optional `cloudflared` when `AILA_PUBLIC_START_CLOUDFLARED=1`

Web auth requires Auth0 configuration for the browser app:

- backend: `AUTH0_DOMAIN`, `AUTH0_ISSUER`, `AUTH0_AUDIENCE`, `FRONTEND_ORIGIN`
- frontend: `VITE_AUTH0_DOMAIN`, `VITE_AUTH0_CLIENT_ID`, `VITE_AUTH0_AUDIENCE`, optional `VITE_AUTH0_REDIRECT_URI`, and `VITE_API_BASE_URL`

Auth0 SPA note for local development:

- open the frontend from `http://localhost:5173` or serve it over `https://...`
- opening the Vite dev server from a LAN IP like `http://192.168.x.x:5173` will fail because the Auth0 SPA SDK requires a secure origin or localhost

## Backend Orientation

Start with these docs:

- [Back-end/app/auth/AUTHENTICATION.md](Back-end/app/auth/AUTHENTICATION.md)
- [Back-end/app/ARCHITECTURE.md](Back-end/app/ARCHITECTURE.md)
- [Back-end/app/orchestration/RUNS.md](Back-end/app/orchestration/RUNS.md)
- [Back-end/app/persistence/MEMORY.md](Back-end/app/persistence/MEMORY.md)
- [Back-end/app/retrieval/RETRIEVAL.md](Back-end/app/retrieval/RETRIEVAL.md)
- [Back-end/app/ingestion/INGESTION.md](Back-end/app/ingestion/INGESTION.md)
- [Back-end/app/API.md](Back-end/app/API.md)
- [Back-end/app/streaming/STREAMING.md](Back-end/app/streaming/STREAMING.md)
- [Back-end/app/agents/AGENT_ROLES.md](Back-end/app/agents/AGENT_ROLES.md)
- [Back-end/app/providers/PROVIDERS.md](Back-end/app/providers/PROVIDERS.md)

## Frontend Orientation

- [Front-end/FRONTEND.md](Front-end/FRONTEND.md)

## Public API Deployment

For internet exposure of the backend API on an existing host, use:

- [Back-end/infra/public-api/README.md](Back-end/infra/public-api/README.md)

That deployment bundle includes:

- systemd units for the API and chat worker
- a Cloudflare Tunnel config template
- the recommended split-hostname shape for `api.<domain>` now and `app.<domain>` later

## Important Product Model

The application is organized around:

- `user`
- `project`
- `chat_session`

Project scope matters:

- each project owns its own memory
- chats inside a project share that project memory
- answers should respect the remembered environment of the active project

## Development Notes

- The backend architecture favors explicit lifecycle phases and traceability.
- The router owns workflow.
- Providers own transport only.
- Persistence layers stay persistence-only.
- Ingestion is an operator workflow, separate from normal chat runtime.
- Web auth now uses Auth0 Universal Login plus backend-validated bearer access tokens.
- The React app bootstraps through `GET /app/bootstrap`.
- CLI/TUI remains a separate local operator path and still uses local username selection.
- Chat requests can run in normal mode or Magi council mode (`off`, `lite`, `full`).
- Web chat execution now uses durable Postgres-backed chat runs processed by a separate worker.

## Documentation Rule

The markdown docs in this repo are part of the architecture surface.

When backend/frontend structure changes, update the relevant docs in the same change instead of leaving them as historical artifacts.
