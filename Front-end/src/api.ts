import type {
  BootstrapResponse,
  ChatMessage,
  ChatRun,
  ChatSession,
  Project,
  SendMessageResponse,
  User,
} from "./types";

const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL as string | undefined)?.trim() || "http://localhost:8000";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers || {}),
    },
    ...init,
  });

  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || `Request failed with status ${response.status}`);
  }

  return response.json() as Promise<T>;
}

type BackendStreamEvent =
  | { type: "state"; seq?: number; code: string }
  | { type: "event"; seq?: number; code: string; payload?: Record<string, unknown> }
  | ({ type: "done"; seq?: number } & SendMessageResponse)
  | { type: "error"; seq?: number; message: string }
  | { type: "cancelled"; seq?: number; message: string };

type StreamHandlers = {
  onSequence?: (seq: number) => void;
  onState?: (code: string) => void;
  onEvent?: (code: string, payload?: Record<string, unknown>) => void;
  onTextDelta?: (delta: string) => void;
  onDone?: (payload: SendMessageResponse) => void;
  onError?: (message: string) => void;
  onCancelled?: (message: string) => void;
};

function parseSseEvent(rawChunk: string): BackendStreamEvent | null {
  const dataLines = rawChunk
    .split("\n")
    .filter((line) => line.startsWith("data:"))
    .map((line) => line.slice(5).trim());

  if (dataLines.length === 0) {
    return null;
  }

  return JSON.parse(dataLines.join("\n")) as BackendStreamEvent;
}

async function readEventStream(
  path: string,
  handlers: StreamHandlers = {},
  signal?: AbortSignal,
): Promise<SendMessageResponse | null> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    method: "GET",
    headers: {
      "Content-Type": "application/json",
    },
    signal,
  });

  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || `Request failed with status ${response.status}`);
  }

  if (!response.body) {
    throw new Error("Streaming response body was not available.");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let finalPayload: SendMessageResponse | null = null;

  while (true) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value || new Uint8Array(), { stream: !done });

    let boundaryIndex = buffer.indexOf("\n\n");
    while (boundaryIndex !== -1) {
      const rawChunk = buffer.slice(0, boundaryIndex);
      buffer = buffer.slice(boundaryIndex + 2);
      const event = parseSseEvent(rawChunk);

      if (event) {
        if (typeof event.seq === "number") {
          handlers.onSequence?.(event.seq);
        }
        if (event.type === "state") {
          handlers.onState?.(event.code);
        } else if (event.type === "event") {
          if (event.code === "text_delta") {
            const delta = typeof event.payload?.delta === "string" ? event.payload.delta : "";
            if (delta) {
              handlers.onTextDelta?.(delta);
            }
          }
          handlers.onEvent?.(event.code, event.payload);
        } else if (event.type === "done") {
          finalPayload = {
            user_message: event.user_message,
            assistant_message: event.assistant_message,
            debug: event.debug,
          };
          handlers.onDone?.(finalPayload);
        } else if (event.type === "cancelled") {
          handlers.onCancelled?.(event.message);
          return finalPayload;
        } else if (event.type === "error") {
          handlers.onError?.(event.message);
          throw new Error(event.message);
        }
      }

      boundaryIndex = buffer.indexOf("\n\n");
    }

    if (done) {
      break;
    }
  }

  return finalPayload;
}

