import type { ChatRun, RunEvent } from "../types";

export const ACTIVE_RUN_STATUSES = new Set(["queued", "running", "cancel_requested"]);
export const STREAMING_CODES = new Set([
  "text_delta",
  "text_checkpoint",
  "magi_role_text_delta",
  "magi_role_text_checkpoint",
]);
export const DEBUG_TABS = ["Timeline", "States", "Retrieval", "Memory", "Streaming", "Raw"] as const;

export type DebugTab = (typeof DEBUG_TABS)[number];

export const TAB_FILTERS: Record<DebugTab, (event: RunEvent) => boolean> = {
  Timeline: () => true,
  States: (event) => event.type === "state",
  Retrieval: (event) => (event.type === "state" || event.type === "event") && event.code.startsWith("retrieval_"),
  Memory: (event) => (event.type === "state" || event.type === "event") && event.code.includes("memory"),
  Streaming: (event) => event.type === "event" && STREAMING_CODES.has(event.code),
  Raw: () => true,
};

type StateRow = {
  event: RunEvent;
  durationMs: number | null;
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
  const stateEvents = events.filter((event) => event.type === "state");
  return stateEvents.map((event, index) => {
    const currentMs = parseTimeMs(event.created_at);
    const nextMs = parseTimeMs(stateEvents[index + 1]?.created_at);
    return {
      event,
      durationMs: currentMs !== null && nextMs !== null ? Math.max(0, nextMs - currentMs) : null,
    };
  });
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
  if (event.type === "state") {
    return event.code;
  }
  if (event.type === "error" || event.type === "cancelled") {
    return event.type;
  }
  if (event.type === "done") {
    return "done";
  }
  return event.code || event.type;
}

export function eventSummary(event: RunEvent): string {
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
  return event.code || event.type;
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
