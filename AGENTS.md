# Repository Guidelines

## Project Structure & Module Organization
- `Back-end/app/`: Python runtime for the assistant. Entry point is `app/main.py`, with supporting modules like `model_router.py`, `gemini_caller.py`, `context_agent.py`, and `vectorDB.py`.
- `Back-end/scripts/`: Data ingestion and cleanup utilities (older variants live under `scripts/deprecated/`).
- `Back-end/data/`: Source PDFs used for retrieval-augmented generation (RAG).
- `Back-end/lancedb_data/` and `Back-end/chroma_db/`: Local vector database artifacts.
- `Back-end/extracted_*.json` and `Back-end/doc_context.txt`: Preprocessed datasets and context outputs.
- `Proposal.pdf`: Local project proposal reference (ignored by git).

## Architecture Priorities
- The project is intentionally organized around explicit state machines and traceability. Prefer making lifecycle phases visible in the router trace over hiding important work inside helpers or side effects.
- `Back-end/app/model_router.py` is the top-level orchestration layer. The router should own workflow phases and state transitions, not provider-specific execution details.
- Task agents should stay task-shaped, not provider-shaped. Current examples include classifier, contextualizer, responder, and memory extractor. They should accept injected workers instead of hardcoding a provider.
- Provider workers (`openAI_caller.py`, `gemini_caller.py`, `local_caller.py`) should own transport/API behavior only. They should not mutate router state or persistent storage directly.
- Persistence/query layers should stay persistence/query-only. For example, `memory_store.py` should store, merge, and query memory, while extraction/policy live in separate modules.
- If a subsystem has meaningful internal phases, prefer explicit modeling. The responder already has visible substates, and the memory pipeline is now represented as explicit router states (`LOAD_MEMORY`, `EXTRACT_MEMORY`, `RESOLVE_MEMORY`, `COMMIT_MEMORY`).
- Tool use should be observable. If a model can call tools, keep those calls visible through emitted events and trace markers rather than burying them in silent helper logic.
- Avoid “magic” behavior. Future agents should prefer architecture that makes it easy to answer: what phase is running, what component owns it, and where state changed.

## Build, Test, and Development Commands
- `conda activate AI-Linux-Assistant`: Required before running Python tooling for this project.
- `python app/main.py` (run from `Back-end/`): Launches the CLI assistant loop.
- `python app/AI_Generated_TUI.py` (run from `Back-end/`): Launches the curses-based TUI with router state and tool visibility.
- `python scripts/evaluate_router.py` (run from `Back-end/`): Runs the repeatable router evaluation battery and writes JSON results to `Back-end/evals/`.
- `python scripts/chatGPT_PDF_intake.py`: Ingests PDFs into cleaned JSON (read the script before running; it writes outputs).
- `python scripts/context_enrichment.py`: Enriches context fields for RAG (writes `extracted_*` files).
- No build system is defined; use a Python virtualenv and install dependencies as needed.

## Coding Style & Naming Conventions
- Indentation: 4 spaces; keep lines concise and readable.
- Naming: `snake_case` for functions/variables/files, `PascalCase` for classes, `UPPER_SNAKE_CASE` for constants.
- Keep imports grouped by standard library, third-party, then local modules.
- Avoid adding non-ASCII characters unless a file already uses them.

## Testing Guidelines
- Automated tests now live under `Back-end/tests/`. Name new files `test_*.py`.
- `pytest` is the intended test runner, but it may not be installed in every local environment. Keep test modules importable and plain-assert friendly so they can still be executed with a small direct harness when needed.
- Current coverage focuses on router flow, prompt regressions, and provider tool-loop behavior. Prefer expanding those areas before adding broad integration tests.
- Prefer unit tests around `VectorDB` ingestion/retrieval behavior, router state transitions, provider/tool-call formatting, and the memory pipeline (`extract -> resolve -> commit`).
- When changing FSMs, update tests to assert the expected state trace. Trace regressions matter in this codebase.
- When changing memory behavior, test both committed and unresolved outcomes. Candidate/conflict handling is part of the architecture, not an implementation detail.

## Configuration & Security
- Store secrets in `Back-end/.env` (e.g., `GOOGLE_API_KEY`); do not commit them.
- Default role/provider/model selection is centralized in `Back-end/app/settings.py`; prefer changing defaults there or via `.env` overrides instead of scattering model choices across modules.
- Model and DB paths are still configured in `app/vectorDB.py` (e.g., `lancedb_data`, `extracted_clean_final.json`). Update carefully and document changes in PRs.
- `VectorDB` supports `VECTORDB_EMBED_DEVICE` and `VECTORDB_RERANK_DEVICE` environment overrides. The eval runner may use these to control retrieval device placement.

## Commit & Pull Request Guidelines
- No commit history is available in this repo; use short, imperative commit subjects (e.g., "Add vector DB ingest guard").
- PRs should include: a clear description, commands run, and notes about any data/regeneration steps. Screenshots are unnecessary unless you change UI outputs.
