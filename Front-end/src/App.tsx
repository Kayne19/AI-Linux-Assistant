import { CSSProperties, FormEvent, KeyboardEvent, useEffect, useMemo, useRef, useState } from "react";
import { api } from "./api";
import { DebugPanel } from "./debug/DebugPanel";
import { renderMessageContent } from "./renderMessage";
import { getStreamStatusAliases, getStreamStatusKey, getStreamStatusLabel } from "./streamStatusText";
import type { ChatMessage, ChatRun, ChatSession, Project, StreamStatusEvent, User } from "./types";

type AsyncState = "idle" | "loading" | "error";

type CouncilEntry = {
  entryId: string;
  role: string;
  phase: string;
  round?: number;
  text: string;
  complete: boolean;
  streamBuffer?: string;
};

type ChatRunUIState = {
  runId: string;
  clientRequestId: string;
  pendingContent: string;
  streamStatus: StreamStatusEvent | null;
  streamingAssistantId: number | null;
  optimisticUserId: number;
  optimisticAssistantId: number;
  lastSeenSeq: number;
  councilEntries: CouncilEntry[];
};

type PendingTextDeltaBatch = {
  delta: string;
  frameId: number | null;
};


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
  // First try full JSON parse
  try {
    const parsed = JSON.parse(buffer);
    if (typeof parsed?.position === "string") return parsed.position;
  } catch {
    // incomplete JSON, try regex extraction
  }
  // Regex fallback: grab content after "position":"
  const match = buffer.match(/"position"\s*:\s*"([\s\S]*)/);
  if (!match) return "";
  let inner = match[1];
  // Stop at the closing quote (before next field like "confidence" or "key_claims")
  const closeIdx = inner.search(/"\s*,\s*"(?:confidence|key_claims)/);
  if (closeIdx > 0) inner = inner.slice(0, closeIdx);
  // Unescape JSON string sequences safely
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

export default function App() {
  const isDebugMode =
    import.meta.env.DEV ||
    (typeof window !== "undefined" && window.localStorage.getItem("ala_debug") === "1");
  const [usernameInput, setUsernameInput] = useState("");
  const [projectNameInput, setProjectNameInput] = useState("");
  const [projectDescriptionInput, setProjectDescriptionInput] = useState("");
  const [editProjectNameInput, setEditProjectNameInput] = useState("");
  const [editProjectDescriptionInput, setEditProjectDescriptionInput] = useState("");
  const [editChatTitleInput, setEditChatTitleInput] = useState("");
  const [messageInput, setMessageInput] = useState("");

  const [user, setUser] = useState<User | null>(null);
  const [projects, setProjects] = useState<Project[]>([]);
  const [selectedProjectId, setSelectedProjectId] = useState<string>("");
  const [expandedProjectId, setExpandedProjectId] = useState<string>("");
  const [chatListsByProject, setChatListsByProject] = useState<Record<string, ChatSession[]>>({});
  const [chats, setChats] = useState<ChatSession[]>([]);
  const [selectedChatId, setSelectedChatId] = useState<string>("");
  const [messagesByChat, setMessagesByChat] = useState<Record<string, ChatMessage[]>>({});
  const [runUiByChat, setRunUiByChat] = useState<Record<string, ChatRunUIState>>({});
  const [showCreateProjectDialog, setShowCreateProjectDialog] = useState(false);
  const [editingProjectId, setEditingProjectId] = useState<string>("");
  const [editingChatId, setEditingChatId] = useState<string>("");
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [sidebarWidth, setSidebarWidth] = useState(244);
  const [creatingChat, setCreatingChat] = useState(false);
  const [status, setStatus] = useState<AsyncState>("idle");
  const [error, setError] = useState("");
  const [statusAliasIndex, setStatusAliasIndex] = useState(0);
  const [councilMode, setCouncilMode] = useState<"off" | "full" | "lite">("off");
  const [councilActive, setCouncilActive] = useState(false);
  const [councilPanelCollapsed, setCouncilPanelCollapsed] = useState(false);
  const [councilEntries, setCouncilEntries] = useState<CouncilEntry[]>([]);
  const [viewingCouncilMessageId, setViewingCouncilMessageId] = useState<number | null>(null);
  const [debugPanelOpen, setDebugPanelOpen] = useState(false);

  const dragStateRef = useRef<{ active: boolean; width: number }>({ active: false, width: 244 });
  const messagesContainerRef = useRef<HTMLDivElement | null>(null);
  const messagesEndRef = useRef<HTMLDivElement | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const stickToBottomRef = useRef(true);
  const councilFeedRef = useRef<HTMLDivElement | null>(null);
  const councilEndRef = useRef<HTMLDivElement | null>(null);
  const streamControllersRef = useRef<Record<string, AbortController>>({});
  const pendingTextDeltaBatchesRef = useRef<Record<string, PendingTextDeltaBatch>>({});
  const previousSelectedChatIdRef = useRef("");

  const selectedProject = useMemo(
    () => projects.find((project) => project.id === selectedProjectId) || null,
    [projects, selectedProjectId],
  );
  const selectedChat = useMemo(
    () => chats.find((chat) => chat.id === selectedChatId) || null,
    [chats, selectedChatId],
  );
  const messages = selectedChatId ? (messagesByChat[selectedChatId] || []) : [];
  const selectedRunUi = selectedChatId ? (runUiByChat[selectedChatId] || null) : null;
  const streamStatus = selectedRunUi?.streamStatus ?? null;
  const streamingAssistantId = selectedRunUi?.streamingAssistantId ?? null;
  const selectedChatBusy = Boolean(selectedChat?.active_run_id || selectedRunUi);

  async function reloadProjects(userId: string) {
    const nextProjects = await api.listProjects(userId);
    setProjects(nextProjects);
    setChatListsByProject((current) =>
      Object.fromEntries(
        Object.entries(current).filter(([projectId]) => nextProjects.some((project) => project.id === projectId)),
      ),
    );
    setSelectedProjectId((current) =>
      nextProjects.some((project) => project.id === current) ? current : nextProjects[0]?.id || "",
    );
    return nextProjects;
  }

  async function reloadChats(projectId: string) {
    const nextChats = await api.listChats(projectId);
    setChatListsByProject((current) => ({
      ...current,
      [projectId]: nextChats,
    }));
    setChats(nextChats);
    setSelectedChatId((current) =>
      nextChats.some((chat) => chat.id === current) ? current : nextChats[0]?.id || "",
    );
    return nextChats;
  }

  async function reloadMessages(chatId: string) {
    const nextMessages = await api.listMessages(chatId);
    setMessagesByChat((current) => ({
      ...current,
      [chatId]: nextMessages,
    }));
    return nextMessages;
  }

  function setMessagesForChat(chatId: string, updater: (current: ChatMessage[]) => ChatMessage[]) {
    setMessagesByChat((current) => ({
      ...current,
      [chatId]: updater(current[chatId] || []),
    }));
  }

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

  function clearPendingTextDeltaBatch(chatId: string) {
    const pending = pendingTextDeltaBatchesRef.current[chatId];
    if (!pending) {
      return;
    }
    if (pending.frameId !== null) {
      window.cancelAnimationFrame(pending.frameId);
    }
    delete pendingTextDeltaBatchesRef.current[chatId];
  }

  function applyTextCheckpoint(chatId: string, assistantId: number, text: string) {
    clearPendingTextDeltaBatch(chatId);
    setMessagesForChat(chatId, (current) =>
      current.map((message) =>
        message.id === assistantId ? { ...message, content: text } : message,
      ),
    );
  }

  function queueTextDelta(chatId: string, assistantId: number, delta: string) {
    if (!delta) {
      return;
    }
    const batches = pendingTextDeltaBatchesRef.current;
    const batch = batches[chatId] || { delta: "", frameId: null };
    batch.delta += delta;
    batches[chatId] = batch;
    if (batch.frameId !== null) {
      return;
    }
    batch.frameId = window.requestAnimationFrame(() => {
      const currentBatch = pendingTextDeltaBatchesRef.current[chatId];
      if (!currentBatch) {
        return;
      }
      const bufferedDelta = currentBatch.delta;
      currentBatch.delta = "";
      currentBatch.frameId = null;
      if (!bufferedDelta) {
        return;
      }
      setRunUiForChat(chatId, (current) =>
        current ? { ...current, streamStatus: { source: "event", code: "text_delta" } } : current,
      );
      setMessagesForChat(chatId, (current) =>
        current.map((message) =>
          message.id === assistantId ? { ...message, content: message.content + bufferedDelta } : message,
        ),
      );
    });
  }

  useEffect(
    () => () => {
      Object.values(pendingTextDeltaBatchesRef.current).forEach((batch) => {
        if (batch.frameId !== null) {
          window.cancelAnimationFrame(batch.frameId);
        }
      });
      pendingTextDeltaBatchesRef.current = {};
    },
    [],
  );

  function ensureOptimisticMessages(chatId: string, run: ChatRun) {
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
      content: run.partial_assistant_text || "",
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
      streamStatus: run.latest_state_code ? { source: "state", code: run.latest_state_code } : { source: "state", code: "START" },
      streamingAssistantId: optimisticIds.assistantId,
      optimisticUserId: optimisticIds.userId,
      optimisticAssistantId: optimisticIds.assistantId,
      lastSeenSeq: Math.max(current?.lastSeenSeq || 0, run.latest_event_seq || 0),
      councilEntries: current?.councilEntries || [],
    }));
  }

  function clearRunUi(chatId: string) {
    const controller = streamControllersRef.current[chatId];
    if (controller) {
      controller.abort();
      delete streamControllersRef.current[chatId];
    }
    clearPendingTextDeltaBatch(chatId);
    setRunUiForChat(chatId, () => undefined);
  }

  function scrollMessagesToBottom(behavior: ScrollBehavior = "auto") {
    const container = messagesContainerRef.current;
    if (!container) {
      return;
    }
    container.scrollTo({
      top: container.scrollHeight,
      behavior,
    });
  }

  function updateStickToBottom() {
    const container = messagesContainerRef.current;
    if (!container) {
      return;
    }
    const distanceFromBottom = container.scrollHeight - container.scrollTop - container.clientHeight;
    stickToBottomRef.current = distanceFromBottom <= 64;
  }

  function autoResizeTextarea() {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 240)}px`;
  }

  useEffect(() => {
    autoResizeTextarea();
  }, [messageInput]);

  useEffect(() => {
    if (!user || !selectedProjectId) {
      setChats([]);
      setSelectedChatId("");
      return;
    }

    void reloadChats(selectedProjectId).catch((err: Error) => {
      setError(err.message);
      setStatus("error");
    });
  }, [user, selectedProjectId]);

  useEffect(() => {
    if (!selectedProjectId) {
      return;
    }
    const cachedChats = chatListsByProject[selectedProjectId];
    if (!cachedChats) {
      return;
    }
    setChats(cachedChats);
    setSelectedChatId((current) =>
      cachedChats.some((chat) => chat.id === current) ? current : cachedChats[0]?.id || "",
    );
  }, [chatListsByProject, selectedProjectId]);


  useEffect(() => {
    if (!selectedProjectId) {
      setExpandedProjectId("");
      return;
    }

    setExpandedProjectId((current) => {
      if (current && projects.some((project) => project.id === current)) {
        return current;
      }
      return selectedProjectId;
    });
  }, [projects, selectedProjectId]);

  useEffect(() => {
    setCouncilActive(false);
    setCouncilEntries([]);
    if (!selectedChatId) {
      return;
    }

    if (!messagesByChat[selectedChatId]) {
      void reloadMessages(selectedChatId).catch((err: Error) => {
        setError(err.message);
        setStatus("error");
      });
    }
  }, [selectedChatId]);

  useEffect(() => {
    if (!stickToBottomRef.current) {
      return;
    }
    requestAnimationFrame(() => {
      scrollMessagesToBottom(selectedChatBusy ? "auto" : messages.length > 0 ? "smooth" : "auto");
    });
  }, [messages, selectedChatBusy]);

  useEffect(() => {
    stickToBottomRef.current = true;
    requestAnimationFrame(() => {
      scrollMessagesToBottom();
    });
  }, [selectedChatId]);

  useEffect(() => {
    if (!selectedChatBusy) {
      return;
    }
    requestAnimationFrame(() => {
      scrollMessagesToBottom("auto");
    });
  }, [selectedChatBusy, streamStatus]);

  useEffect(() => {
    councilEndRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [councilEntries]);

  const liveStatusKey = getStreamStatusKey(streamStatus);
  const liveStatusAliases = getStreamStatusAliases(streamStatus);

  useEffect(() => {
    setStatusAliasIndex(0);
    if (!selectedChatBusy || liveStatusAliases.length <= 1) {
      return;
    }

    const intervalId = window.setInterval(() => {
      setStatusAliasIndex((current) => (current + 1) % liveStatusAliases.length);
    }, 2400);

    return () => {
      window.clearInterval(intervalId);
    };
  }, [liveStatusKey, liveStatusAliases, selectedChatBusy]);

  useEffect(() => {
    function onPointerMove(event: PointerEvent) {
      if (!dragStateRef.current.active || sidebarCollapsed) {
        return;
      }
      const nextWidth = Math.min(360, Math.max(220, event.clientX));
      dragStateRef.current.width = nextWidth;
      setSidebarWidth(nextWidth);
    }

    function onPointerUp() {
      dragStateRef.current.active = false;
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
    }

    window.addEventListener("pointermove", onPointerMove);
    window.addEventListener("pointerup", onPointerUp);
    return () => {
      window.removeEventListener("pointermove", onPointerMove);
      window.removeEventListener("pointerup", onPointerUp);
    };
  }, [sidebarCollapsed]);

  function updateChatRunStatus(chatId: string, activeRunId: string | null, activeRunStatus: string | null) {
    const apply = (items: ChatSession[]) =>
      items.map((chat) =>
        chat.id === chatId ? { ...chat, active_run_id: activeRunId, active_run_status: activeRunStatus } : chat,
      );
    setChats((current) => apply(current));
    setChatListsByProject((current) =>
      Object.fromEntries(
        Object.entries(current).map(([projectId, items]) => [projectId, apply(items)]),
      ),
    );
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
    const controller = new AbortController();
    streamControllersRef.current[chatId] = controller;

    try {
      await api.streamRun(
        run.id,
        {
          onSequence: (seq) => {
            setRunUiForChat(chatId, (current) =>
              current ? { ...current, lastSeenSeq: Math.max(current.lastSeenSeq, seq) } : current,
            );
          },
          onState: (code) =>
            setRunUiForChat(chatId, (current) =>
              current ? { ...current, streamStatus: { source: "state", code } } : current,
            ),
          onEvent: (code, payload) => {
            if (code !== "text_delta" && code !== "text_checkpoint") {
              setRunUiForChat(chatId, (current) =>
                current ? { ...current, streamStatus: { source: "event", code, payload } } : current,
              );
            }
            if (code === "magi_role_start" && payload) {
              const role = String(payload.role || "");
              const phase = String(payload.phase || "");
              const round = typeof payload.round === "number" ? payload.round : undefined;
              const entryId = `${phase}-${role}-${round ?? 0}`;
              setRunUiForChat(chatId, (current) =>
                current
                  ? {
                      ...current,
                      councilEntries: [
                        ...current.councilEntries.filter((entry) => entry.entryId !== entryId),
                        { entryId, role, phase, round, text: "", complete: false },
                      ],
                    }
                  : current,
              );
            }
            if (code === "magi_role_text_delta" && payload) {
              const role = String(payload.role || "");
              const phase = String(payload.phase || "");
              const round = typeof payload.round === "number" ? payload.round : undefined;
              const delta = String(payload.delta || "");
              const entryId = `${phase}-${role}-${round ?? 0}`;
              setRunUiForChat(chatId, (current) =>
                current
                  ? {
                      ...current,
                      councilEntries: current.councilEntries.map((entry) =>
                        entry.entryId === entryId
                          ? { ...entry, streamBuffer: (entry.streamBuffer ?? "") + delta }
                          : entry,
                      ),
                    }
                  : current,
              );
            }
            if (code === "magi_role_complete" && payload) {
              const role = String(payload.role || "");
              const phase = String(payload.phase || "");
              const round = typeof payload.round === "number" ? payload.round : undefined;
              const text = String(payload.text || "");
              const entryId = `${phase}-${role}-${round ?? 0}`;
              setRunUiForChat(chatId, (current) =>
                current
                  ? {
                      ...current,
                      councilEntries: current.councilEntries.map((entry) =>
                        entry.entryId === entryId ? { ...entry, text, complete: true, streamBuffer: undefined } : entry,
                      ),
                    }
                  : current,
              );
            }
          },
          onTextCheckpoint: (text) => {
            applyTextCheckpoint(chatId, optimisticAssistantId, text);
          },
          onTextDelta: (delta) => {
            queueTextDelta(chatId, optimisticAssistantId, delta);
          },
          onDone: async (payload) => {
            clearPendingTextDeltaBatch(chatId);
            setMessagesForChat(chatId, (current) => [
              ...current.filter((message) => message.id >= 0),
              payload.user_message,
              payload.assistant_message,
            ]);
            clearRunUi(chatId);
            updateChatRunStatus(chatId, null, null);
            if (selectedProjectId) {
              await reloadChats(selectedProjectId);
            }
          },
          onCancelled: (message) => {
            clearPendingTextDeltaBatch(chatId);
            setMessagesForChat(chatId, (current) => current.filter((messageItem) => messageItem.id >= 0));
            clearRunUi(chatId);
            updateChatRunStatus(chatId, null, null);
            setError(message);
          },
          onError: (message) => {
            clearPendingTextDeltaBatch(chatId);
            setMessagesForChat(chatId, (current) => current.filter((messageItem) => messageItem.id >= 0));
            clearRunUi(chatId);
            updateChatRunStatus(chatId, null, null);
            setError(message);
          },
        },
        {
          afterSeq: runUiByChat[chatId]?.lastSeenSeq || 0,
          signal: controller.signal,
        },
      );
    } catch (err) {
      const name = (err as Error).name || "";
      if (name !== "AbortError") {
        setError((err as Error).message);
      }
    } finally {
      if (streamControllersRef.current[chatId] === controller) {
        delete streamControllersRef.current[chatId];
      }
    }
  }

  useEffect(() => {
    const previousChatId = previousSelectedChatIdRef.current;
    if (previousChatId && previousChatId !== selectedChatId) {
      const controller = streamControllersRef.current[previousChatId];
      if (controller) {
        controller.abort();
        delete streamControllersRef.current[previousChatId];
      }
    }
    previousSelectedChatIdRef.current = selectedChatId;
  }, [selectedChatId]);

  useEffect(() => {
    if (!selectedChatId || !selectedChat?.active_run_id) {
      return;
    }
    void api.getRun(selectedChat.active_run_id)
      .then((run) => {
        ensureOptimisticMessages(selectedChatId, run);
        return attachRunStream(selectedChatId, run);
      })
      .catch((err: Error) => {
        setError(err.message);
      });
  }, [selectedChatId, selectedChat?.active_run_id]);

  useEffect(() => {
    if (viewingCouncilMessageId !== null) {
      return;
    }
    if (!selectedRunUi) {
      setCouncilEntries([]);
      setCouncilActive(false);
      return;
    }
    setCouncilEntries(selectedRunUi.councilEntries);
    setCouncilActive(selectedRunUi.councilEntries.length > 0 || selectedChatBusy);
  }, [selectedRunUi, viewingCouncilMessageId, selectedChatBusy]);

  useEffect(() => {
    if (!selectedProjectId) {
      return;
    }
    const hasActiveRuns = chats.some((chat) => chat.active_run_id);
    if (!hasActiveRuns) {
      return;
    }
    const intervalId = window.setInterval(() => {
      void reloadChats(selectedProjectId).catch(() => undefined);
    }, 2000);
    return () => {
      window.clearInterval(intervalId);
    };
  }, [chats, selectedProjectId]);

  function startSidebarResize() {
    if (sidebarCollapsed) {
      return;
    }
    dragStateRef.current.active = true;
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
  }

  function closeSidebarOnMobile() {
    if (typeof window !== "undefined" && window.innerWidth <= 960) {
      setSidebarCollapsed(true);
    }
  }

  async function handleLogin(event: FormEvent) {
    event.preventDefault();
    setStatus("loading");
    setError("");
    try {
      const result = await api.bootstrap(usernameInput);
      setUser(result.user);
      setProjects(result.projects);
      setChatListsByProject(result.chats_by_project);
      setSelectedProjectId(result.projects[0]?.id || "");
      setSelectedChatId("");
      setMessagesByChat({});
      setRunUiByChat({});
      setStatus("idle");
    } catch (err) {
      setError((err as Error).message);
      setStatus("error");
    }
  }

  async function handleCreateProject(event: FormEvent) {
    event.preventDefault();
    if (!user) return;
    setStatus("loading");
    setError("");
    try {
      const project = await api.createProject({
        user_id: user.id,
        name: projectNameInput,
        description: projectDescriptionInput,
      });
      await reloadProjects(user.id);
      setSelectedProjectId(project.id);
      setSelectedChatId("");
      setProjectNameInput("");
      setProjectDescriptionInput("");
      setShowCreateProjectDialog(false);
      setStatus("idle");
    } catch (err) {
      setError((err as Error).message);
      setStatus("error");
    }
  }

  async function handleCreateChat() {
    if (!selectedProjectId || creatingChat) return;
    if (selectedChat && messages.length === 0) {
      closeSidebarOnMobile();
      return;
    }
    setCreatingChat(true);
    setStatus("loading");
    setError("");
    try {
      const chat = await api.createChat(selectedProjectId, "");
      await reloadChats(selectedProjectId);
      setSelectedChatId(chat.id);
      setStatus("idle");
    } catch (err) {
      setError((err as Error).message);
      setStatus("error");
    } finally {
      setCreatingChat(false);
    }
  }

  function openEditProjectDialog(project: Project) {
    setEditingProjectId(project.id);
    setEditProjectNameInput(project.name || "");
    setEditProjectDescriptionInput(project.description || "");
    setError("");
  }

  function closeEditProjectDialog() {
    setEditingProjectId("");
    setEditProjectNameInput("");
    setEditProjectDescriptionInput("");
  }

  async function handleEditProject(event: FormEvent) {
    event.preventDefault();
    if (!editingProjectId || !user) return;
    setStatus("loading");
    setError("");
    try {
      const updatedProject = await api.updateProject(editingProjectId, {
        name: editProjectNameInput,
        description: editProjectDescriptionInput,
      });
      setProjects((current) =>
        current.map((project) => (project.id === updatedProject.id ? updatedProject : project)),
      );
      setSelectedProjectId(updatedProject.id);
      closeEditProjectDialog();
      await reloadProjects(user.id);
      setStatus("idle");
    } catch (err) {
      setError((err as Error).message);
      setStatus("error");
    }
  }

  async function handleDeleteProject() {
    if (!editingProjectId || !user) return;
    setStatus("loading");
    setError("");
    try {
      await api.deleteProject(editingProjectId);
      if (selectedProjectId === editingProjectId) {
        setSelectedProjectId("");
        setSelectedChatId("");
        setMessagesByChat({});
        setRunUiByChat({});
      }
      closeEditProjectDialog();
      await reloadProjects(user.id);
      setStatus("idle");
    } catch (err) {
      setError((err as Error).message);
      setStatus("error");
    }
  }

  function openEditChatDialog(chat: ChatSession) {
    setEditingChatId(chat.id);
    setEditChatTitleInput(chat.title || "");
    setError("");
  }

  function closeEditChatDialog() {
    setEditingChatId("");
    setEditChatTitleInput("");
  }

  async function handleEditChat(event: FormEvent) {
    event.preventDefault();
    if (!editingChatId || !selectedProjectId) return;
    setStatus("loading");
    setError("");
    try {
      const updatedChat = await api.updateChat(editingChatId, {
        title: editChatTitleInput,
      });
      setChats((current) =>
        current.map((chat) => (chat.id === updatedChat.id ? updatedChat : chat)),
      );
      setSelectedChatId(updatedChat.id);
      closeEditChatDialog();
      await reloadChats(selectedProjectId);
      setStatus("idle");
    } catch (err) {
      setError((err as Error).message);
      setStatus("error");
    }
  }

  async function handleDeleteChat() {
    if (!editingChatId || !selectedProjectId) return;
    setStatus("loading");
    setError("");
    try {
      await api.deleteChat(editingChatId);
      if (selectedChatId === editingChatId) {
        setSelectedChatId("");
        setMessagesByChat((current) => {
          const next = { ...current };
          delete next[editingChatId];
          return next;
        });
        clearRunUi(editingChatId);
      }
      closeEditChatDialog();
      await reloadChats(selectedProjectId);
      setStatus("idle");
    } catch (err) {
      setError((err as Error).message);
      setStatus("error");
    }
  }

  function handleViewCouncilEntries(message: ChatMessage) {
    const stored = message.council_entries;
    if (!stored?.length) return;
    const entries: CouncilEntry[] = stored.map((e) => ({
      entryId: `${e.phase}-${e.role}-${e.round ?? 0}`,
      role: e.role,
      phase: e.phase,
      round: e.round ?? undefined,
      text: e.text,
      complete: true,
    }));
    setCouncilEntries(entries);
    setCouncilActive(true);
    setCouncilPanelCollapsed(false);
    setViewingCouncilMessageId(message.id);
  }

  async function handleCancelActiveRun() {
    const runId = selectedChat?.active_run_id || selectedRunUi?.runId;
    if (!runId || !selectedChatId) {
      return;
    }
    try {
      await api.cancelRun(runId);
      updateChatRunStatus(selectedChatId, runId, "cancel_requested");
    } catch (err) {
      setError((err as Error).message);
    }
  }

  async function handleSendMessage(event: FormEvent) {
    event.preventDefault();
    if (!selectedChatId || !messageInput.trim() || selectedChatBusy) return;
    setError("");
    const content = messageInput;
    const clientRequestId = globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random()}`;
    try {
      setMessageInput("");
      if (councilMode !== "off") {
        setCouncilActive(true);
        setCouncilPanelCollapsed(false);
        setCouncilEntries([]);
        setViewingCouncilMessageId(null);
      }
      stickToBottomRef.current = true;
      requestAnimationFrame(() => {
        scrollMessagesToBottom("smooth");
      });
      const run = await api.createRun(selectedChatId, content, {
        magi: councilMode,
        clientRequestId,
      });
      updateChatRunStatus(selectedChatId, run.id, run.status);
      ensureOptimisticMessages(selectedChatId, run);
      await attachRunStream(selectedChatId, run);
    } catch (err) {
      setMessageInput(content);
      setError((err as Error).message);
    }
  }

  function handleComposerKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key !== "Enter" || event.shiftKey || event.nativeEvent.isComposing) {
      return;
    }
    event.preventDefault();
    event.currentTarget.form?.requestSubmit();
  }

  const appShellStyle = {
    "--sidebar-width": `${sidebarCollapsed ? 0 : sidebarWidth}px`,
  } as CSSProperties;
  const composerPlaceholder = !selectedProject
    ? "Create a project first."
    : !selectedChatId
      ? "Create a chat inside this project."
      : messages.length === 0
        ? `Ask about ${selectedProject.name}...`
        : "Reply...";
  const liveStatusLabel = getStreamStatusLabel(streamStatus);
  const liveStatusSubtext = liveStatusAliases[statusAliasIndex] || liveStatusAliases[0] || liveStatusLabel;

  if (!user) {
    return (
      <main className="auth-page">
        <section className="auth-panel">
          <div className="auth-copy">
            <p className="eyebrow">AI Linux Assistant</p>
            <h1>Sign in to enter your workspace.</h1>
            <p>
              Projects, chats, and memory live behind a named workspace. Pick a username to continue.
            </p>
          </div>

          <form className="auth-form" onSubmit={handleLogin}>
            <label className="stack">
              <span className="label">Username</span>
              <input
                value={usernameInput}
                onChange={(event) => setUsernameInput(event.target.value)}
                placeholder="kayne19"
                autoFocus
              />
            </label>
            {error ? <p className="error-banner auth-error">{error}</p> : null}
            <button type="submit" disabled={status === "loading" || !usernameInput.trim()}>
              {status === "loading" ? "Entering..." : "Enter workspace"}
            </button>
          </form>
        </section>
      </main>
    );
  }

  return (
    <>
    <div className="app-shell" style={appShellStyle}>
      <aside className={`sidebar ${sidebarCollapsed ? "collapsed" : ""}`}>
        {!sidebarCollapsed ? (
          <>
            <div className="sidebar-top">
              <div className="brand-copy">
                <p className="eyebrow">Project workspace</p>
                <h1>AI Linux Assistant</h1>
                <p className="lede">Stateful troubleshooting anchored to projects, not disposable chats.</p>
              </div>
            </div>

            <div className="sidebar-content">
            <section className="rail-section user-section">
              <div className="user-chip" title={user.username}>
                <div className="user-meta">
                  <span className="eyebrow">Signed in</span>
                  <strong className="user-name">{user.username}</strong>
                </div>
              </div>
            </section>

            <section className="rail-section">
              <div className="rail-section-header">
                <div>
                  <p className="eyebrow">Projects</p>
                  <h2>{projects.length} workspaces</h2>
                </div>
                <button
                  type="button"
                  className="subtle-action"
                  onClick={() => setShowCreateProjectDialog(true)}
                  aria-label="Create project"
                >
                  + New
                </button>
              </div>

              <div className="project-tree">
                {projects.map((project) => {
                  const active = project.id === selectedProjectId;
                  const expanded = project.id === expandedProjectId;
                  const projectChats = chatListsByProject[project.id] ?? (active ? chats : []);

                  return (
                    <div key={project.id} className={`project-tree-item ${active ? "active" : ""}`}>
                      <div className="project-row">
                        <button
                          className={`rail-item project-item ${active ? "active" : ""}`}
                          title={project.name}
                          onClick={() => {
                            if (active) {
                              setExpandedProjectId((current) => (current === project.id ? "" : project.id));
                              return;
                            }

                            const cachedChats = chatListsByProject[project.id];
                            if (cachedChats) {
                              setChats(cachedChats);
                              setSelectedChatId((current) =>
                                cachedChats.some((chat) => chat.id === current) ? current : cachedChats[0]?.id || "",
                              );
                            }
                            setSelectedProjectId(project.id);
                            setExpandedProjectId(project.id);
                            closeSidebarOnMobile();
                          }}
                        >
                          <span className="rail-item-copy">
                            <strong>{project.name}</strong>
                            <small>{project.description || "No description"}</small>
                          </span>
                        </button>
                        <button
                          type="button"
                          className="project-edit-trigger"
                          aria-label={`Edit ${project.name}`}
                          onClick={(event) => {
                            event.stopPropagation();
                            openEditProjectDialog(project);
                          }}
                        >
                          <svg viewBox="0 0 20 20" aria-hidden="true" className="chat-edit-icon">
                            <path
                              d="M13.9 3.1a2.2 2.2 0 0 1 3.1 3.1l-8.8 8.8-3.7.6.6-3.7 8.8-8.8Zm-7.8 9.5-.2 1.1 1.1-.2 7.9-7.9a.8.8 0 1 0-1.1-1.1l-7.7 8.1Z"
                              fill="currentColor"
                            />
                          </svg>
                        </button>
                      </div>

                      {expanded ? (
                        <div className="project-tree-children">
                          <div className="project-tree-header">
                            <span className="project-tree-label">Chats</span>
                            <button
                              type="button"
                              className="subtle-action"
                              onClick={handleCreateChat}
                              disabled={creatingChat}
                            >
                              + New
                            </button>
                          </div>

                          {projectChats.length > 0 ? (
                            <div className="nested-chat-list">
                              {projectChats.map((chat) => (
                                <div
                                  key={chat.id}
                                  className={`nested-chat-row ${chat.id === selectedChatId ? "active" : ""}`}
                                >
                                  <button
                                    className={`nested-chat-item ${chat.id === selectedChatId ? "active" : ""}`}
                                    title={chat.title || "Untitled chat"}
                                    onClick={() => {
                                      setSelectedChatId(chat.id);
                                      closeSidebarOnMobile();
                                    }}
                                  >
                                    <span className="nested-chat-copy">
                                      <strong>{chat.title || "Untitled chat"}</strong>
                                      <small>{formatChatTimestamp(chat.updated_at || chat.created_at)}</small>
                                    </span>
                                  </button>
                                  <button
                                    type="button"
                                    className="chat-edit-trigger"
                                    aria-label={`Edit ${chat.title || "chat"}`}
                                    onClick={(event) => {
                                      event.stopPropagation();
                                      openEditChatDialog(chat);
                                    }}
                                  >
                                    <svg viewBox="0 0 20 20" aria-hidden="true" className="chat-edit-icon">
                                      <path
                                        d="M13.9 3.1a2.2 2.2 0 0 1 3.1 3.1l-8.8 8.8-3.7.6.6-3.7 8.8-8.8Zm-7.8 9.5-.2 1.1 1.1-.2 7.9-7.9a.8.8 0 1 0-1.1-1.1l-7.7 8.1Z"
                                        fill="currentColor"
                                      />
                                    </svg>
                                  </button>
                                </div>
                              ))}
                            </div>
                          ) : (
                            <div className="nested-chat-empty">
                              <p>No chats in this project yet.</p>
                                <button
                                  type="button"
                                  className="ghost-button compact"
                                  onClick={handleCreateChat}
                                  disabled={creatingChat}
                                >
                                  Create chat
                                </button>
                            </div>
                          )}
                        </div>
                      ) : null}
                    </div>
                  );
                })}

                {projects.length === 0 ? <p className="empty">No projects yet.</p> : null}
              </div>
            </section>

            </div>
            <div className="sidebar-footer">
              <div className="sidebar-footer-actions">
                {isDebugMode ? (
                  <button
                    type="button"
                    className={`debug-chip${debugPanelOpen ? " active" : ""}`}
                    onClick={() => setDebugPanelOpen((current) => !current)}
                  >
                    [DBG]
                  </button>
                ) : null}
                <button
                  type="button"
                  className="collapse-button"
                  onClick={() => setSidebarCollapsed(true)}
                  aria-label="Collapse sidebar"
                >
                  « collapse
                </button>
              </div>
            </div>
            
          </>
        ) : null}
      </aside>

      {!sidebarCollapsed ? (
        <button
          type="button"
          className="mobile-sidebar-backdrop"
          aria-label="Close sidebar"
          onClick={() => setSidebarCollapsed(true)}
        />
      ) : null}

      <div
        className={`sidebar-resize-handle ${sidebarCollapsed ? "disabled" : ""}`}
        onPointerDown={startSidebarResize}
        onClick={() => {
          if (sidebarCollapsed) {
            setSidebarCollapsed(false);
          }
        }}
        role="button"
        aria-label={sidebarCollapsed ? "Expand sidebar" : "Resize sidebar"}
      >
        {sidebarCollapsed ? <span className="sidebar-expand-glyph">»</span> : null}
      </div>

      <main className="main-panel">
        <button
          type="button"
          className="mobile-sidebar-toggle"
          aria-label={sidebarCollapsed ? "Open sidebar" : "Close sidebar"}
          aria-expanded={!sidebarCollapsed}
          onClick={() => setSidebarCollapsed((current) => !current)}
        >
          {sidebarCollapsed ? "Menu" : "Close"}
        </button>
        <div className="workspace-shell">
          <section className={`chat-stage${councilActive && !councilPanelCollapsed ? " council-open" : ""}`}>
            <section className="council-panel">
              <div className="council-panel-header">
                <span className="eyebrow">Council</span>
                <span className="council-panel-label">
                  {viewingCouncilMessageId !== null ? "Past deliberation" : "Agents deliberating"}
                </span>
                <button
                  type="button"
                  className="council-panel-close"
                  aria-label="Close council panel"
                  onClick={() => setCouncilPanelCollapsed(true)}
                >
                  <svg viewBox="0 0 20 20" aria-hidden="true" width="14" height="14">
                    <path d="M5 5l10 10M15 5L5 15" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
                  </svg>
                </button>
              </div>
              <div className="council-feed" ref={councilFeedRef}>
                {councilEntries.map((entry) => (
                  <div
                    key={entry.entryId}
                    className={`council-entry council-role-${entry.role}${entry.complete ? "" : " pending"}`}
                  >
                    <div className="council-entry-header">
                      <span className={`role-badge role-${entry.role}`}>{entry.role}</span>
                      <span className="council-entry-phase">{formatCouncilPhase(entry.phase, entry.round)}</span>
                    </div>
                    {entry.complete ? (
                      <p className="council-entry-text">{entry.text}</p>
                    ) : entry.streamBuffer ? (
                      <p className="council-entry-text streaming">
                        {getStreamingDisplayText(entry.streamBuffer) || "…"}
                        <span className="stream-cursor" aria-hidden="true" />
                      </p>
                    ) : (
                      <div className="council-entry-loading">
                        <span className="status-dot" aria-hidden="true" />
                        <span>Deliberating…</span>
                      </div>
                    )}
                  </div>
                ))}
                <div ref={councilEndRef} />
              </div>
            </section>
            <section className="chat-card">
              <div
                ref={messagesContainerRef}
                className="messages"
                onScroll={updateStickToBottom}
              >
                {!selectedProject ? (
                  <div className="empty-state">
                    <p className="eyebrow">No project selected</p>
                    <h3>Create a project to start organizing chats.</h3>
                    <p>
                      Projects are the container. Each project keeps its own set of chats and context.
                    </p>
                    <button type="button" className="empty-state-action" onClick={() => setShowCreateProjectDialog(true)}>
                      Create project
                    </button>
                  </div>
                ) : !selectedChatId ? (
                  <div className="empty-state">
                    <p className="eyebrow">No chat selected</p>
                    <h3>Open a chat inside {selectedProject.name}.</h3>
                    <p>
                      This project is active, but you still need a chat thread before you can start asking questions.
                    </p>
                    <button type="button" className="empty-state-action" onClick={handleCreateChat} disabled={creatingChat}>
                      Create chat
                    </button>
                  </div>
                ) : messages.length === 0 ? (
                  <div className="empty-state">
                    <p className="eyebrow">Ready</p>
                    <h3>{selectedChat?.title || "Start a conversation tied to this project."}</h3>
                    <p>
                      The assistant keeps troubleshooting context scoped to {selectedProject.name}, so this thread can build on prior work.
                    </p>
                  </div>
                ) : (
                  <>
                    {messages.map((message) => (
                      <article
                        key={`${message.session_id}-${message.id}-${message.role}`}
                        className={`message ${message.role === "user" ? "user" : "assistant"}`}
                      >
                        {message.role === "user" ? (
                          <div className="message-meta-row">
                            <span className="message-time">{formatMessageTimestamp(message.created_at)}</span>
                          </div>
                        ) : null}
                        {message.id === streamingAssistantId && selectedChatBusy ? (
                          <>
                            <div className="message-role">{liveStatusLabel}</div>
                            <div className={`message-status-subtext ${message.content.trim() ? "inline" : ""}`}>
                              {!message.content.trim() ? <span className="status-dot" aria-hidden="true" /> : null}
                              <p>{liveStatusSubtext}</p>
                            </div>
                            {message.content.trim() ? (
                              <div className="message-content">
                                {renderMessageContent(message.content)}
                              </div>
                            ) : (
                              <div className="message-content live-status-content" />
                            )}
                          </>
                        ) : (
                          <>
                            <div className="message-content">
                              {message.role === "user" ? <p>{message.content}</p> : renderMessageContent(message.content)}
                            </div>
                            {message.role !== "user" && message.council_entries?.length ? (
                              <button
                                type="button"
                                className={`council-replay-btn${viewingCouncilMessageId === message.id ? " active" : ""}`}
                                onClick={() => handleViewCouncilEntries(message)}
                                title="View council deliberation for this response"
                              >
                                See council discussion
                              </button>
                            ) : null}
                          </>
                        )}
                      </article>
                    ))}
                  </>
                )}
                <div ref={messagesEndRef} />
              </div>

            </section>
          </section>
        </div>
        <form className="composer" onSubmit={handleSendMessage}>
          <div className="composer-shell">
            {error ? <p className="composer-error-text">{error}</p> : null}
            <div className="composer-input-wrap">
              <div className="composer-left-actions">
                <button
                  type="button"
                  className={`council-toggle-btn${councilMode === "lite" ? " active lite" : councilMode === "full" ? " active" : ""}`}
                  onClick={() => setCouncilMode((m) => m === "off" ? "full" : m === "full" ? "lite" : "off")}
                  title="Council mode: click to cycle off → full → lite → off"
                >
                  <svg viewBox="0 0 20 20" aria-hidden="true" className="council-icon">
                    <circle cx="10" cy="10" r="6.5" stroke="currentColor" strokeWidth="1.4" fill="none" />
                    <path d="M7 10l2 2 4-4" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" fill="none" className="council-check" />
                  </svg>
                  {councilMode === "lite" ? "Council Lite" : "Council"}
                </button>
                {selectedChatBusy ? (
                  <button type="button" className="ghost-button compact" onClick={handleCancelActiveRun}>
                    Cancel run
                  </button>
                ) : null}
              </div>
              <textarea
                ref={textareaRef}
                value={messageInput}
                onChange={(event) => { setMessageInput(event.target.value); autoResizeTextarea(); }}
                onKeyDown={handleComposerKeyDown}
                placeholder={composerPlaceholder}
                disabled={!selectedChatId || selectedChatBusy}
              />
              <div className="composer-actions">
                <button
                  type="submit"
                  className="composer-send"
                  aria-label="Send message"
                  disabled={!selectedChatId || !messageInput.trim() || selectedChatBusy}
                >
                  <svg viewBox="0 0 20 20" aria-hidden="true" className="send-icon">
                    <path
                      d="M3 10L16 4L11 17L9.5 11.5L3 10Z"
                      fill="currentColor"
                      stroke="none"
                    />
                  </svg>
                </button>
              </div>
            </div>
          </div>
        </form>
      </main>
      {debugPanelOpen && isDebugMode ? <DebugPanel chatId={selectedChatId} onClose={() => setDebugPanelOpen(false)} /> : null}
    </div>
    {showCreateProjectDialog ? (
      <div className="dialog-backdrop" onClick={() => setShowCreateProjectDialog(false)}>
        <div className="dialog-card" role="dialog" aria-modal="true" aria-labelledby="create-project-title" onClick={(event) => event.stopPropagation()}>
          <div className="dialog-header">
            <div>
              <p className="eyebrow">New project</p>
              <h2 id="create-project-title">Create a project workspace.</h2>
            </div>
            <button type="button" className="icon-button" aria-label="Close project dialog" onClick={() => setShowCreateProjectDialog(false)}>
              ×
            </button>
          </div>
          <form className="dialog-form" onSubmit={handleCreateProject}>
            <input
              value={projectNameInput}
              onChange={(event) => setProjectNameInput(event.target.value)}
              placeholder="Debian laptop"
              autoFocus
            />
            <textarea
              rows={3}
              value={projectDescriptionInput}
              onChange={(event) => setProjectDescriptionInput(event.target.value)}
              placeholder="What this machine or stack is for"
            />
            <div className="dialog-actions">
              <button type="button" className="ghost-button compact" onClick={() => setShowCreateProjectDialog(false)}>
                Cancel
              </button>
              <button type="submit" disabled={!projectNameInput.trim() || status === "loading"}>
                Create
              </button>
            </div>
          </form>
        </div>
      </div>
    ) : null}
    {editingProjectId ? (
      <div className="dialog-backdrop" onClick={closeEditProjectDialog}>
        <div className="dialog-card" role="dialog" aria-modal="true" aria-labelledby="edit-project-title" onClick={(event) => event.stopPropagation()}>
          <div className="dialog-header">
            <div>
              <p className="eyebrow">Edit project</p>
              <h2 id="edit-project-title">Update this project’s details.</h2>
            </div>
            <button type="button" className="icon-button" aria-label="Close edit project dialog" onClick={closeEditProjectDialog}>
              ×
            </button>
          </div>
          <form className="dialog-form" onSubmit={handleEditProject}>
            <input
              value={editProjectNameInput}
              onChange={(event) => setEditProjectNameInput(event.target.value)}
              placeholder="Debian laptop"
              autoFocus
            />
            <textarea
              rows={3}
              value={editProjectDescriptionInput}
              onChange={(event) => setEditProjectDescriptionInput(event.target.value)}
              placeholder="What this machine or stack is for"
            />
            {error ? <p className="error-banner">{error}</p> : null}
            <div className="dialog-actions">
              <button type="button" className="danger-button compact" onClick={handleDeleteProject} disabled={status === "loading"}>
                Delete
              </button>
              <button type="button" className="ghost-button compact" onClick={closeEditProjectDialog}>
                Cancel
              </button>
              <button type="submit" disabled={!editProjectNameInput.trim() || status === "loading"}>
                Save
              </button>
            </div>
          </form>
        </div>
      </div>
    ) : null}
    {editingChatId ? (
      <div className="dialog-backdrop" onClick={closeEditChatDialog}>
        <div className="dialog-card" role="dialog" aria-modal="true" aria-labelledby="edit-chat-title" onClick={(event) => event.stopPropagation()}>
          <div className="dialog-header">
            <div>
              <p className="eyebrow">Edit chat</p>
              <h2 id="edit-chat-title">Update this chat’s details.</h2>
            </div>
            <button type="button" className="icon-button" aria-label="Close edit chat dialog" onClick={closeEditChatDialog}>
              ×
            </button>
          </div>
          <form className="dialog-form" onSubmit={handleEditChat}>
            <input
              value={editChatTitleInput}
              onChange={(event) => setEditChatTitleInput(event.target.value)}
              placeholder="Fresh troubleshooting session"
              autoFocus
            />
            {error ? <p className="error-banner">{error}</p> : null}
            <div className="dialog-actions">
              <button type="button" className="danger-button compact" onClick={handleDeleteChat} disabled={status === "loading"}>
                Delete
              </button>
              <button type="button" className="ghost-button compact" onClick={closeEditChatDialog}>
                Cancel
              </button>
              <button type="submit" disabled={!editChatTitleInput.trim() || status === "loading"}>
                Save
              </button>
            </div>
          </form>
        </div>
      </div>
    ) : null}
    </>
  );
}
