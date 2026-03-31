export type User = {
  id: string;
  username: string;
};

export type Project = {
  id: string;
  user_id: string;
  name: string;
  description: string;
  created_at: string;
  updated_at: string;
};

export type ChatSession = {
  id: string;
  project_id: string;
  title: string;
  created_at: string;
  updated_at: string;
};

export type CouncilEntry = {
  role: string;
  phase: string;
  round?: number | null;
  text: string;
};

export type ChatMessage = {
  id: number;
  session_id: string;
  role: "user" | "model" | "assistant";
  content: string;
  created_at: string;
  council_entries?: CouncilEntry[] | null;
};

export type StreamStatusEvent = {
  source: "state" | "event";
  code: string;
  payload?: Record<string, unknown>;
};

export type AssistantDebug = {
  state_trace: string[];
  tool_events: Array<Record<string, unknown>>;
  retrieval_query: string;
  retrieved_sources: string[];
};

export type SendMessageResponse = {
  user_message: ChatMessage;
  assistant_message: ChatMessage;
  debug: AssistantDebug;
};

export type BootstrapResponse = {
  user: User;
  projects: Project[];
  chats_by_project: Record<string, ChatSession[]>;
};
