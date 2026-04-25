import type { ChatRun, RunEvent } from "../types";

export const ACTIVE_RUN_STATUSES = new Set(["queued", "running", "cancel_requested", "pause_requested", "paused"]);
export const STREAMING_CODES = new Set([
  "text_delta",
  "text_checkpoint",
  "magi_role_text_delta",
  "magi_role_text_checkpoint",
]);
export const SUBSYSTEM_STATE_CODES = new Set(["responder_state", "magi_state"]);
const CONTEXT_EVENT_CODES = new Set(["summarized_conversation_history", "summarized_retrieved_docs"]);
const RETRIEVAL_TOOL_NAMES = new Set(["search_rag_database", "search_RAG_database"]);
export const DEBUG_TABS = ["Timeline", "States", "Context", "Retrieval", "Memory", "Streaming", "Raw"] as const;

export type DebugTab = (typeof DEBUG_TABS)[number];

export function isRetrievalToolName(name: unknown): boolean {
  return typeof name === "string" && RETRIEVAL_TOOL_NAMES.has(name);
}

function payloadNamesIncludeRetrievalTool(payload: Record<string, unknown>): boolean {
  return Array.isArray(payload.names) && payload.names.some((name) => isRetrievalToolName(name));
}

function formatStringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string" && Boolean(item)) : [];
}

function summarizeRetrievalToolScope(payload: Record<string, unknown>): string {
  const args = isObjectRecord(payload.args)
    ? payload.args
    : (isObjectRecord(payload.tool_args) ? payload.tool_args : {});
  const scopeHints = isObjectRecord(args.scope_hints) ? args.scope_hints : {};
  const scopeParts = [
    ["os", scopeHints.os_family],
    ["src", scopeHints.source_family],
    ["pkg", scopeHints.package_managers],
    ["init", scopeHints.init_systems],
    ["subs", scopeHints.major_subsystems],
  ]
    .map(([label, value]) => {
      const values = Array.isArray(value) ? value.filter(Boolean) : (value ? [value] : []);
      return values.length > 0 ? `${label}=${values.join(",")}` : "";
    })
    .filter(Boolean);
  const ids = formatStringArray(args.canonical_source_ids);
  return [
    scopeParts.length > 0 ? `scope_hints: ${scopeParts.join(",")}` : "",
    ids.length > 0 ? `ids=${ids.length}` : "",
  ].filter(Boolean).join(" • ");
}

export function isRetrievalToolEvent(event: RunEvent): boolean {
  if (event.type !== "event" || !event.code.startsWith("tool_") || !isObjectRecord(event.payload)) {
    return false;
  }
  return isRetrievalToolName(event.payload.name);
}

export function getRetrievalEvents(events: RunEvent[]): RunEvent[] {
  const retrievalRounds = new Set<number>();

  for (const event of events) {
    if (event.type !== "event" || event.code !== "tool_calls_received" || !isObjectRecord(event.payload)) {
      continue;
    }
    if (!payloadNamesIncludeRetrievalTool(event.payload)) {
      continue;
    }
    if (typeof event.payload.round === "number") {
      retrievalRounds.add(event.payload.round);
    }
  }

  return events.filter((event) => {
    if (event.type === "event" && event.code.startsWith("retrieval_")) {
      return true;
    }
    if (isRetrievalToolEvent(event)) {
      return true;
    }
    if (event.type !== "event" || !isObjectRecord(event.payload)) {
      return false;
    }
    if (event.code === "tool_calls_received") {
      return payloadNamesIncludeRetrievalTool(event.payload);
    }
    if ((event.code === "request_submitted" || event.code === "tool_results_submitted") && typeof event.payload.round === "number") {
      return retrievalRounds.has(event.payload.round);
    }
    return false;
  });
}

export const TAB_FILTERS: Record<DebugTab, (event: RunEvent) => boolean> = {
  Timeline: () => true,
  States: (event) => event.type === "state" || (event.type === "event" && !STREAMING_CODES.has(event.code)),
  Context: (event) => event.type === "event" && CONTEXT_EVENT_CODES.has(event.code),
  Retrieval: (event) =>
    (event.type === "event" && event.code.startsWith("retrieval_"))
      || isRetrievalToolEvent(event)
      || (event.type === "event" && event.code === "tool_calls_received" && isObjectRecord(event.payload) && payloadNamesIncludeRetrievalTool(event.payload)),
  Memory: (event) => (event.type === "state" || event.type === "event") && event.code.includes("memory"),
  Streaming: (event) => event.type === "event" && STREAMING_CODES.has(event.code),
  Raw: () => true,
};

