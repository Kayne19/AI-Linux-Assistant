import type { ChatRun, RunEvent } from "../types";

export const ACTIVE_RUN_STATUSES = new Set(["queued", "running", "cancel_requested"]);
export const TERMINAL_RUN_STATUSES = new Set(["completed", "failed", "cancelled"]);
export const STREAMING_CODES = new Set(["text_delta", "text_checkpoint", "magi_role_text_delta"]);
export const DEBUG_TABS = ["Timeline", "States", "Retrieval", "Memory", "Streaming", "Raw"] as const;

export type DebugTab = (typeof DEBUG_TABS)[number];

export const TAB_FILTERS: Record<DebugTab, (event: RunEvent) => boolean> = {
  Timeline: () => true,
  States: (event) => event.type === "state",
  Retrieval: (event) => typeof (event as { code?: unknown }).code === "string" && (event as { code: string }).code.startsWith("retrieval_"),
  Memory: (event) => typeof (event as { code?: unknown }).code === "string" && (event as { code: string }).code.includes("memory"),
  Streaming: (event) => event.type === "event" && STREAMING_CODES.has(event.code),
  Raw: () => true,
};

export function isActiveRunStatus(status: string | null | undefined) {
  return ACTIVE_RUN_STATUSES.has(String(status || ""));
}

export function isTerminalRunStatus(status: string | null | undefined) {
  return TERMINAL_RUN_STATUSES.has(String(status || ""));
}

export function isTerminalRunEvent(event: RunEvent) {
  return event.type === "done" || event.type === "error" || event.type === "cancelled";
}

export function parseTimestamp(value: string | null | undefined) {
  if (!value) {
    return null;
  }
  const parsed = Date.parse(value);
  return Number.isNaN(parsed) ? null : parsed;
}

export function formatTimestamp(value: string | null | undefined) {
  const parsed = parseTimestamp(value);
  if (parsed === null) {
    return "Unknown";
  }
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    second: "2-digit",
  }).format(new Date(parsed));
}

export function formatCompactTimestamp(value: string | null | undefined) {
  const parsed = parseTimestamp(value);
  if (parsed === null) {
    return "Unknown";
  }
  return new Intl.DateTimeFormat(undefined, {
    hour: "numeric",
    minute: "2-digit",
    second: "2-digit",
  }).format(new Date(parsed));
}

export function formatDuration(value: number | null | undefined) {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return "—";
  }
  if (value < 1000) {
    return `${Math.round(value)}ms`;
  }
  if (value < 60_000) {
    return `${(value / 1000).toFixed(value >= 10_000 ? 0 : 1)}s`;
  }
  const minutes = Math.floor(value / 60_000);
  const seconds = Math.round((value % 60_000) / 1000);
  return `${minutes}m ${seconds}s`;
}

export function getLatencyTone(value: number | null | undefined) {
  if (value === null || value === undefined) {
    return "neutral";
  }
  if (value < 1000) {
    return "good";
  }
  if (value <= 3000) {
    return "warn";
  }
  return "bad";
}

export function truncateId(value: string | null | undefined, size: number = 8) {
  const text = String(value || "");
  if (!text || text.length <= size * 2) {
    return text || "—";
  }
  return `${text.slice(0, size)}…${text.slice(-size)}`;
}

export async function copyText(value: string) {
  if (!value || !navigator.clipboard) {
    return false;
  }
  try {
    await navigator.clipboard.writeText(value);
    return true;
  } catch {
    return false;
  }
}

export function getHighestSeq(events: RunEvent[]) {
  return events.reduce((highest, event) => Math.max(highest, Number(event.seq || 0)), 0);
}

export function mergeRunEvents(current: RunEvent[], incoming: RunEvent[]) {
  const bySeq = new Map<number, RunEvent>();
  for (const event of current) {
    bySeq.set(event.seq, event);
  }
  for (const event of incoming) {
    bySeq.set(event.seq, event);
  }
  return Array.from(bySeq.values()).sort((left, right) => left.seq - right.seq);
}

