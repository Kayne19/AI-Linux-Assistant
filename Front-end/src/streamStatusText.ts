import type { StreamStatusEvent } from "./types";

type StatusEntry = {
  label: string;
  aliases: string[];
};

const DEFAULT_STATUS_ENTRY: StatusEntry = {
  label: "Thinking",
  aliases: [
    "Thinking",
  ],
};

const STREAM_STATUS_TEXT: Record<string, StatusEntry> = {
  START: {
    label: "Thinking",
    aliases: [
      "Thinking",
      "Getting started",
    ],
  },
  LOAD_MEMORY: {
    label: "Recalling context",
    aliases: [
      "Trying to recall",
      "Checking memory",
    ],
  },
  SUMMARIZE_CONVERSATION_HISTORY: {
    label: "Reviewing recent conversation",
    aliases: [
      "Reviewing the chat",
      "Looking back",
    ],
  },
  CLASSIFY: {
    label: "Thinking",
    aliases: [
      "Thinking",
      "Sorting it out",
    ],
  },
  REWRITE_QUERY: {
    label: "Refining the question",
    aliases: [
      "Rephrasing the question",
      "Tightening the search",
    ],
  },
  RETRIEVE_CONTEXT: {
    label: "Reading manuals",
    aliases: [
      "Reading manuals",
      "Looking through docs",
      "Dusting off the textbook",
    ],
  },
  GENERATE_RESPONSE: {
    label: "Writing response",
    aliases: [
      "Formatting ideas",
      "Putting it together",
    ],
  },
  SUMMARIZE_RETRIEVED_DOCS: {
    label: "Condensing the evidence",
    aliases: [
      "Condensing the evidence",
      "Remembering the important parts",
    ],
  },
  EXTRACT_MEMORY: {
    label: "Saving memory",
    aliases: [
      "Keeping the useful bits",
    ],
  },
  RESOLVE_MEMORY: {
    label: "Reconciling memory",
    aliases: [
      "Making sure I head that right",
      "Checking for conflicts",
    ],
  },
  COMMIT_MEMORY: {
    label: "Committing memory",
    aliases: [
      "Committing memory",
      "Saving it for later",
    ],
  },
  retrieval_search_started: {
    label: "Searching documentation",
    aliases: [
      "Searching docs",
      "Starting the search",
    ],
  },
  retrieval_candidates_found: {
    label: "Searching documentation",
    aliases: [
      "Searching docs",
      "Checking matches",
    ],
  },
  retrieval_sources_filtered: {
    label: "Narrowing to relevant manuals",
    aliases: [
      "Filtering sources",
      "Narrowing it down",
    ],
  },
  retrieval_reranking: {
    label: "Comparing relevant sources",
    aliases: [
      "Comparing sources",
      "Choosing the best answers",
    ],
  },
  retrieval_source_boosting: {
    label: "Weighing the evidence",
    aliases: [
      "Weighting the evidence",
      "Boosting strong sources",
    ],
  },
  retrieval_expanding: {
    label: "Expanding search",
    aliases: [
      "Expanding search",
      "Looking wider",
      "Consulting more sources"
    ],
  },
  retrieval_complete: {
    label: "Reading manuals",
    aliases: [
      "Reading manuals",
      "Using the docs",
    ],
  },
  web_search_used: {
    label: "Searching the web",
    aliases: [
      "Consulting the internet",
      "Maybe Google knows",
      "Consulting the marketplace of ideas",
    ],
  },
  "tool:search_rag_database": {
    label: "Reading manuals",
    aliases: [
      "Reading manuals",
      "Searching project docs",
    ],
  },
  "tool:search_RAG_database": {
    label: "Reading manuals",
    aliases: [
      "Reading manuals",
      "Searching project docs",
    ],
  },
  "tool:search_conversation_history": {
    label: "Reviewing conversation history",
    aliases: [
      "Reviewing the chat",
      "Looking back",
    ],
  },
  "tool:search_memory_issues": {
    label: "Recalling context",
    aliases: [
      "Trying to recall",
      "Checking memory",
    ],
  },
  "tool:search_attempt_log": {
    label: "Recalling context",
    aliases: [
      "Trying to recall",
      "Checking past attempts",
    ],
  },
  "tool:default": {
    label: "Using tools",
    aliases: [
      "Using tools",
      "Working in the background",
    ],
  },
  "responder:WEB_SEARCH": {
    label: "Consulting the marketplace of ideas",
    aliases: [
      "Consulting the internet",
      "Checking the web",
    ],
  },
  "responder:PROCESS_TOOL_CALLS": {
    label: "Using tools",
    aliases: [
      "Using tools",
      "Running tool calls",
    ],
  },
  "responder:SUBMIT_TOOL_RESULTS": {
    label: "Reviewing tool results",
    aliases: [
      "Reviewing tool results",
      "Checking tool output",
    ],
  },
  "responder:REQUEST_MODEL": {
    label: "Thinking",
    aliases: [
      "Thinking",
      "Taking another pass",
    ],
  },
  "responder:default": {
    label: "Writing response",
    aliases: [
      "Writing response",
      "Still writing",
    ],
  },
  "magi:OPENING_ARGUMENTS": {
    label: "Deliberating",
    aliases: [
      "Starting deliberation",
      "Gathering perspectives",
    ],
  },
  "magi:ROLE_EAGER": {
    label: "Eager is proposing",
    aliases: [
      "Generating hypothesis",
      "Taking a first pass",
    ],
  },
  "magi:ROLE_SKEPTIC": {
    label: "Skeptic is challenging",
    aliases: [
      "Playing devil's advocate",
      "Looking for holes",
    ],
  },
  "magi:ROLE_HISTORIAN": {
    label: "Historian is verifying",
    aliases: [
      "Checking project history",
      "Consulting past experience",
    ],
  },
  "magi:DISCUSSION_GATE": {
    label: "Checking whether debate is needed",
    aliases: [
      "Checking whether debate is needed",
      "Deciding whether to push deeper",
    ],
  },
  "magi:DISCUSSION": {
    label: "Agents are discussing",
    aliases: [
      "Refining the diagnosis",
      "Comparing perspectives",
    ],
  },
  "magi:DISCUSSION_EAGER": {
    label: "Eager is responding",
    aliases: [
      "Eager has more to say",
    ],
  },
  "magi:DISCUSSION_SKEPTIC": {
    label: "Skeptic is responding",
    aliases: [
      "Skeptic has more to say",
    ],
  },
  "magi:DISCUSSION_HISTORIAN": {
    label: "Historian is responding",
    aliases: [
      "Historian has more to say",
    ],
  },
  "magi:CLOSING_ARGUMENTS": {
    label: "Closing arguments",
    aliases: [
      "Committing to conclusions",
      "Final positions forming",
    ],
  },
  "magi:CLOSING_EAGER": {
    label: "Eager is closing",
    aliases: [
      "Eager commits",
      "Eager's final take",
    ],
  },
  "magi:CLOSING_SKEPTIC": {
    label: "Skeptic is closing",
    aliases: [
      "Skeptic commits",
      "Skeptic's final take",
    ],
  },
  "magi:CLOSING_HISTORIAN": {
    label: "Historian is closing",
    aliases: [
      "Historian commits",
      "Historian's final take",
    ],
  },
  "magi:ARBITER": {
    label: "Arbiter is synthesizing",
    aliases: [
      "Reaching a verdict",
      "Writing final answer",
    ],
  },
  "magi:default": {
    label: "Deliberating",
    aliases: [
      "Deliberating",
      "Magi system active",
    ],
  },
};

export function getStreamStatusKey(streamStatus: StreamStatusEvent | null) {
  if (!streamStatus) {
    return "START";
  }

  if (streamStatus.source === "state") {
    return streamStatus.code || "START";
  }

  if (streamStatus.code === "tool_start") {
    const name = String(streamStatus.payload?.name || "");
    return name ? `tool:${name}` : "tool:default";
  }

  if (streamStatus.code === "responder_state") {
    const responderState = String(streamStatus.payload?.state || "");
    return responderState ? `responder:${responderState}` : "responder:default";
  }

  if (streamStatus.code === "magi_state") {
    const magiState = String(streamStatus.payload?.state || "");
    return magiState ? `magi:${magiState}` : "magi:default";
  }

  return streamStatus.code || "START";
}

function getStatusEntry(streamStatus: StreamStatusEvent | null) {
  const key = getStreamStatusKey(streamStatus);
  return STREAM_STATUS_TEXT[key] || DEFAULT_STATUS_ENTRY;
}

export function getStreamStatusLabel(streamStatus: StreamStatusEvent | null) {
  return getStatusEntry(streamStatus).label;
}

export function getStreamStatusAliases(streamStatus: StreamStatusEvent | null) {
  return getStatusEntry(streamStatus).aliases;
}
