import { useEffect, useRef, useState } from "react";
import {
  getCouncilCompletionCatchupDelta,
  hasPendingCouncilWorkForChat,
  shouldDeferCouncilCompletion,
  takeReadyCouncilCompletion,
} from "../councilStreamLifecycle";
import type { ChatMessage, PendingCouncilDeltaBatch, UICouncilEntry } from "../types";
import {
  TEXT_DELTA_CHARS_PER_MS,
  TEXT_DELTA_MAX_CHARS_PER_FRAME,
  TEXT_DELTA_MIN_CHARS_PER_FRAME,
} from "../utils";

type UseCouncilStreamingOptions = {
  updateRunUiCouncilEntries: (
    chatId: string,
    updater: (entries: UICouncilEntry[]) => UICouncilEntry[],
  ) => void;
  onDrainComplete?: (chatId: string) => void;
};

export function getCouncilEntryId(role: string, phase: string, round?: number) {
  return `${phase}-${role}-${round ?? 0}`;
}

function getInterventionEntryId(seq?: number | null, inputKind?: string, text?: string) {
  if (typeof seq === "number" && Number.isFinite(seq)) {
    return `intervention-${seq}`;
  }
  const normalizedKind = String(inputKind || "input").trim().replace(/\s+/g, "-");
  const normalizedText = String(text || "").trim().slice(0, 24).replace(/\s+/g, "-");
  return `intervention-${normalizedKind}${normalizedText ? `-${normalizedText}` : ""}`;
}

