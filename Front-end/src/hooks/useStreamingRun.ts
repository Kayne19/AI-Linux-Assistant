import { MutableRefObject, useEffect, useRef, useState } from "react";
import { api } from "../api";
import { streamRunSession } from "../runStreamSession";
import type {
  ChatMessage,
  ChatRun,
  ChatRunUIState,
  ChatSession,
  PendingDonePayload,
  StreamStatusEvent,
  UICouncilEntry,
} from "../types";
import { optimisticIdsForRun } from "../utils";

type TextDeltaController = {
  queueTextDelta: (chatId: string, assistantId: number, delta: string) => void;
  clearPendingTextDeltaBatch: (chatId: string) => void;
  hasPendingDelta: (chatId: string) => boolean;
};

type CouncilController = {
  clearPendingCouncilDeltaBatchesForChat: (chatId: string) => void;
  handleMagiRoleStart: (chatId: string, payload: Record<string, unknown>) => void;
  handleMagiRoleTextDelta: (chatId: string, payload: Record<string, unknown>) => void;
  handleMagiRoleTextCheckpoint: (chatId: string, payload: Record<string, unknown>) => void;
  handleMagiRoleComplete: (chatId: string, payload: Record<string, unknown>) => void;
  hasPendingCouncilWork: (chatId: string) => boolean;
};

type UseStreamingRunOptions = {
  chats: ChatSession[];
  selectedChatId: string;
  selectedChat: ChatSession | null;
  selectedProjectId: string;
  textDelta: TextDeltaController;
  council: CouncilController;
  setMessagesForChat: (chatId: string, updater: (current: ChatMessage[]) => ChatMessage[]) => void;
  reloadChats: (projectId: string) => Promise<ChatSession[]>;
  updateChatRunStatus: (chatId: string, activeRunId: string | null, activeRunStatus: string | null) => void;
  onError: (message: string) => void;
  onTextDrainCompleteRef: MutableRefObject<(chatId: string) => void>;
  onCouncilDrainCompleteRef: MutableRefObject<(chatId: string) => void>;
  runUiCouncilEntriesUpdaterRef: MutableRefObject<
    (chatId: string, updater: (entries: UICouncilEntry[]) => UICouncilEntry[]) => void
  >;
  runUiStreamStatusUpdaterRef: MutableRefObject<(chatId: string, streamStatus: StreamStatusEvent) => void>;
};

type CheckpointSeed = {
  runId: string;
  seq: number;
  text: string;
};