export type NestedStateRow = {
  kind: "nested";
  key: string;
  event: RunEvent;
  durationMs: number | null;
  phase: string;
  stateCode: string;
  details: Record<string, unknown>;
};

export type StateRow = {
  kind: "router" | "subsystem";
  key: string;
  event: RunEvent;
  durationMs: number | null;
  nestedRows: NestedStateRow[];
  phase?: string;
  stateCode?: string;
  details?: Record<string, unknown>;
};

type RunTimings = {
  queueWaitMs: number | null;
  firstEventLatencyMs: number | null;
  firstTextDeltaLatencyMs: number | null;
  totalDurationMs: number | null;
  timeInCurrentStateMs: number | null;
};

function parseTimeMs(value?: string | null): number | null {
  if (!value) {
    return null;
  }
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed : null;
}

export function isObjectRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function parseSubsystemStatePayload(event: RunEvent) {
  if (event.type !== "event" || !SUBSYSTEM_STATE_CODES.has(event.code) || !isObjectRecord(event.payload)) {
    return null;
  }
  const payload = event.payload;
  const phase = typeof payload.phase === "string"
    ? payload.phase
    : (event.code === "responder_state" ? "responder" : "magi");
  const stateCode = typeof payload.state === "string" ? payload.state : "";
  const details = isObjectRecord(payload.details)
    ? payload.details
    : (isObjectRecord(payload.payload) ? payload.payload : {});
  return {
    phase,
    stateCode,
    details,
  };
}

function eventPhase(event: RunEvent): string {
  if (event.type === "state") {
    return "router";
  }
  if (event.type === "paused") {
    return "run";
  }
  if (event.type !== "event") {
    return event.type;
  }
  const subsystem = parseSubsystemStatePayload(event);
  if (subsystem) {
    return subsystem.phase;
  }
  if (isRetrievalToolEvent(event)) {
    return "retrieval";
  }
  if (event.code === "retrieval_scope_selected") {
    return "retrieval";
  }
  if (event.code.startsWith("retrieval_")) {
    return "retrieval";
  }
  if (event.code.startsWith("memory_")) {
    return "memory";
  }
  if (event.code.startsWith("chat_name") || event.code === "chat_named" || event.code === "auto_name_scheduled") {
    return "naming";
  }
  if (event.code.startsWith("magi_")) {
    return "magi";
  }
  if (event.code === "pause_requested") {
    return "run";
  }
  if (event.code.startsWith("tool_")) {
    return "tool";
  }
  if (["request_submitted", "web_search_used", "tool_calls_received", "tool_results_submitted", "response_completed", "structured_output_warning"].includes(event.code)) {
    return "provider";
  }
  return "event";
}

