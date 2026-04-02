import type { ChatRun, RunEvent } from "../types";

export const ACTIVE_RUN_STATUSES = new Set(["queued", "running", "cancel_requested"]);
export const STREAMING_CODES = new Set([
  "text_delta",
  "text_checkpoint",
  "magi_role_text_delta",
  "magi_role_text_checkpoint",
]);
export const SUBSYSTEM_STATE_CODES = new Set(["responder_state", "magi_state"]);
export const DEBUG_TABS = ["Timeline", "States", "Retrieval", "Memory", "Streaming", "Raw"] as const;

export type DebugTab = (typeof DEBUG_TABS)[number];

export const TAB_FILTERS: Record<DebugTab, (event: RunEvent) => boolean> = {
  Timeline: () => true,
  States: (event) => event.type === "state" || (event.type === "event" && SUBSYSTEM_STATE_CODES.has(event.code)),
  Retrieval: (event) => (event.type === "state" || event.type === "event") && event.code.startsWith("retrieval_"),
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

function isObjectRecord(value: unknown): value is Record<string, unknown> {
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
    if (!parsed) {
      continue;
    }
    const nestedRow: NestedStateRow = {
      kind: "nested",
      key: `substate:${event.seq}`,
      event,
      durationMs: null,
      phase: parsed.phase,
      stateCode: parsed.stateCode || "UNKNOWN",
      details: parsed.details,
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
      phase: parsed.phase,
      stateCode: parsed.stateCode || "UNKNOWN",
      details: parsed.details,
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
  if (event.type === "state") {
    return event.code;
  }
  if (event.type === "error" || event.type === "cancelled") {
    return event.type;
  }
  if (event.type === "done") {
    return "done";
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
  if (event.payload) {
    return JSON.stringify(event.payload).slice(0, 220);
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
  if (status === "cancel_requested" || status === "queued") {
    return "warn";
  }
  if (status === "running") {
    return "ok";
  }
  return "neutral";
}
