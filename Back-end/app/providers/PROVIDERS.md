# Provider Adapters

This document defines the interface and responsibilities for LLM provider adapters in the `Back-end/app/providers` package.

## Core Principle: Providers Own Transport Only

Provider adapters should only own the mechanics of communicating with external or local model APIs. They are implementation details injected into task-shaped agents.

### Responsibilities
- **Request Formatting**: Mapping the project's internal message and tool structures to the provider's specific API format.
- **Response Parsing**: Converting provider-specific outputs (text, tool calls, usage metadata) back into internal system models.
- **Structured Output Enforcement**: When a caller explicitly requests JSON via `structured_output=True` plus an `output_schema`, providers should use the vendor-native structured-output feature instead of relying only on prompt text.
- **Tool-Call Transport**: Handling the specific semantics of how the provider expects tool definitions and returns tool results.
- **Single-Step Transport**: Exposing one-step request/response primitives for router-owned protocols such as the regular responder mini-FSM.
- **Reliability**: Implementing provider-specific retries, timeouts, and error handling.

### Prohibitions
Providers MUST NOT own:
- **Router State**: They should not decide which phase runs next or modify the FSM.
- **Loop Ownership**: They should not own the regular responder's repeated tool-call loop; they return one model step and let the router decide whether another step is allowed.
- **Memory Policy**: They should not decide what gets committed to project memory.
- **Project/Session Scoping**: Scope is enforced by the backend, not the provider.
- **Policy Decisions**: They should not decide whether RAG is required or if a user is authorized.

## Supported Providers
- **Anthropic**: Adapter for the Messages API.
- **Google**: Adapter for the Gemini Developer API via `google-genai`.
- **OpenAI**: Adapter for the Responses API.
- **Local**: Adapter for locally hosted models (e.g., via Ollama or vLLM).

## Adding a New Provider
To add a new provider, implement a caller that satisfies the expected interface used by the agents. Ensure that all internal models are correctly mapped and that streaming deltas are handled consistently with the rest of the system.

Current responder transport rule:

- provider callers may still expose legacy convenience helpers that internally loop until tool calls stop
- the regular chatbot path should prefer the single-step transport methods so the router owns the bounded responder protocol
- those single-step calls must be sufficient for the router to run internal responder decision/evaluation phases before or after router-executed retrieval
- Magi keeps its existing role-level tool-loop contract and shared tool text contract

Structured-output rule:

- plain-text calls still use the normal provider text path
- JSON-required callers pass `structured_output=True` and an `output_schema` dict to `generate_text()` / `generate_text_stream()`
- Google maps that request to `generate_content` via `response_mime_type="application/json"` plus `response_schema`
- OpenAI maps that request to Responses structured output via `text.format`
- Anthropic maps that request to Messages structured output via `output_config.format`
- Local models currently warn and fall back to prompt-and-parse behavior
- if a provider cannot honor native structured output, it should emit a compact `structured_output_warning` event before using the prompt fallback path

Google-specific notes:

- Gemini tool calls are transported through `function_declarations`, returned as `function_call` parts, and resumed with `function_response` parts
- the main-app Google path supports the same router single-step transport contract as OpenAI and Anthropic: `start_text_step()` plus `continue_text_step()`
- native Google web search is intentionally not enabled in this path; callers that request it should receive an explicit unsupported event instead of an implicit fallback

## OpenAI Batch API wrapper (`openai_batch.py`)

`OpenAIBatchClient` is a transport-only wrapper around the four Batch API operations:

- `upload_jsonl(path)` — uploads a JSONL request file via `files.create(purpose="batch")`, returns the `file_id`.
- `submit_batch(input_file_id, ...)` — calls `batches.create` and returns a `BatchSubmission` dataclass with `batch_id`, `input_file_id`, `status`, and `created_at`.
- `get_status(batch_id)` — calls `batches.retrieve` and returns a `BatchStatus` dataclass with `status`, `output_file_id`, `error_file_id`, `request_counts` (plain dict), and `completed_at`.
- `download_output(file_id, dest_path)` — streams the file content to disk via `files.content`, returns `dest_path`.

Terminal statuses are `TERMINAL_STATUSES = frozenset({"completed", "failed", "expired", "cancelled"})`. Use `is_terminal(status)` to check.

All four methods retry on 429 rate-limit errors with the same exponential backoff used by `OpenAICaller` (min 1 s, max 80 s, up to 12 attempts). Request-body construction is shared with `OpenAICaller` via `openai_request_builder.py`; both callers produce byte-identical `responses.create` payloads from `build_responses_request_kwargs`.

Ingestion batch enrichment uses the same structured-output contract as sync
enrichment. `context_enrichment.py` attaches the JSON schema block to each
Batch `/v1/responses` body and validates the returned JSON before mutating
chunk metadata. The Batch client remains transport-only; it does not parse
chunk metadata or decide which fields to keep.