function summarizeGenericEventPayload(event: RunEvent): string {
  if (event.type === "paused") {
    return event.message || "paused";
  }
  if (event.type !== "event") {
    return "";
  }
  const payload = isObjectRecord(event.payload) ? event.payload : {};
  switch (event.code) {
    case "request_submitted":
      return typeof payload.round === "number" ? `round ${payload.round}` : "request sent";
    case "web_search_used":
      return [
        typeof payload.provider === "string" ? payload.provider : "",
        typeof payload.count === "number" ? `${payload.count} search${payload.count === 1 ? "" : "es"}` : "",
        typeof payload.round === "number" ? `round ${payload.round}` : "",
      ].filter(Boolean).join(" • ");
    case "tool_calls_received":
      return [
        typeof payload.round === "number" ? `round ${payload.round}` : "",
        typeof payload.count === "number" ? `${payload.count} tool call${payload.count === 1 ? "" : "s"}` : "",
        Array.isArray(payload.names) ? payload.names.join(", ") : "",
      ].filter(Boolean).join(" • ");
    case "tool_results_submitted":
      return typeof payload.round === "number" ? `round ${payload.round}` : "tool results submitted";
    case "response_completed":
      return typeof payload.tool_rounds === "number"
        ? `${payload.tool_rounds} tool round${payload.tool_rounds === 1 ? "" : "s"}`
        : "response complete";
    case "structured_output_warning":
      return [
        typeof payload.provider === "string" ? payload.provider : "",
        typeof payload.schema_name === "string" ? payload.schema_name : "",
        typeof payload.reason === "string" ? payload.reason : "",
        payload.used_prompt_fallback === true ? "prompt fallback" : "",
      ].filter(Boolean).join(" • ");
    case "retrieval_scope_selected":
      return [
        typeof payload.candidate_count === "number" ? `scope: ${payload.candidate_count} docs` : "scope selected",
        typeof payload.widenings_taken === "number" ? `widenings=${payload.widenings_taken}` : "",
      ].filter(Boolean).join(" • ");
    case "tool_start":
      if (typeof payload.name === "string" && RETRIEVAL_TOOL_NAMES.has(payload.name)) {
        const args = isObjectRecord(payload.args)
          ? payload.args
          : (isObjectRecord(payload.tool_args) ? payload.tool_args : {});
        return [
          "retrieval tool start",
          typeof args.query === "string" ? args.query : "",
          summarizeRetrievalToolScope(payload),
        ].filter(Boolean).join(" • ");
      }
      return [
        typeof payload.name === "string" ? payload.name : "",
        isObjectRecord(payload.args) ? JSON.stringify(payload.args) : "",
      ].filter(Boolean).join(" • ");
    case "tool_complete":
      if (typeof payload.name === "string" && RETRIEVAL_TOOL_NAMES.has(payload.name)) {
        return [
          "retrieval tool complete",
          Array.isArray(payload.result_blocks) ? `${payload.result_blocks.length} block${payload.result_blocks.length === 1 ? "" : "s"}` : "",
          typeof payload.result_size === "number" ? `${payload.result_size} chars` : "",
          summarizeRetrievalToolScope(payload),
        ].filter(Boolean).join(" • ");
      }
      return [
        typeof payload.name === "string" ? payload.name : "",
        typeof payload.result_size === "number" ? `${payload.result_size} chars` : "",
      ].filter(Boolean).join(" • ");
    case "tool_error":
      if (typeof payload.name === "string" && RETRIEVAL_TOOL_NAMES.has(payload.name)) {
        return [
          "retrieval tool error",
          typeof payload.error === "string" ? payload.error : "",
        ].filter(Boolean).join(" • ");
      }
      return [
        typeof payload.name === "string" ? payload.name : "",
        typeof payload.error === "string" ? payload.error : "",
      ].filter(Boolean).join(" • ");
    case "retrieval_search_started":
      return [
        typeof payload.query === "string" && payload.query ? payload.query : "",
        Array.isArray(payload.sources) && payload.sources.length > 0 ? `${payload.sources.length} source filter${payload.sources.length === 1 ? "" : "s"}` : "",
      ].filter(Boolean).join(" • ");
    case "retrieval_candidates_found":
      return [
        typeof payload.count === "number" ? `${payload.count} candidate${payload.count === 1 ? "" : "s"}` : "",
        typeof payload.initial_fetch === "number" ? `fetch=${payload.initial_fetch}` : "",
      ].filter(Boolean).join(" • ");
    case "retrieval_sources_filtered":
      return [
        typeof payload.count === "number" ? `${payload.count} remaining` : "",
        Array.isArray(payload.sources) ? payload.sources.join(", ") : "",
      ].filter(Boolean).join(" • ");
    case "retrieval_reranking":
      return typeof payload.count === "number" ? `${payload.count} candidate${payload.count === 1 ? "" : "s"}` : "reranking";
    case "retrieval_source_boosting":
      return typeof payload.sources === "number" ? `${payload.sources} source profile${payload.sources === 1 ? "" : "s"}` : "source boosting";
    case "retrieval_expanding":
      return [
        typeof payload.neighbor_pages === "number" ? `neighbors=${payload.neighbor_pages}` : "",
        typeof payload.max_expanded === "number" ? `max=${payload.max_expanded}` : "",
      ].filter(Boolean).join(" • ");
    case "retrieval_complete":
      return [
        typeof payload.merged_blocks === "number" ? `${payload.merged_blocks} merged block${payload.merged_blocks === 1 ? "" : "s"}` : "",
        Array.isArray(payload.selected_sources) ? payload.selected_sources.join(", ") : "",
      ].filter(Boolean).join(" • ");
    case "memory_loaded":
      return [
        typeof payload.chars === "number" ? `${payload.chars} chars` : "",
        payload.has_memory === true ? "loaded" : "empty",
      ].filter(Boolean).join(" • ");
    case "memory_extracted":
      return [
        typeof payload.facts === "number" ? `facts=${payload.facts}` : "",
        typeof payload.issues === "number" ? `issues=${payload.issues}` : "",
        typeof payload.attempts === "number" ? `attempts=${payload.attempts}` : "",
        typeof payload.constraints === "number" ? `constraints=${payload.constraints}` : "",
        typeof payload.preferences === "number" ? `preferences=${payload.preferences}` : "",
      ].filter(Boolean).join(" • ");
    case "memory_resolved":
    case "memory_committed": {
      const committed = isObjectRecord(payload.committed) ? payload.committed : {};
      return [
        typeof committed.facts === "number" ? `facts=${committed.facts}` : "",
        typeof committed.issues === "number" ? `issues=${committed.issues}` : "",
        typeof payload.candidates === "number" ? `candidates=${payload.candidates}` : "",
        typeof payload.conflicts === "number" ? `conflicts=${payload.conflicts}` : "",
      ].filter(Boolean).join(" • ");
    }
    case "memory_skipped":
      return typeof payload.reason === "string" ? payload.reason : "skipped";
    case "memory_error":
      return [
        typeof payload.phase === "string" ? payload.phase : "",
        typeof payload.error === "string" ? payload.error : "",
      ].filter(Boolean).join(" • ");
    case "auto_name_scheduled":
      return typeof payload.mode === "string" ? payload.mode : "scheduled";
    case "chat_named":
      return typeof payload.title === "string" ? payload.title : "title updated";
    case "chat_name_skipped":
      return typeof payload.reason === "string" ? payload.reason : "skipped";
    case "chat_name_error":
      return typeof payload.error === "string" ? payload.error : "title error";
    case "magi_discussion_gate":
      return [
        payload.force_discussion === true ? "forced" : "skipped",
        typeof payload.reason === "string" ? payload.reason : "",
        typeof payload.grounding_strength === "string" ? `grounding=${payload.grounding_strength}` : "",
      ].filter(Boolean).join(" • ");
    case "magi_discussion_round":
      return [
        typeof payload.round === "number" ? `round ${payload.round}` : "",
        Array.isArray(payload.contributors) ? `contributors=${payload.contributors.join(",") || "none"}` : "",
        payload.early_stop === true ? "early stop" : "",
      ].filter(Boolean).join(" • ");
    case "magi_synthesis_complete":
      return [
        typeof payload.decision_mode === "string" ? payload.decision_mode : "",
        typeof payload.uncertainty_level === "string" ? `uncertainty=${payload.uncertainty_level}` : "",
        typeof payload.winning_branch === "string" && payload.winning_branch ? payload.winning_branch : "",
      ].filter(Boolean).join(" • ");
    case "magi_intervention_added":
      return [
        typeof payload.input_kind === "string" ? payload.input_kind : "input",
        typeof payload.seq === "number" ? `seq ${payload.seq}` : "",
      ].filter(Boolean).join(" • ");
    default:
      return event.payload ? JSON.stringify(event.payload).slice(0, 220) : "";
  }
}