export function computeTimings(run: ChatRun, events: RunEvent[], nowMs: number = Date.now()) {
  const startedMs = parseTimestamp(run.started_at);
  const createdMs = parseTimestamp(run.created_at);
  const finishedMs = parseTimestamp(run.finished_at);

  const queueWait = startedMs !== null && createdMs !== null ? startedMs - createdMs : null;

  const eventTimes = events
    .map((event) => parseTimestamp(event.created_at))
    .filter((value): value is number => value !== null);
  const firstEventMs = eventTimes.length > 0 ? Math.min(...eventTimes) : null;
  const firstEventLatency = startedMs !== null && firstEventMs !== null ? firstEventMs - startedMs : null;

  const firstTextDeltaTimes = events
    .filter((event) => event.type === "event" && event.code === "text_delta")
    .map((event) => parseTimestamp(event.created_at))
    .filter((value): value is number => value !== null);
  const firstTextDeltaMs = firstTextDeltaTimes.length > 0 ? Math.min(...firstTextDeltaTimes) : null;
  const firstTextDeltaLatency = startedMs !== null && firstTextDeltaMs !== null ? firstTextDeltaMs - startedMs : null;

  const totalDuration = startedMs !== null ? (finishedMs ?? nowMs) - startedMs : null;

  const stateEvents = events.filter((event): event is Extract<RunEvent, { type: "state" }> => event.type === "state");
  const latestStateEvent = stateEvents.reduce<Extract<RunEvent, { type: "state" }> | null>(
    (latest, event) => (latest === null || event.seq > latest.seq ? event : latest),
    null,
  );
  const latestStateMs = latestStateEvent ? parseTimestamp(latestStateEvent.created_at) : null;
  const timeInCurrentState =
    isActiveRunStatus(run.status) && latestStateMs !== null ? nowMs - latestStateMs : null;

  return {
    queueWait,
    firstEventLatency,
    firstTextDeltaLatency,
    totalDuration,
    timeInCurrentState,
  };
}

export function getStateDurations(events: RunEvent[]) {
  const stateEvents = events.filter((event): event is Extract<RunEvent, { type: "state" }> => event.type === "state");
  const durations = new Map<number, number | null>();
  for (let index = 0; index < stateEvents.length; index += 1) {
    const current = stateEvents[index];
    const next = stateEvents[index + 1];
    const currentMs = parseTimestamp(current.created_at);
    const nextMs = next ? parseTimestamp(next.created_at) : null;
    durations.set(
      current.seq,
      currentMs !== null && nextMs !== null ? nextMs - currentMs : null,
    );
  }
  return durations;
}

export function getEventTitle(event: RunEvent) {
  if (event.type === "state") {
    return event.code;
  }
  if (event.type === "event") {
    return event.code;
  }
  if (event.type === "done") {
    return "done";
  }
  return event.type;
}

export function getEventSummary(event: RunEvent) {
  if (event.type === "state") {
    return "Router state transition";
  }
  if (event.type === "event") {
    if (event.code === "text_delta") {
      return String(event.payload?.delta || "").slice(0, 120) || "Streaming token delta";
    }
    if (event.code === "text_checkpoint") {
      const text = String(event.payload?.text || "");
      return text ? `${text.length} chars checkpointed` : "Checkpoint saved";
    }
    const payload = event.payload || {};
    const keys = Object.keys(payload);
    if (keys.length === 0) {
      return "Event emitted";
    }
    return keys
      .slice(0, 3)
      .map((key) => `${key}: ${renderPrimitive(payload[key])}`)
      .join(" · ");
  }
  if (event.type === "done") {
    return `${event.assistant_message.content.length} chars persisted`;
  }
  return event.message || "Terminal event";
}

export function renderPrimitive(value: unknown) {
  if (value === null || value === undefined || value === "") {
    return "—";
  }
  if (typeof value === "string") {
    return value.length > 64 ? `${value.slice(0, 64)}…` : value;
  }
  if (typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  if (Array.isArray(value)) {
    return `${value.length} items`;
  }
  if (typeof value === "object") {
    return `${Object.keys(value as Record<string, unknown>).length} fields`;
  }
  return String(value);
}

export function getTabEvents(tab: DebugTab, events: RunEvent[]) {
  return events.filter(TAB_FILTERS[tab]);
}

export function getLeaseRemainingMs(run: ChatRun, nowMs: number = Date.now()) {
  const leaseExpiresMs = parseTimestamp(run.lease_expires_at);
  if (!isActiveRunStatus(run.status) || leaseExpiresMs === null) {
    return null;
  }
  return leaseExpiresMs - nowMs;
}

export function sortRunsNewestFirst(runs: ChatRun[]) {
  return [...runs].sort((left, right) => {
    const leftMs = parseTimestamp(left.created_at) ?? 0;
    const rightMs = parseTimestamp(right.created_at) ?? 0;
    if (leftMs !== rightMs) {
      return rightMs - leftMs;
    }
    if (left.id === right.id) {
      return 0;
    }
    return left.id < right.id ? 1 : -1;
  });
}

export function dedupeRuns(runs: ChatRun[]) {
  const byId = new Map<string, ChatRun>();
  for (const run of runs) {
    byId.set(run.id, run);
  }
  return sortRunsNewestFirst(Array.from(byId.values()));
}
