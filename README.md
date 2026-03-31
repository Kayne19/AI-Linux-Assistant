# AI Linux Assistant

AI Linux Assistant is a custom, project-scoped Linux support system with:

- a Python backend built around an explicit router/FSM
- persistent users, projects, chats, and memory
- retrieval-augmented answering over an ingested document corpus
- a React frontend
- streaming chat responses and live backend status updates

This repo is organized as a real application, not a prompt wrapper.

## Layout

- `Back-end/`
  - Python runtime, API, orchestration, ingestion, retrieval, persistence
- `Front-end/`
  - React + TypeScript web client
- `run_dev.py`
  - starts backend and frontend together for local development

## Start The App

The project expects the `AI-Linux-Assistant` conda environment for backend Python tooling.

From the repo root:

```bash
conda activate AI-Linux-Assistant
python run_dev.py
```

That starts:

- FastAPI backend on port `8000`
- Vite frontend on port `5173`

## Backend Orientation

Start with these docs:

- [Back-end/ARCHITECTURE.md](Back-end/ARCHITECTURE.md)
- [Back-end/MEMORY.md](Back-end/MEMORY.md)
- [Back-end/RETRIEVAL.md](Back-end/RETRIEVAL.md)
- [Back-end/INGESTION.md](Back-end/INGESTION.md)
- [Back-end/API.md](Back-end/API.md)
- [Back-end/STREAMING.md](Back-end/STREAMING.md)

## Frontend Orientation

- [Front-end/FRONTEND.md](Front-end/FRONTEND.md)

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

## Documentation Rule

The markdown docs in this repo are part of the architecture surface.

When backend/frontend structure changes, update the relevant docs in the same change instead of leaving them as historical artifacts.