function summarizeDetails(details: Record<string, unknown>): string {
  const summaryParts: string[] = [];
  const round = details.round;
  if (typeof round === "number") {
    summaryParts.push(`round ${round}`);
  }
  const count = details.count;
  if (typeof count === "number") {
    summaryParts.push(`${count} item${count === 1 ? "" : "s"}`);
  }
  const toolRounds = details.tool_rounds;
  if (typeof toolRounds === "number") {
    summaryParts.push(`${toolRounds} tool round${toolRounds === 1 ? "" : "s"}`);
  }
  const names = details.names;
  if (Array.isArray(names) && names.length > 0) {
    summaryParts.push(names.join(", "));
  }

  if (summaryParts.length > 0) {
    return summaryParts.join(" • ");
  }

  const entries = Object.entries(details)
    .filter(([key]) => !["round", "count", "tool_rounds", "names"].includes(key))
    .slice(0, 4);
  if (entries.length === 0) {
    return "";
  }
  return entries
    .map(([key, value]) => `${key}=${typeof value === "string" ? value : JSON.stringify(value)}`)
    .join(" • ");
}

export function isActiveRunStatus(status: string): boolean {
  return ACTIVE_RUN_STATUSES.has(status);
}

export function truncateMiddle(value: string, start = 8, end = 6): string {
  if (!value) {
    return "—";
  }
  if (value.length <= start + end + 3) {
    return value;
  }
  return `${value.slice(0, start)}…${value.slice(-end)}`;
}

export function mergeRunEvents(currentEvents: RunEvent[], nextEvents: RunEvent[]): RunEvent[] {
  const merged = new Map<number, RunEvent>();
  for (const event of currentEvents) {
    merged.set(event.seq, event);
  }
  for (const event of nextEvents) {
    merged.set(event.seq, event);
  }
  return Array.from(merged.values()).sort((left, right) => left.seq - right.seq);
}