export function useStreamingRun({
  chats,
  selectedChatId,
  selectedChat,
  selectedProjectId,
  textDelta,
  council,
  setMessagesForChat,
  reloadChats,
  updateChatRunStatus,
  onError,
  onTextDrainCompleteRef,
  onCouncilDrainCompleteRef,
  runUiCouncilEntriesUpdaterRef,
  runUiStreamStatusUpdaterRef,
}: UseStreamingRunOptions) {
  const [runUiByChat, setRunUiByChat] = useState<Record<string, ChatRunUIState>>({});

  const streamControllersRef = useRef<Record<string, AbortController>>({});
  const streamingActiveRef = useRef<Record<string, boolean>>({});
  const lastCheckpointRef = useRef<Record<string, CheckpointSeed>>({});
  const pendingDonePayloadsRef = useRef<Record<string, PendingDonePayload>>({});
  const previousSelectedChatIdRef = useRef("");
  const selectedProjectIdRef = useRef(selectedProjectId);
  const reloadChatsRef = useRef(reloadChats);
  const updateChatRunStatusRef = useRef(updateChatRunStatus);
  const onErrorRef = useRef(onError);

  const selectedRunUi = selectedChatId ? (runUiByChat[selectedChatId] || null) : null;
  const streamStatus = selectedRunUi?.streamStatus ?? null;
  const streamingAssistantId = selectedRunUi?.streamingAssistantId ?? null;
  const selectedChatBusy = Boolean(selectedChat?.active_run_id || selectedRunUi);

  useEffect(() => {
    selectedProjectIdRef.current = selectedProjectId;
  }, [selectedProjectId]);

  useEffect(() => {
    reloadChatsRef.current = reloadChats;
  }, [reloadChats]);

  useEffect(() => {
    updateChatRunStatusRef.current = updateChatRunStatus;
  }, [updateChatRunStatus]);

  useEffect(() => {
    onErrorRef.current = onError;
  }, [onError]);

  function setRunUiForChat(
    chatId: string,
    updater: (current: ChatRunUIState | undefined) => ChatRunUIState | undefined,
  ) {
    setRunUiByChat((current) => {
      const nextValue = updater(current[chatId]);
      if (!nextValue) {
        const next = { ...current };
        delete next[chatId];
        return next;
      }

      return {
        ...current,
        [chatId]: nextValue,
      };
    });
  }

  function updateRunUiCouncilEntries(chatId: string, updater: (entries: UICouncilEntry[]) => UICouncilEntry[]) {
    setRunUiForChat(chatId, (current) => (current ? { ...current, councilEntries: updater(current.councilEntries) } : current));
  }

  function updateRunUiStreamStatus(chatId: string, nextStreamStatus: StreamStatusEvent) {
    setRunUiForChat(chatId, (current) => (current ? { ...current, streamStatus: nextStreamStatus } : current));
  }

  async function finalizeDone(chatId: string, payload: PendingDonePayload["payload"], projectIdAtCompletion: string) {
    setMessagesForChat(chatId, (current) => [
      ...current.filter((message) => message.id >= 0),
      payload.user_message,
      payload.assistant_message,
    ]);
    clearRunUi(chatId);
    updateChatRunStatusRef.current(chatId, null, null);
    delete pendingDonePayloadsRef.current[chatId];
    if (projectIdAtCompletion) {
      await reloadChatsRef.current(projectIdAtCompletion);
    }
  }

  function handleTextDrainComplete(chatId: string) {
    const pendingDone = pendingDonePayloadsRef.current[chatId];
    if (pendingDone && !council.hasPendingCouncilWork(chatId)) {
      void finalizeDone(chatId, pendingDone.payload, pendingDone.selectedProjectIdAtCompletion);
    }
  }

  function handleCouncilDrainComplete(chatId: string) {
    const pendingDone = pendingDonePayloadsRef.current[chatId];
    if (pendingDone && !textDelta.hasPendingDelta(chatId) && !council.hasPendingCouncilWork(chatId)) {
      void finalizeDone(chatId, pendingDone.payload, pendingDone.selectedProjectIdAtCompletion);
    }
  }

  function setCheckpointSeed(chatId: string, runId: string, seq: number, text: string) {
    lastCheckpointRef.current[chatId] = {
      runId,
      seq: Math.max(0, seq),
      text,
    };
  }

  function getCheckpointSeed(chatId: string, runId: string) {
    const checkpoint = lastCheckpointRef.current[chatId];
    if (!checkpoint || checkpoint.runId !== runId) {
      return null;
    }

    return checkpoint;
  }

  function applyTextCheckpoint(chatId: string, assistantId: number, text: string) {
    textDelta.clearPendingTextDeltaBatch(chatId);
    setMessagesForChat(chatId, (current) =>
      current.map((message) => (message.id === assistantId ? { ...message, content: text } : message)),
    );
  }

  function ensureOptimisticMessages(chatId: string, run: ChatRun) {
    const checkpointSeed = getCheckpointSeed(chatId, run.id);
    if (!checkpointSeed) {
      delete lastCheckpointRef.current[chatId];
    }

    streamingActiveRef.current[chatId] = false;
    const seedText =
      checkpointSeed && checkpointSeed.seq >= (run.latest_event_seq || 0)
        ? checkpointSeed.text
        : run.partial_assistant_text || checkpointSeed?.text || "";
    const optimisticIds = optimisticIdsForRun(run.id);

    const optimisticUserMessage: ChatMessage = {
      id: optimisticIds.userId,
      session_id: chatId,
      role: "user",
      content: run.request_content,
      created_at: run.created_at || new Date().toISOString(),
    };
    const optimisticAssistantMessage: ChatMessage = {
      id: optimisticIds.assistantId,
      session_id: chatId,
      role: "assistant",
      content: seedText,
      created_at: run.created_at || new Date().toISOString(),
    };

    setMessagesForChat(chatId, (current) => [
      ...current.filter((message) => message.id >= 0),
      optimisticUserMessage,
      optimisticAssistantMessage,
    ]);

    setRunUiForChat(chatId, (current) => ({
      runId: run.id,
      clientRequestId: run.client_request_id,
      pendingContent: run.request_content,
      streamStatus: run.latest_state_code
        ? { source: "state", code: run.latest_state_code }
        : { source: "state", code: "START" },
      streamingAssistantId: optimisticIds.assistantId,
      optimisticUserId: optimisticIds.userId,
      optimisticAssistantId: optimisticIds.assistantId,
      lastSeenSeq: Math.max(current?.lastSeenSeq || 0, checkpointSeed?.seq || 0, run.latest_event_seq || 0),
      councilEntries: current?.councilEntries || [],
    }));
  }

  function clearRunUi(chatId: string) {
    const controller = streamControllersRef.current[chatId];
    if (controller) {
      controller.abort();
      delete streamControllersRef.current[chatId];
    }

    streamingActiveRef.current[chatId] = false;
    textDelta.clearPendingTextDeltaBatch(chatId);
    council.clearPendingCouncilDeltaBatchesForChat(chatId);
    delete pendingDonePayloadsRef.current[chatId];
    setRunUiForChat(chatId, () => undefined);
  }

  async function attachRunStream(chatId: string, run: ChatRun) {
    if (!selectedChatId || selectedChatId !== chatId) {
      return;
    }

    if (streamControllersRef.current[chatId]) {
      return;
    }

    ensureOptimisticMessages(chatId, run);
    const optimisticAssistantId = optimisticIdsForRun(run.id).assistantId;
    const checkpointSeed = getCheckpointSeed(chatId, run.id);
    const controller = new AbortController();
    streamControllersRef.current[chatId] = controller;
    streamingActiveRef.current[chatId] = false;

    try {
      await streamRunSession(
        run.id,
        {
          onSequence: (seq) =>
            setRunUiForChat(chatId, (current) =>
              current ? { ...current, lastSeenSeq: Math.max(current.lastSeenSeq, seq) } : current,
            ),
          onState: (code) => updateRunUiStreamStatus(chatId, { source: "state", code }),
          onEvent: (code, payload) => {
            if (
              code !== "text_delta"
              && code !== "text_checkpoint"
              && code !== "magi_role_text_delta"
              && code !== "magi_role_text_checkpoint"
            ) {
              updateRunUiStreamStatus(chatId, { source: "event", code, payload });
            }

            if (code === "magi_role_start" && payload) {
              council.handleMagiRoleStart(chatId, payload);
            }

            if (code === "magi_role_text_delta" && payload) {
              council.handleMagiRoleTextDelta(chatId, payload);
            }

            if (code === "magi_role_complete" && payload) {
              council.handleMagiRoleComplete(chatId, payload);
            }
          },
          onMagiRoleTextCheckpoint: (payload) => {
            council.handleMagiRoleTextCheckpoint(chatId, payload);
          },
          onTextCheckpoint: (text, seq) => {
            setCheckpointSeed(chatId, run.id, seq, text);
            if (streamingActiveRef.current[chatId]) {
              return;
            }
            applyTextCheckpoint(chatId, optimisticAssistantId, text);
          },
          onTextDelta: (delta) => {
            streamingActiveRef.current[chatId] = true;
            textDelta.queueTextDelta(chatId, optimisticAssistantId, delta);
          },
          onDone: async (payload) => {
            streamingActiveRef.current[chatId] = false;
            delete lastCheckpointRef.current[chatId];

            if (textDelta.hasPendingDelta(chatId) || council.hasPendingCouncilWork(chatId)) {
              pendingDonePayloadsRef.current[chatId] = {
                payload,
                selectedProjectIdAtCompletion: selectedProjectIdRef.current,
              };
              return;
            }

            await finalizeDone(chatId, payload, selectedProjectIdRef.current);
          },
          onCancelled: (message) => {
            streamingActiveRef.current[chatId] = false;
            delete lastCheckpointRef.current[chatId];
            textDelta.clearPendingTextDeltaBatch(chatId);
            setMessagesForChat(chatId, (current) => current.filter((messageItem) => messageItem.id >= 0));
            clearRunUi(chatId);
            updateChatRunStatusRef.current(chatId, null, null);
            onErrorRef.current(message);
          },
          onError: (message) => {
            streamingActiveRef.current[chatId] = false;
            delete lastCheckpointRef.current[chatId];
            textDelta.clearPendingTextDeltaBatch(chatId);
            setMessagesForChat(chatId, (current) => current.filter((messageItem) => messageItem.id >= 0));
            clearRunUi(chatId);
            updateChatRunStatusRef.current(chatId, null, null);
            onErrorRef.current(message);
          },
        },
        {
          afterSeq: checkpointSeed?.seq || 0,
          signal: controller.signal,
        },
      );
    } catch (err) {
      const name = (err as Error).name || "";
      if (name !== "AbortError") {
        onErrorRef.current((err as Error).message);
      }
    } finally {
      streamingActiveRef.current[chatId] = false;
      if (streamControllersRef.current[chatId] === controller) {
        delete streamControllersRef.current[chatId];
      }
    }
  }

  async function handleCancelActiveRun() {
    const runId = selectedChat?.active_run_id || selectedRunUi?.runId;
    if (!runId || !selectedChatId) {
      return;
    }

    try {
      await api.cancelRun(runId);
      updateChatRunStatusRef.current(selectedChatId, runId, "cancel_requested");
    } catch (err) {
      onErrorRef.current((err as Error).message);
    }
  }

  function resetAll() {
    const chatIds = new Set([
      ...Object.keys(streamControllersRef.current),
      ...Object.keys(runUiByChat),
      ...Object.keys(lastCheckpointRef.current),
      ...Object.keys(pendingDonePayloadsRef.current),
    ]);

    chatIds.forEach((chatId) => {
      streamControllersRef.current[chatId]?.abort();
      textDelta.clearPendingTextDeltaBatch(chatId);
      council.clearPendingCouncilDeltaBatchesForChat(chatId);
    });

    streamControllersRef.current = {};
    streamingActiveRef.current = {};
    lastCheckpointRef.current = {};
    pendingDonePayloadsRef.current = {};
    setRunUiByChat({});
  }

  useEffect(() => {
    onTextDrainCompleteRef.current = handleTextDrainComplete;
    onCouncilDrainCompleteRef.current = handleCouncilDrainComplete;
    runUiCouncilEntriesUpdaterRef.current = updateRunUiCouncilEntries;
    runUiStreamStatusUpdaterRef.current = updateRunUiStreamStatus;
  });

  useEffect(() => {
    const previousChatId = previousSelectedChatIdRef.current;
    if (previousChatId && previousChatId !== selectedChatId) {
      const controller = streamControllersRef.current[previousChatId];
      if (controller) {
        controller.abort();
        delete streamControllersRef.current[previousChatId];
      }
      streamingActiveRef.current[previousChatId] = false;
    }

    previousSelectedChatIdRef.current = selectedChatId;
  }, [selectedChatId]);

  useEffect(() => {
    if (!selectedChatId || !selectedChat?.active_run_id) {
      return;
    }

    void api
      .getRun(selectedChat.active_run_id)
      .then((run) => attachRunStream(selectedChatId, run))
      .catch((err: Error) => {
        onErrorRef.current(err.message);
      });
  }, [selectedChat?.active_run_id, selectedChatId]);

  useEffect(() => {
    if (!selectedProjectId) {
      return;
    }

    const hasActiveRuns = chats.some((chat) => chat.active_run_id);
    if (!hasActiveRuns) {
      return;
    }

    const intervalId = window.setInterval(() => {
      void reloadChatsRef.current(selectedProjectId).catch(() => undefined);
    }, 2000);

    return () => {
      window.clearInterval(intervalId);
    };
  }, [chats, selectedProjectId]);

  return {
    selectedRunUi,
    streamStatus,
    streamingAssistantId,
    selectedChatBusy,
    attachRunStream,
    clearRunUi,
    handleCancelActiveRun,
    setRunUiForChat,
    resetAll,
  };
}
