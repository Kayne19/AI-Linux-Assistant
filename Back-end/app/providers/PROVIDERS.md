# Provider Adapters

This document defines the interface and responsibilities for LLM provider adapters in the `Back-end/app/providers` package.

## Core Principle: Providers Own Transport Only

Provider adapters should only own the mechanics of communicating with external or local model APIs. They are implementation details injected into task-shaped agents.

### Responsibilities
- **Request Formatting**: Mapping the project's internal message and tool structures to the provider's specific API format.
- **Response Parsing**: Converting provider-specific outputs (text, tool calls, usage metadata) back into internal system models.
- **Tool-Call Transport**: Handling the specific semantics of how the provider expects tool definitions and returns tool results.
- **Reliability**: Implementing provider-specific retries, timeouts, and error handling.

### Prohibitions
Providers MUST NOT own:
- **Router State**: They should not decide which phase runs next or modify the FSM.
- **Memory Policy**: They should not decide what gets committed to project memory.
- **Project/Session Scoping**: Scope is enforced by the backend, not the provider.
- **Policy Decisions**: They should not decide whether RAG is required or if a user is authorized.

## Supported Providers
- **Anthropic**: Adapter for the Messages API.
- **OpenAI**: Adapter for the Chat Completions API.
- **Local**: Adapter for locally hosted models (e.g., via Ollama or vLLM).

## Adding a New Provider
To add a new provider, implement a caller that satisfies the expected interface used by the agents. Ensure that all internal models are correctly mapped and that streaming deltas are handled consistently with the rest of the system.
