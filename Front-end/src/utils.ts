const TEXT_DELTA_CHARS_PER_MS = 0.035;
const TEXT_DELTA_MIN_CHARS_PER_FRAME = 1;
const TEXT_DELTA_MAX_CHARS_PER_FRAME = 4;

function formatChatTimestamp(value: string) {
  if (!value) {
    return "Unknown";
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
  }).format(date);
}

function formatCouncilPhase(phase: string, round?: number): string {
  if (phase === "opening_arguments") return "Opening Argument";
  if (phase === "discussion") return `Discussion · Round ${round ?? ""}`.trim();
  if (phase === "closing_arguments") return "Closing Argument";
  if (phase === "arbiter") return "Synthesis";
  return phase;
}

function getStreamingDisplayText(buffer: string): string {
  try {
    const parsed = JSON.parse(buffer);
    if (typeof parsed?.position === "string") return parsed.position;
  } catch {
    // incomplete JSON, try regex extraction
  }

  const match = buffer.match(/"position"\s*:\s*"([\s\S]*)/);
  if (!match) return "";
  let inner = match[1];
  const closeIdx = inner.search(/"\s*,\s*"(?:confidence|key_claims)/);
  if (closeIdx > 0) inner = inner.slice(0, closeIdx);

  try {
    return JSON.parse('"' + inner.replace(/"/g, '\\"').replace(/\\\\"/g, '\\"') + '"');
  } catch {
    return inner.replace(/\\n/g, "\n").replace(/\\"/g, '"').replace(/\\\\/g, "\\");
  }
}

function formatMessageTimestamp(value: string) {
  if (!value) {
    return "";
  }

  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "";
  }

  return new Intl.DateTimeFormat(undefined, {
    hour: "numeric",
    minute: "2-digit",
  }).format(date);
}

function optimisticIdsForRun(runId: string) {
  let hash = 0;
  for (let index = 0; index < runId.length; index += 1) {
    hash = (hash * 31 + runId.charCodeAt(index)) >>> 0;
  }
  const base = -(hash || Date.now());
  return {
    userId: base,
    assistantId: base - 1,
  };
}

export {
  TEXT_DELTA_CHARS_PER_MS,
  TEXT_DELTA_MAX_CHARS_PER_FRAME,
  TEXT_DELTA_MIN_CHARS_PER_FRAME,
  formatChatTimestamp,
  formatCouncilPhase,
  formatMessageTimestamp,
  getStreamingDisplayText,
  optimisticIdsForRun,
};
