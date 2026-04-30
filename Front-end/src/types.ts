export type RunMode = "off" | "lite" | "full";
export type RunResumeInputKind =
	| "fact"
	| "correction"
	| "constraint"
	| "goal_clarification";

export type User = {
	id: string;
	username?: string | null;
	display_name: string;
	email: string;
	email_verified: boolean;
	avatar_url: string;
	role: "user" | "admin";
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
	entry_kind?: "role" | "user_intervention" | string;
	input_kind?: RunResumeInputKind | string;
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
	entryKind?: "role" | "user_intervention" | string;
	inputKind?: RunResumeInputKind | string;
};

export type ChatRunUIState = {
	runId: string;
	clientRequestId: string;
	pendingContent: string;
	streamStatus: StreamStatusEvent | null;
	canPauseRun: boolean;
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
	lastDrainAt: number | null;
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
		debug: AssistantDebug;
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
	run_kind: string;
	request_content: string;
	magi: RunMode;
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
	normalized_inputs?: NormalizedInputs | null;
};

export type ChatRunListResponse = {
	runs: ChatRun[];
	total: number;
	page: number;
	page_size: number;
	has_more: boolean;
};

export type NormalizedTurnEntry = {
	role: string;
	content: string;
};

export type RetrievedContextBlock = {
	source: string;
	pages: number[];
	page_label: string;
	text: string;
	section_path?: string[] | null;
	section_title?: string | null;
	chunk_type?: string | null;
	local_subsystems?: string[] | null;
	entities?: Record<string, string[]> | null;
	canonical_source_id?: string | null;
	page_start?: number | null;
	page_end?: number | null;
	citation_label?: string | null;
};

export type NormalizedInputs = {
	request_text: string;
	conversation_summary_text: string;
	recent_turns: NormalizedTurnEntry[];
	memory_snapshot_text: string;
	retrieval_query: string;
	retrieved_context_text: string;
	retrieved_context_blocks: RetrievedContextBlock[];
};

export type AssistantDebug = {
	state_trace: string[];
	tool_events: Array<Record<string, unknown>>;
	retrieval_query: string;
	retrieved_sources: string[];
	auto_name_scheduled?: boolean;
	normalized_inputs?: NormalizedInputs | null;
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

export type RunEvent =
	| RunStateEvent
	| RunGenericEvent
	| RunDoneEvent
	| RunErrorEvent
	| { type: "cancelled"; seq: number; message: string; created_at: string }
	| {
			type: "paused";
			seq: number;
			message: string;
			created_at: string;
			payload?: Record<string, unknown>;
	  };

export type AppBootstrapResponse = {
	user: User;
	projects: Project[];
	chats_by_project: Record<string, ChatSession[]>;
};

// ---------------------------------------------------------------------------
// Admin settings types
// ---------------------------------------------------------------------------

export const COMPONENT_KEYS = [
	"classifier",
	"contextualizer",
	"responder",
	"magi_eager",
	"magi_skeptic",
	"magi_historian",
	"magi_arbiter",
	"magi_lite_eager",
	"magi_lite_skeptic",
	"magi_lite_historian",
	"magi_lite_arbiter",
	"history_summarizer",
	"context_summarizer",
	"memory_extractor",
	"registry_updater",
	"ingest_enricher",
	"chat_namer",
] as const;

export type ComponentKey = (typeof COMPONENT_KEYS)[number];

export type ComponentSettings = {
	provider: string;
	model: string;
	reasoning_effort: string;
	is_default: boolean;
};

export type ComponentSettingsPatch = {
	provider?: string;
	model?: string;
	reasoning_effort?: string;
};

// ---------------------------------------------------------------------------
// Numeric settings — retrieval and history context
// ---------------------------------------------------------------------------

export type NumericSetting = {
	value: number;
	is_default: boolean;
};

export type RetrievalSettings = {
	initial_fetch: NumericSetting;
	final_top_k: NumericSetting;
	neighbor_pages: NumericSetting;
	max_expanded: NumericSetting;
	source_profile_sample: NumericSetting;
};

export type HistoryContextSettings = {
	max_recent_turns: NumericSetting;
	summarize_turn_threshold: NumericSetting;
	summarize_char_threshold: NumericSetting;
};

export type RetrievalSettingsPatch = {
	initial_fetch?: number;
	final_top_k?: number;
	neighbor_pages?: number;
	max_expanded?: number;
	source_profile_sample?: number;
};

export type HistoryContextSettingsPatch = {
	max_recent_turns?: number;
	summarize_turn_threshold?: number;
	summarize_char_threshold?: number;
};

export type AppSettingsConfig = {
	[K in ComponentKey]: ComponentSettings;
} & {
	retrieval: RetrievalSettings;
	history_context: HistoryContextSettings;
};

export type AppSettingsPatch = Partial<
	Record<ComponentKey, ComponentSettingsPatch>
> & {
	retrieval?: RetrievalSettingsPatch;
	history_context?: HistoryContextSettingsPatch;
};
