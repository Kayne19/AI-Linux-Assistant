export type User = {
  id: string;
  username: string;
};

export type AsyncState = "idle" | "loading" | "error";

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

export type UICouncilEntry = {
  entryId: string;
  role: string;
  phase: string;
  round?: number;
  text: string;
  complete: boolean;
  streamBuffer?: string;
  streamPreview?: string;
};

export type ChatRunUIState = {
  runId: string;
  clientRequestId: string;
  pendingContent: string;
  streamStatus: StreamStatusEvent | null;
  streamingAssistantId: number | null;
  optimisticUserId: number;
  optimisticAssistantId: number;
  lastSeenSeq: number;
  councilEntries: UICouncilEntry[];
};

export type PendingTextDeltaBatch = {
  delta: string;
  frameId: number | null;
  lastDrainAt: number | null;
};

export type PendingCouncilDeltaBatch = {
  delta: string;
  frameId: number | null;
};

export type CheckpointSeed = {
  runId: string;
  seq: number;
  text: string;
};

export type PendingDonePayload = {
  payload: {
    user_message: ChatMessage;
    assistant_message: ChatMessage;
  };
  selectedProjectIdAtCompletion: string;
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

export type ChatRunListResponse = {
  runs: ChatRun[];
  total: number;
  page: number;
  page_size: number;
  has_more: boolean;
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

export type RunStateEvent = {
  type: "state";
  seq: number;
  code: string;
  created_at: string;
};

export type RunGenericEvent = {
  type: "event";
  seq: number;
  code: string;
  payload?: Record<string, unknown>;
  created_at: string;
};

export type RunDoneEvent = SendMessageResponse & {
  type: "done";
  seq: number;
  created_at: string;
};

export type RunErrorEvent = {
  type: "error";
  seq: number;
  message: string;
  created_at: string;
};

export type RunCancelledEvent = {
  type: "cancelled";
  seq: number;
  message: string;
  created_at: string;
};

export type RunEvent = RunStateEvent | RunGenericEvent | RunDoneEvent | RunErrorEvent | RunCancelledEvent;

export type BootstrapResponse = {
  user: User;
  projects: Project[];
  chats_by_project: Record<string, ChatSession[]>;
};
