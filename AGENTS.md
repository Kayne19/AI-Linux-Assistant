# Repository Guidelines

## Project Structure & Module Organization
- `Back-end/app/`: Python runtime for the assistant. Entry point is `app/main.py`, with supporting modules like `model_router.py`, `gemini_caller.py`, `context_agent.py`, and `vectorDB.py`.
- `Back-end/scripts/`: Data ingestion and cleanup utilities (older variants live under `scripts/deprecated/`).
- `Back-end/data/`: Source PDFs used for retrieval-augmented generation (RAG).
- `Back-end/lancedb_data/` and `Back-end/chroma_db/`: Local vector database artifacts.
- `Back-end/extracted_*.json` and `Back-end/doc_context.txt`: Preprocessed datasets and context outputs.
- `Proposal.pdf`: Local project proposal reference (ignored by git).

## Build, Test, and Development Commands
- `conda activate AI-Linux-Assistant`: Required before running Python tooling for this project.
- `python app/main.py` (run from `Back-end/`): Launches the CLI assistant loop.
- `python scripts/chatGPT_PDF_intake.py`: Ingests PDFs into cleaned JSON (read the script before running; it writes outputs).
- `python scripts/context_enrichment.py`: Enriches context fields for RAG (writes `extracted_*` files).
- No build system is defined; use a Python virtualenv and install dependencies as needed.

## Coding Style & Naming Conventions
- Indentation: 4 spaces; keep lines concise and readable.
- Naming: `snake_case` for functions/variables/files, `PascalCase` for classes, `UPPER_SNAKE_CASE` for constants.
- Keep imports grouped by standard library, third-party, then local modules.
- Avoid adding non-ASCII characters unless a file already uses them.

## Testing Guidelines
- No automated tests are present. If you add tests, use `pytest`, place them under `Back-end/tests/`, and name files `test_*.py`.
- Prefer unit tests around `VectorDB` ingestion and retrieval logic, and around request formatting in `GeminiCaller`.

## Configuration & Security
- Store secrets in `Back-end/.env` (e.g., `GOOGLE_API_KEY`); do not commit them.
- Model and DB paths are hard-coded in `app/vectorDB.py` (e.g., `lancedb_data`, `extracted_clean_final.json`). Update carefully and document changes in PRs.

## Commit & Pull Request Guidelines
- No commit history is available in this repo; use short, imperative commit subjects (e.g., "Add vector DB ingest guard").
- PRs should include: a clear description, commands run, and notes about any data/regeneration steps. Screenshots are unnecessary unless you change UI outputs.