export function computeTimings(run: ChatRun, events: RunEvent[], nowMs: number = Date.now()): RunTimings {
  const createdMs = parseTimeMs(run.created_at);
  const startedMs = parseTimeMs(run.started_at);
  const finishedMs = parseTimeMs(run.finished_at);
  const eventTimes = events
    .map((event) => parseTimeMs(event.created_at))
    .filter((value): value is number => value !== null);
  const firstEventMs = eventTimes.length > 0 ? Math.min(...eventTimes) : null;
  const firstTextDeltaTimes = events
    .filter((event) => event.type === "event" && event.code === "text_delta")
    .map((event) => parseTimeMs(event.created_at))
    .filter((value): value is number => value !== null);
  const firstTextDeltaMs = firstTextDeltaTimes.length > 0 ? Math.min(...firstTextDeltaTimes) : null;
  const stateEvents = events.filter((event) => event.type === "state");
  const latestStateEvent = stateEvents.length > 0 ? stateEvents[stateEvents.length - 1] : null;
  const latestStateMs = latestStateEvent ? parseTimeMs(latestStateEvent.created_at) : null;

  return {
    queueWaitMs: createdMs !== null && startedMs !== null ? startedMs - createdMs : null,
    firstEventLatencyMs: startedMs !== null && firstEventMs !== null ? firstEventMs - startedMs : null,
    firstTextDeltaLatencyMs: startedMs !== null && firstTextDeltaMs !== null ? firstTextDeltaMs - startedMs : null,
    totalDurationMs: startedMs !== null ? (finishedMs ?? nowMs) - startedMs : null,
    timeInCurrentStateMs:
      isActiveRunStatus(run.status) && latestStateMs !== null ? Math.max(0, nowMs - latestStateMs) : null,
  };
}

export function getStateRows(events: RunEvent[]): StateRow[] {
  const filteredEvents = events.filter(TAB_FILTERS.States);
  const rows: StateRow[] = [];
  let currentRouterRow: StateRow | null = null;

  for (const event of filteredEvents) {
    if (event.type === "state") {
      const row: StateRow = {
        kind: "router",
        key: `state:${event.seq}`,
        event,
        durationMs: null,
        nestedRows: [],
      };
      rows.push(row);
      currentRouterRow = row;
      continue;
    }

    const parsed = parseSubsystemStatePayload(event);
    const nestedRow: NestedStateRow = {
      kind: "nested",
      key: `substate:${event.seq}`,
      event,
      durationMs: null,
      phase: parsed?.phase || eventPhase(event),
      stateCode: parsed?.stateCode || eventTitle(event) || (event.type === "event" ? event.code : event.type),
      details: parsed?.details || (event.type === "event" && isObjectRecord(event.payload) ? event.payload : {}),
    };
    if (currentRouterRow) {
      currentRouterRow.nestedRows.push(nestedRow);
      continue;
    }
    rows.push({
      kind: "subsystem",
      key: `subsystem:${event.seq}`,
      event,
      durationMs: null,
      nestedRows: [],
      phase: parsed?.phase || eventPhase(event),
      stateCode: parsed?.stateCode || eventTitle(event) || (event.type === "event" ? event.code : event.type),
      details: parsed?.details || (event.type === "event" && isObjectRecord(event.payload) ? event.payload : {}),
    });
  }

  for (let index = 0; index < rows.length; index += 1) {
    const row = rows[index];
    const currentMs = parseTimeMs(row.event.created_at);
    const nextMs = parseTimeMs(rows[index + 1]?.event.created_at);
    row.durationMs = currentMs !== null && nextMs !== null ? Math.max(0, nextMs - currentMs) : null;

    const parentEndMs = nextMs;
    for (let nestedIndex = 0; nestedIndex < row.nestedRows.length; nestedIndex += 1) {
      const nestedRow = row.nestedRows[nestedIndex];
      const nestedMs = parseTimeMs(nestedRow.event.created_at);
      const nextNestedMs = parseTimeMs(row.nestedRows[nestedIndex + 1]?.event.created_at);
      const targetMs = nextNestedMs ?? parentEndMs;
      nestedRow.durationMs = nestedMs !== null && targetMs !== null ? Math.max(0, targetMs - nestedMs) : null;
    }
  }

  return rows;
}

