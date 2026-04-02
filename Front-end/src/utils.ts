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
  if (phase === "intervention") return "User input";
  if (phase === "arbiter") return "Synthesis";
  return phase;
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
  optimisticIdsForRun,
};
