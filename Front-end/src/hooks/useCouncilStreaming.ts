import { useEffect, useRef, useState } from "react";
import type { ChatMessage, PendingCouncilDeltaBatch, UICouncilEntry } from "../types";
import { getStreamingDisplayText } from "../utils";

type UseCouncilStreamingOptions = {
  updateRunUiCouncilEntries: (
    chatId: string,
    updater: (entries: UICouncilEntry[]) => UICouncilEntry[],
  ) => void;
};

function getCouncilEntryId(role: string, phase: string, round?: number) {
  return `${phase}-${role}-${round ?? 0}`;
}

export function useCouncilStreaming({ updateRunUiCouncilEntries }: UseCouncilStreamingOptions) {
  const pendingCouncilDeltaBatchesRef = useRef<Record<string, PendingCouncilDeltaBatch>>({});
  const updateRunUiCouncilEntriesRef = useRef(updateRunUiCouncilEntries);

  const [councilMode, setCouncilMode] = useState<"off" | "full" | "lite">("off");
  const [councilActive, setCouncilActive] = useState(false);
  const [councilPanelCollapsed, setCouncilPanelCollapsed] = useState(false);
  const [councilEntries, setCouncilEntries] = useState<UICouncilEntry[]>([]);
  const [viewingCouncilMessageId, setViewingCouncilMessageId] = useState<number | null>(null);

  const councilFeedRef = useRef<HTMLDivElement | null>(null);
  const councilEndRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    updateRunUiCouncilEntriesRef.current = updateRunUiCouncilEntries;
  }, [updateRunUiCouncilEntries]);

  useEffect(() => {
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
  }

  function queueCouncilTextDelta(chatId: string, entryId: string, delta: string) {
    if (!delta) {
      return;
    }

    const batchKey = councilDeltaBatchKey(chatId, entryId);
    const batches = pendingCouncilDeltaBatchesRef.current;
    const batch = batches[batchKey] || { delta: "", frameId: null };
    batch.delta += delta;
    batches[batchKey] = batch;

    if (batch.frameId !== null) {
      return;
    }

    batch.frameId = window.requestAnimationFrame(() => {
      const currentBatch = pendingCouncilDeltaBatchesRef.current[batchKey];
      if (!currentBatch) {
        return;
      }

      const bufferedDelta = currentBatch.delta;
      currentBatch.delta = "";
      currentBatch.frameId = null;
      if (!bufferedDelta) {
        return;
      }

      updateRunUiCouncilEntriesRef.current(chatId, (entries) =>
        entries.map((entry) =>
          entry.entryId === entryId
            ? (() => {
                const streamBuffer = (entry.streamBuffer ?? "") + bufferedDelta;
                return {
                  ...entry,
                  streamBuffer,
                  streamPreview: getStreamingDisplayText(streamBuffer),
                };
              })()
            : entry,
        ),
      );
    });
  }

  function handleMagiRoleStart(chatId: string, payload: Record<string, unknown>) {
    const role = String(payload.role || "");
    const phase = String(payload.phase || "");
    const round = typeof payload.round === "number" ? payload.round : undefined;
    const entryId = getCouncilEntryId(role, phase, round);

    clearPendingCouncilDeltaBatch(chatId, entryId);
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
      },
    ]);
  }

  function handleMagiRoleTextDelta(chatId: string, payload: Record<string, unknown>) {
    const role = String(payload.role || "");
    const phase = String(payload.phase || "");
    const round = typeof payload.round === "number" ? payload.round : undefined;
    const delta = String(payload.delta || "");
    const entryId = getCouncilEntryId(role, phase, round);
    queueCouncilTextDelta(chatId, entryId, delta);
  }

  function handleMagiRoleComplete(chatId: string, payload: Record<string, unknown>) {
    const role = String(payload.role || "");
    const phase = String(payload.phase || "");
    const round = typeof payload.round === "number" ? payload.round : undefined;
    const text = String(payload.text || "");
    const entryId = getCouncilEntryId(role, phase, round);

    clearPendingCouncilDeltaBatch(chatId, entryId);
    updateRunUiCouncilEntriesRef.current(chatId, (entries) =>
      entries.map((entry) =>
        entry.entryId === entryId
          ? {
              ...entry,
              text,
              complete: true,
              streamBuffer: undefined,
              streamPreview: undefined,
            }
          : entry,
      ),
    );
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

    if (!entries.length) {
      setCouncilEntries([]);
      setCouncilActive(selectedChatBusy);
      return;
    }

    setCouncilEntries(entries);
    setCouncilActive(entries.length > 0 || selectedChatBusy);
  }

  function clearForChatSelection() {
    setCouncilActive(false);
    setCouncilEntries([]);
  }

  useEffect(
    () => () => {
      Object.values(pendingCouncilDeltaBatchesRef.current).forEach((batch) => {
        if (batch.frameId !== null) {
          window.cancelAnimationFrame(batch.frameId);
        }
      });
      pendingCouncilDeltaBatchesRef.current = {};
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
    viewingCouncilMessageId,
    setViewingCouncilMessageId,
    councilFeedRef,
    councilEndRef,
    clearPendingCouncilDeltaBatchesForChat,
    handleMagiRoleStart,
    handleMagiRoleTextDelta,
    handleMagiRoleComplete,
    handleViewCouncilEntries,
    syncLiveCouncilEntries,
    clearForChatSelection,
  };
}
