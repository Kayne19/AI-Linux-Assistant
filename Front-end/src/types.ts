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
  active_run_id?: string | null;
  active_run_status?: string | null;
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

export type ChatRun = {
  id: string;
  chat_session_id: string;
  project_id: string;
  user_id: string;
  status: string;
  request_content: string;
  magi: string;
  client_request_id: string;
  latest_state_code: string;
  latest_event_seq: number;
  partial_assistant_text: string;
  error_message: string;
  worker_id: string;
  cancel_requested: boolean;
  lease_expires_at: string;
  started_at: string;
  finished_at: string;
  created_at: string;
  updated_at: string;
  final_user_message_id?: number | null;
  final_assistant_message_id?: number | null;
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