export function useCouncilStreaming({ updateRunUiCouncilEntries, onDrainComplete }: UseCouncilStreamingOptions) {
  const pendingCouncilDeltaBatchesRef = useRef<Record<string, PendingCouncilDeltaBatch>>({});
  const pendingCouncilCompletionsRef = useRef<Record<string, Record<string, unknown>>>({});
  const liveCouncilEntriesRef = useRef<Record<string, boolean>>({});
  const visibleCouncilTextRef = useRef<Record<string, string>>({});
  const updateRunUiCouncilEntriesRef = useRef(updateRunUiCouncilEntries);
  const onDrainCompleteRef = useRef(onDrainComplete);

  const [councilMode, setCouncilMode] = useState<"off" | "full" | "lite">("off");
  const [councilActive, setCouncilActive] = useState(false);
  const [councilPanelCollapsed, setCouncilPanelCollapsed] = useState(false);
  const [councilEntries, setCouncilEntries] = useState<UICouncilEntry[]>([]);
  const [viewingCouncilMessageId, setViewingCouncilMessageId] = useState<number | null>(null);
  const [councilInterventionInput, setCouncilInterventionInput] = useState("");

  const councilFeedRef = useRef<HTMLDivElement | null>(null);
  const councilEndRef = useRef<HTMLDivElement | null>(null);
  const councilStickToBottomRef = useRef(true);

  useEffect(() => {
    updateRunUiCouncilEntriesRef.current = updateRunUiCouncilEntries;
  }, [updateRunUiCouncilEntries]);

  useEffect(() => {
    onDrainCompleteRef.current = onDrainComplete;
  }, [onDrainComplete]);

  useEffect(() => {
    if (!councilStickToBottomRef.current) return;
    councilEndRef.current?.scrollIntoView({
      behavior: councilEntries.some((entry) => !entry.complete) ? "auto" : "smooth",
      block: "end",
    });
  }, [councilEntries]);

  function cycleCouncilMode() {
    setCouncilMode((current) => (current === "off" ? "full" : current === "full" ? "lite" : "off"));
  }

  function councilDeltaBatchKey(chatId: string, entryId: string) {
    return `${chatId}:${entryId}`;
  }

  function clearPendingCouncilDeltaBatch(chatId: string, entryId: string) {
    const batchKey = councilDeltaBatchKey(chatId, entryId);
    const pending = pendingCouncilDeltaBatchesRef.current[batchKey];
    if (!pending) {
      return;
    }

    if (pending.frameId !== null) {
      window.cancelAnimationFrame(pending.frameId);
    }

    delete pendingCouncilDeltaBatchesRef.current[batchKey];
  }

  function applyMagiRoleComplete(chatId: string, payload: Record<string, unknown>) {
    const role = String(payload.role || "");
    const phase = String(payload.phase || "");
    const round = typeof payload.round === "number" ? payload.round : undefined;
    const text = String(payload.text || "");
    const entryId = getCouncilEntryId(role, phase, round);
    const entryKey = liveCouncilEntryKey(chatId, entryId);

    clearPendingCouncilDeltaBatch(chatId, entryId);
    setEntryStreamingActive(chatId, entryId, false);
    updateRunUiCouncilEntriesRef.current(chatId, (entries) => {
      const existingEntry = entries.find((entry) => entry.entryId === entryId);
      const streamedText = String(existingEntry?.streamBuffer || "");
      const finalText = text.length >= streamedText.length ? text : streamedText;
      visibleCouncilTextRef.current[entryKey] = finalText;
      if (!existingEntry) {
        return [
          ...entries,
          {
            entryId,
            role,
            phase,
            round,
            text: finalText,
            complete: true,
            entryKind: "role",
          },
        ];
      }
      return entries.map((entry) =>
        entry.entryId === entryId
          ? {
              ...entry,
              text: finalText,
              complete: true,
              streamBuffer: undefined,
              streamPreview: undefined,
            }
          : entry,
      );
    });
  }

  function applyMagiInterventionAdded(chatId: string, payload: Record<string, unknown>, seq?: number) {
    const inputText = String(payload.input_text || payload.text || payload.delta || payload.content || "");
    if (!inputText) {
      return;
    }
    const inputKind = String(payload.input_kind || payload.kind || payload.entry_kind || "fact");
    const entryId = getInterventionEntryId(seq, inputKind, inputText);
    const entryKey = liveCouncilEntryKey(chatId, entryId);

    visibleCouncilTextRef.current[entryKey] = inputText;
    updateRunUiCouncilEntriesRef.current(chatId, (entries) => {
      const existingEntry = entries.find((entry) => entry.entryId === entryId);
      const nextEntry: UICouncilEntry = {
        entryId,
        role: "user",
        phase: "intervention",
        text: inputText,
        complete: true,
        entryKind: "user_intervention",
        inputKind,
      };
      if (!existingEntry) {
        return [...entries, nextEntry];
      }
      return entries.map((entry) => (entry.entryId === entryId ? { ...entry, ...nextEntry } : entry));
    });
  }

  function liveCouncilEntryKey(chatId: string, entryId: string) {
    return `${chatId}:${entryId}`;
  }

  function setEntryStreamingActive(chatId: string, entryId: string, active: boolean) {
    const key = liveCouncilEntryKey(chatId, entryId);
    if (active) {
      liveCouncilEntriesRef.current[key] = true;
      return;
    }
    delete liveCouncilEntriesRef.current[key];
  }

  function hasEntryStreamingActive(chatId: string, entryId: string) {
    return Boolean(liveCouncilEntriesRef.current[liveCouncilEntryKey(chatId, entryId)]);
  }

  function clearPendingCouncilDeltaBatchesForChat(chatId: string) {
    Object.entries(pendingCouncilDeltaBatchesRef.current).forEach(([batchKey, pending]) => {
      if (!batchKey.startsWith(`${chatId}:`)) {
        return;
      }

      if (pending.frameId !== null) {
        window.cancelAnimationFrame(pending.frameId);
      }

      delete pendingCouncilDeltaBatchesRef.current[batchKey];
    });
    Object.keys(pendingCouncilCompletionsRef.current).forEach((entryKey) => {
      if (entryKey.startsWith(`${chatId}:`)) {
        delete pendingCouncilCompletionsRef.current[entryKey];
      }
    });
    Object.keys(liveCouncilEntriesRef.current).forEach((entryKey) => {
      if (entryKey.startsWith(`${chatId}:`)) {
        delete liveCouncilEntriesRef.current[entryKey];
      }
    });
    Object.keys(visibleCouncilTextRef.current).forEach((entryKey) => {
      if (entryKey.startsWith(`${chatId}:`)) {
        delete visibleCouncilTextRef.current[entryKey];
      }
    });
  }

  function notifyDrainCompleteIfIdle(chatId: string) {
    if (
      !hasPendingCouncilWorkForChat(
        chatId,
        pendingCouncilDeltaBatchesRef.current,
        pendingCouncilCompletionsRef.current,
      )
    ) {
      onDrainCompleteRef.current?.(chatId);
    }
  }

  function queueCouncilTextDelta(chatId: string, entryId: string, delta: string) {
    if (!delta) {
      return;
    }

    const batchKey = councilDeltaBatchKey(chatId, entryId);
    const batches = pendingCouncilDeltaBatchesRef.current;
    const batch = batches[batchKey] || { delta: "", frameId: null, lastDrainAt: null };
    batch.delta += delta;
    batches[batchKey] = batch;

    scheduleCouncilDeltaDrain(chatId, entryId);
  }

  function scheduleCouncilDeltaDrain(chatId: string, entryId: string) {
    const batchKey = councilDeltaBatchKey(chatId, entryId);
    const batch = pendingCouncilDeltaBatchesRef.current[batchKey];
    if (!batch || batch.frameId !== null) {
      return;
    }

    batch.frameId = window.requestAnimationFrame((timestamp) => {
      const currentBatch = pendingCouncilDeltaBatchesRef.current[batchKey];
      if (!currentBatch) {
        return;
      }

      currentBatch.frameId = null;
      if (!currentBatch.delta) {
        delete pendingCouncilDeltaBatchesRef.current[batchKey];
        notifyDrainCompleteIfIdle(chatId);
        return;
      }

      const elapsedMs =
        currentBatch.lastDrainAt === null ? 16 : Math.max(16, timestamp - currentBatch.lastDrainAt);
      currentBatch.lastDrainAt = timestamp;

      const pacedChars = Math.max(
        TEXT_DELTA_MIN_CHARS_PER_FRAME,
        Math.min(TEXT_DELTA_MAX_CHARS_PER_FRAME, Math.ceil(elapsedMs * TEXT_DELTA_CHARS_PER_MS)),
      );
      const drainChars = Math.min(currentBatch.delta.length, pacedChars);
      const bufferedDelta = currentBatch.delta.slice(0, drainChars);
      currentBatch.delta = currentBatch.delta.slice(drainChars);
      const entryKey = liveCouncilEntryKey(chatId, entryId);
      const nextVisibleText = `${visibleCouncilTextRef.current[entryKey] || ""}${bufferedDelta}`;
      visibleCouncilTextRef.current[entryKey] = nextVisibleText;

      updateRunUiCouncilEntriesRef.current(chatId, (entries) =>
        entries.map((entry) =>
          entry.entryId === entryId
            ? (() => {
                return {
                  ...entry,
                  streamBuffer: nextVisibleText,
                  streamPreview: nextVisibleText,
                };
              })()
            : entry,
        ),
      );

      if (currentBatch.delta) {
        scheduleCouncilDeltaDrain(chatId, entryId);
        return;
      }

      delete pendingCouncilDeltaBatchesRef.current[batchKey];
      const pendingCompletion = takeReadyCouncilCompletion(
        pendingCouncilCompletionsRef.current[batchKey],
        pendingCouncilDeltaBatchesRef.current[batchKey],
      );
      if (pendingCompletion) {
        delete pendingCouncilCompletionsRef.current[batchKey];
        applyMagiRoleComplete(chatId, pendingCompletion);
      }
      notifyDrainCompleteIfIdle(chatId);
    });
  }

  function handleMagiRoleStart(chatId: string, payload: Record<string, unknown>) {
    const role = String(payload.role || "");
    const phase = String(payload.phase || "");
    const round = typeof payload.round === "number" ? payload.round : undefined;
    const entryId = getCouncilEntryId(role, phase, round);

    clearPendingCouncilDeltaBatch(chatId, entryId);
    setEntryStreamingActive(chatId, entryId, false);
    visibleCouncilTextRef.current[liveCouncilEntryKey(chatId, entryId)] = "";
    updateRunUiCouncilEntriesRef.current(chatId, (entries) => [
      ...entries.filter((entry) => entry.entryId !== entryId),
      {
        entryId,
        role,
        phase,
        round,
        text: "",
        complete: false,
        streamBuffer: "",
        streamPreview: "",
        entryKind: "role",
      },
    ]);
  }

  function handleMagiRoleTextDelta(chatId: string, payload: Record<string, unknown>) {
    const role = String(payload.role || "");
    const phase = String(payload.phase || "");
    const round = typeof payload.round === "number" ? payload.round : undefined;
    const delta = String(payload.delta || "");
    const entryId = getCouncilEntryId(role, phase, round);
    if (!delta) {
      return;
    }

    setEntryStreamingActive(chatId, entryId, true);
    queueCouncilTextDelta(chatId, entryId, delta);
  }

  function handleMagiRoleTextCheckpoint(chatId: string, payload: Record<string, unknown>) {
    const role = String(payload.role || "");
    const phase = String(payload.phase || "");
    const round = typeof payload.round === "number" ? payload.round : undefined;
    const text = String(payload.text || "");
    const entryId = getCouncilEntryId(role, phase, round);

    if (!text || hasEntryStreamingActive(chatId, entryId)) {
      return;
    }
    clearPendingCouncilDeltaBatch(chatId, entryId);
    visibleCouncilTextRef.current[liveCouncilEntryKey(chatId, entryId)] = text;
    updateRunUiCouncilEntriesRef.current(chatId, (entries) => {
      const existingEntry = entries.find((entry) => entry.entryId === entryId);
      if (!existingEntry) {
        return [
          ...entries,
          {
            entryId,
            role,
            phase,
            round,
            text: "",
            complete: false,
            streamBuffer: text,
            streamPreview: text,
            entryKind: "role",
          },
        ];
      }
      return entries.map((entry) =>
        entry.entryId === entryId
          ? {
              ...entry,
              role,
              phase,
              round,
              text: entry.complete ? entry.text : "",
              complete: entry.complete,
              streamBuffer: entry.complete ? undefined : text,
              streamPreview: entry.complete ? undefined : text,
            }
          : entry,
      );
    });
  }

  function handleMagiRoleComplete(chatId: string, payload: Record<string, unknown>) {
    const role = String(payload.role || "");
    const phase = String(payload.phase || "");
    const round = typeof payload.round === "number" ? payload.round : undefined;
    const entryId = getCouncilEntryId(role, phase, round);
    const batchKey = councilDeltaBatchKey(chatId, entryId);
    const pendingBatch = pendingCouncilDeltaBatchesRef.current[batchKey];

    if (shouldDeferCouncilCompletion(pendingBatch)) {
      pendingCouncilCompletionsRef.current[batchKey] = payload;
      return;
    }

    const visibleText = visibleCouncilTextRef.current[liveCouncilEntryKey(chatId, entryId)] || "";
    const catchupDelta = getCouncilCompletionCatchupDelta(visibleText, String(payload.text || ""));
    if (catchupDelta) {
      pendingCouncilCompletionsRef.current[batchKey] = payload;
      setEntryStreamingActive(chatId, entryId, true);
      queueCouncilTextDelta(chatId, entryId, catchupDelta);
      return;
    }

    applyMagiRoleComplete(chatId, payload);
    notifyDrainCompleteIfIdle(chatId);
  }

  function handleViewCouncilEntries(message: ChatMessage) {
    const stored = message.council_entries;
    if (!stored?.length) {
      return;
    }

    const nextEntries: UICouncilEntry[] = stored.map((entry) => ({
      entryId: getCouncilEntryId(entry.role, entry.phase, entry.round ?? undefined),
      role: entry.role,
      phase: entry.phase,
      round: entry.round ?? undefined,
      text: entry.text,
      complete: true,
      entryKind: entry.entry_kind || "role",
      inputKind: entry.input_kind || undefined,
    }));

    setCouncilEntries(nextEntries);
    setCouncilActive(true);
    setCouncilPanelCollapsed(false);
    setViewingCouncilMessageId(message.id);
  }

  function syncLiveCouncilEntries(entries: UICouncilEntry[], selectedChatBusy: boolean) {
    if (viewingCouncilMessageId !== null) {
      return;
    }
    setCouncilActive(entries.length > 0);
  }

  function clearForChatSelection() {
    setCouncilActive(false);
    setCouncilEntries([]);
    setViewingCouncilMessageId(null);
    setCouncilInterventionInput("");
  }

  function updateCouncilStickToBottom() {
    const el = councilFeedRef.current;
    if (!el) return;
    const dist = el.scrollHeight - el.scrollTop - el.clientHeight;
    councilStickToBottomRef.current = dist <= 80;
  }

  function resetCouncilStickToBottom() {
    councilStickToBottomRef.current = true;
  }

  useEffect(
    () => () => {
      Object.values(pendingCouncilDeltaBatchesRef.current).forEach((batch) => {
        if (batch.frameId !== null) {
          window.cancelAnimationFrame(batch.frameId);
        }
      });
      pendingCouncilDeltaBatchesRef.current = {};
      pendingCouncilCompletionsRef.current = {};
      visibleCouncilTextRef.current = {};
    },
    [],
  );

  return {
    councilMode,
    setCouncilMode,
    cycleCouncilMode,
    councilActive,
    setCouncilActive,
    councilPanelCollapsed,
    setCouncilPanelCollapsed,
    councilEntries,
    setCouncilEntries,
    councilInterventionInput,
    setCouncilInterventionInput,
    viewingCouncilMessageId,
    setViewingCouncilMessageId,
    councilFeedRef,
    councilEndRef,
    councilStickToBottomRef,
    updateCouncilStickToBottom,
    resetCouncilStickToBottom,
    clearPendingCouncilDeltaBatchesForChat,
    handleMagiRoleStart,
    handleMagiRoleTextDelta,
    handleMagiRoleTextCheckpoint,
    handleMagiRoleComplete,
    handleMagiInterventionAdded: (chatId: string, payload: Record<string, unknown>, seq?: number) =>
      applyMagiInterventionAdded(chatId, payload, seq),
    handleViewCouncilEntries,
    syncLiveCouncilEntries,
    clearForChatSelection,
    hasPendingCouncilWork: (chatId: string) =>
      hasPendingCouncilWorkForChat(
        chatId,
        pendingCouncilDeltaBatchesRef.current,
        pendingCouncilCompletionsRef.current,
      ),
  };
}
