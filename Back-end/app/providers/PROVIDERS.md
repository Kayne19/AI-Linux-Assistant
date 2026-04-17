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
- OpenAI maps that request to Responses structured output via `text.format`
- Anthropic maps that request to Messages structured output via `output_config.format`
- Local models currently warn and fall back to prompt-and-parse behavior
- if a provider cannot honor native structured output, it should emit a compact `structured_output_warning` event before using the prompt fallback path