export function formatDuration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined || Number.isNaN(ms)) {
    return "—";
  }
  const absolute = Math.max(0, Math.round(ms));
  if (absolute < 1000) {
    return `${absolute}ms`;
  }
  if (absolute < 60000) {
    const seconds = absolute / 1000;
    return `${seconds < 10 ? seconds.toFixed(1) : seconds.toFixed(0)}s`;
  }
  const totalSeconds = Math.floor(absolute / 1000);
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  if (minutes < 60) {
    return `${minutes}m ${seconds}s`;
  }
  const hours = Math.floor(minutes / 60);
  const remMinutes = minutes % 60;
  return `${hours}h ${remMinutes}m`;
}

export function formatTimestamp(value?: string | null): string {
  if (!value) {
    return "—";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    second: "2-digit",
  }).format(date);
}

export function formatRelativeStart(value?: string | null, nowMs: number = Date.now()): string {
  const parsed = parseTimeMs(value);
  if (parsed === null) {
    return "—";
  }
  return formatDuration(nowMs - parsed);
}

export function getLatencyTone(ms: number | null | undefined): "ok" | "warn" | "bad" | "neutral" {
  if (ms === null || ms === undefined || Number.isNaN(ms)) {
    return "neutral";
  }
  if (ms < 1000) {
    return "ok";
  }
  if (ms <= 3000) {
    return "warn";
  }
  return "bad";
}

export function getLeaseRemainingMs(run: ChatRun, nowMs: number = Date.now()): number | null {
  const leaseMs = parseTimeMs(run.lease_expires_at);
  if (leaseMs === null || !isActiveRunStatus(run.status)) {
    return null;
  }
  return leaseMs - nowMs;
}

export function getLeaseTone(ms: number | null | undefined): "ok" | "warn" | "bad" | "neutral" {
  if (ms === null || ms === undefined) {
    return "neutral";
  }
  if (ms > 30000) {
    return "ok";
  }
  if (ms >= 10000) {
    return "warn";
  }
  return "bad";
}

export function eventTitle(event: RunEvent): string {
  const subsystemState = parseSubsystemStatePayload(event);
  if (subsystemState) {
    const prefix = subsystemState.phase === "magi" ? "Magi" : "Responder";
    return `${prefix}.${subsystemState.stateCode || "UNKNOWN"}`;
  }
  if (isRetrievalToolEvent(event) && event.type === "event" && isObjectRecord(event.payload)) {
    return `retrieval_tool.${event.code.replace("tool_", "")}`;
  }
  if (event.type === "event" && event.code === "retrieval_scope_selected") {
    return "retrieval.scope_selected";
  }
  if (event.type === "state") {
    return event.code;
  }
  if (event.type === "error" || event.type === "cancelled") {
    return event.type;
  }
  if (event.type === "done") {
    return "done";
  }
  if (event.type === "event") {
    return event.code;
  }
  return "";
}

export function eventSummary(event: RunEvent): string {
  const subsystemState = parseSubsystemStatePayload(event);
  if (subsystemState) {
    return summarizeDetails(subsystemState.details) || subsystemState.stateCode || "UNKNOWN";
  }
  if (event.type === "error" || event.type === "cancelled") {
    return event.message || "No message";
  }
  if (event.type === "done") {
    const assistantText = event.assistant_message?.content || "";
    return assistantText ? assistantText.slice(0, 160) : "Run completed.";
  }
  if (event.type === "state") {
    return event.code;
  }
  if (event.type === "event") {
    return summarizeGenericEventPayload(event);
  }
  return "";
}

export function summarizeNestedStateRows(rows: NestedStateRow[]): string {
  if (rows.length === 0) {
    return "";
  }
  const phaseCounts = rows.reduce<Record<string, number>>((counts, row) => {
    counts[row.phase] = (counts[row.phase] || 0) + 1;
    return counts;
  }, {});
  return Object.entries(phaseCounts)
    .map(([phase, count]) => `${phase} ${count}`)
    .join(" • ");
}

export function hasErrorBanner(run: ChatRun): boolean {
  return (run.status === "failed" || run.status === "cancelled") && Boolean(run.error_message);
}

export function toneForStatus(status: string): "ok" | "warn" | "bad" | "neutral" {
  if (status === "completed") {
    return "ok";
  }
  if (status === "failed" || status === "cancelled") {
    return "bad";
  }
  if (status === "cancel_requested" || status === "pause_requested" || status === "queued") {
    return "warn";
  }
  if (status === "running" || status === "paused") {
    return "ok";
  }
  return "neutral";
}