export const api = {
  health: () => request<{ ok: boolean }>("/health"),
  login: (username: string) =>
    request<User>("/auth/login", {
      method: "POST",
      body: JSON.stringify({ username }),
    }),
  bootstrap: (username: string) =>
    request<BootstrapResponse>("/auth/bootstrap", {
      method: "POST",
      body: JSON.stringify({ username }),
    }),
  listProjects: (userId: string) => request<Project[]>(`/users/${userId}/projects`),
  createProject: (payload: { user_id: string; name: string; description?: string }) =>
    request<Project>("/projects", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  updateProject: (projectId: string, payload: { name: string; description?: string }) =>
    request<Project>(`/projects/${projectId}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  deleteProject: (projectId: string) =>
    request<{ ok: boolean }>(`/projects/${projectId}`, {
      method: "DELETE",
    }),
  listChats: (projectId: string) => request<ChatSession[]>(`/projects/${projectId}/chats`),
  createChat: (projectId: string, title: string) =>
    request<ChatSession>(`/projects/${projectId}/chats`, {
      method: "POST",
      body: JSON.stringify({ title }),
    }),
  updateChat: (chatId: string, payload: { title: string }) =>
    request<ChatSession>(`/chats/${chatId}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  deleteChat: (chatId: string) =>
    request<{ ok: boolean }>(`/chats/${chatId}`, {
      method: "DELETE",
    }),
  getChat: (chatId: string) => request<ChatSession>(`/chats/${chatId}`),
  listMessages: (chatId: string) => request<ChatMessage[]>(`/chats/${chatId}/messages`),
  sendMessage: (chatId: string, content: string, clientRequestId?: string) =>
    request<SendMessageResponse>(`/chats/${chatId}/messages`, {
      method: "POST",
      body: JSON.stringify({ content, client_request_id: clientRequestId || "" }),
    }),
  createRun: (chatId: string, content: string, options: { magi?: string; clientRequestId?: string } = {}) =>
    request<ChatRun>(`/chats/${chatId}/runs`, {
      method: "POST",
      body: JSON.stringify({
        content,
        magi: options.magi || "off",
        client_request_id: options.clientRequestId || "",
      }),
    }),
  getRun: (runId: string) => request<ChatRun>(`/runs/${runId}`),
  cancelRun: (runId: string) =>
    request<ChatRun>(`/runs/${runId}/cancel`, {
      method: "POST",
    }),
  streamRun: (runId: string, handlers: StreamHandlers = {}, options: { afterSeq?: number; signal?: AbortSignal } = {}) =>
    readEventStream(`/runs/${runId}/events/stream?after_seq=${Math.max(0, options.afterSeq || 0)}`, handlers, options.signal),
  streamMessage: async (
    chatId: string,
    content: string,
    handlers: StreamHandlers = {},
    magi: string = "off",
    clientRequestId?: string,
    signal?: AbortSignal,
  ) => {
    const response = await fetch(`${API_BASE_URL}/chats/${chatId}/messages/stream`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ content, magi, client_request_id: clientRequestId || "" }),
      signal,
    });

    if (!response.ok) {
      const text = await response.text();
      throw new Error(text || `Request failed with status ${response.status}`);
    }

    if (!response.body) {
      throw new Error("Streaming response body was not available.");
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let finalPayload: SendMessageResponse | null = null;

    while (true) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value || new Uint8Array(), { stream: !done });

      let boundaryIndex = buffer.indexOf("\n\n");
      while (boundaryIndex !== -1) {
        const rawChunk = buffer.slice(0, boundaryIndex);
        buffer = buffer.slice(boundaryIndex + 2);
        const event = parseSseEvent(rawChunk);

        if (event) {
          if (typeof event.seq === "number") {
            handlers.onSequence?.(event.seq);
          }
          if (event.type === "state") {
            handlers.onState?.(event.code);
          } else if (event.type === "event") {
            if (event.code === "text_delta") {
              const delta = typeof event.payload?.delta === "string" ? event.payload.delta : "";
              if (delta) {
                handlers.onTextDelta?.(delta);
              }
            }
            handlers.onEvent?.(event.code, event.payload);
          } else if (event.type === "done") {
            finalPayload = {
              user_message: event.user_message,
              assistant_message: event.assistant_message,
              debug: event.debug,
            };
            handlers.onDone?.(finalPayload);
          } else if (event.type === "cancelled") {
            handlers.onCancelled?.(event.message);
            return finalPayload;
          } else if (event.type === "error") {
            handlers.onError?.(event.message);
            throw new Error(event.message);
          }
        }

        boundaryIndex = buffer.indexOf("\n\n");
      }

      if (done) {
        break;
      }
    }

    return finalPayload;
  },
};
