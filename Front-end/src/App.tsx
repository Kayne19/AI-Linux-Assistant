import { CSSProperties, FormEvent, startTransition, useEffect, useRef, useState } from "react";
import { api } from "./api";
import { ChatView } from "./components/ChatView";
import { CouncilPanel } from "./components/CouncilPanel";
import { LoginScreen } from "./components/LoginScreen";
import { MessageComposer } from "./components/MessageComposer";
import { Sidebar } from "./components/Sidebar";
import { CreateProjectDialog } from "./components/dialogs/CreateProjectDialog";
import { EditChatDialog } from "./components/dialogs/EditChatDialog";
import { EditProjectDialog } from "./components/dialogs/EditProjectDialog";
import { DebugPanel } from "./debug/DebugPanel";
import { useAuth } from "./hooks/useAuth";
import { useChats } from "./hooks/useChats";
import { useCouncilStreaming } from "./hooks/useCouncilStreaming";
import { useMessages } from "./hooks/useMessages";
import { useProjects } from "./hooks/useProjects";
import { useScrollManager } from "./hooks/useScrollManager";
import { useStreamingRun } from "./hooks/useStreamingRun";
import { useTextDeltaAnimation } from "./hooks/useTextDeltaAnimation";
import { getStreamStatusAliases, getStreamStatusKey, getStreamStatusLabel } from "./streamStatusText";
import type { AsyncState, ChatSession, Project, StreamStatusEvent, UICouncilEntry } from "./types";

export default function App() {
  const [status, setStatus] = useState<AsyncState>("idle");
  const [error, setError] = useState("");
  const [statusAliasIndex, setStatusAliasIndex] = useState(0);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [sidebarWidth, setSidebarWidth] = useState(244);
  const [debugPanelOpen, setDebugPanelOpen] = useState(false);

  const projectsReloadedRef = useRef<(projects: Project[]) => void>(() => undefined);
  const chatDeletedRef = useRef<(chatId: string) => void>(() => undefined);
  const textDrainCompleteRef = useRef<(chatId: string) => void>(() => undefined);
  const councilDrainCompleteRef = useRef<(chatId: string) => void>(() => undefined);
  const runUiCouncilEntriesUpdaterRef = useRef<
    (chatId: string, updater: (entries: UICouncilEntry[]) => UICouncilEntry[]) => void
  >(() => undefined);
  const runUiStreamStatusUpdaterRef = useRef<(chatId: string, streamStatus: StreamStatusEvent) => void>(
    () => undefined,
  );

  const auth = useAuth();
  const isDebugMode = auth.user?.role === "admin";
  const projects = useProjects({
    onStatusChange: setStatus,
    onError: setError,
    onProjectsReloaded: (nextProjects) => projectsReloadedRef.current(nextProjects),
  });
  const chats = useChats({
    enabled: Boolean(auth.user),
    selectedProjectId: projects.selectedProjectId,
    onStatusChange: setStatus,
    onError: setError,
    onChatDeleted: (chatId) => chatDeletedRef.current(chatId),
  });
  const messages = useMessages({ selectedChatId: chats.selectedChatId });
  const council = useCouncilStreaming({
    updateRunUiCouncilEntries: (chatId, updater) => runUiCouncilEntriesUpdaterRef.current(chatId, updater),
    onDrainComplete: (chatId) => councilDrainCompleteRef.current(chatId),
  });
  const textDelta = useTextDeltaAnimation({
    onDrainChunk: (chatId, assistantId, chunk) => {
      messages.setMessagesForChat(chatId, (current) =>
        current.map((message) =>
          message.id === assistantId ? { ...message, content: message.content + chunk } : message,
        ),
      );
    },
    onDrainComplete: (chatId) => textDrainCompleteRef.current(chatId),
    onStreamStatusUpdate: (chatId) =>
      runUiStreamStatusUpdaterRef.current(chatId, {
        source: "event",
        code: "text_delta",
      }),
  });
  const streaming = useStreamingRun({
    chats: chats.chats,
    selectedChatId: chats.selectedChatId,
    selectedChat: chats.selectedChat,
    selectedProjectId: projects.selectedProjectId,
    textDelta,
    council,
    reloadMessages: messages.reloadMessages,
    setMessagesForChat: messages.setMessagesForChat,
    reloadChats: chats.reloadChats,
    updateChatRunStatus: chats.updateChatRunStatus,
    onError: setError,
    onTextDrainCompleteRef: textDrainCompleteRef,
    onCouncilDrainCompleteRef: councilDrainCompleteRef,
    runUiCouncilEntriesUpdaterRef,
    runUiStreamStatusUpdaterRef,
  });
  const selectedChatRunStatus = chats.selectedChat?.active_run_status || "";
  const composerLocked =
    streaming.selectedChatBusy || selectedChatRunStatus === "pause_requested";
  const scroll = useScrollManager({
    selectedChatId: chats.selectedChatId,
    messages: messages.messages,
    selectedChatBusy: streaming.selectedChatBusy,
    streamStatus: streaming.streamStatus,
  });

  projectsReloadedRef.current = (nextProjects) => {
    const nextProjectIds = new Set(nextProjects.map((project) => project.id));
    chats.setChatListsByProject((current) =>
      Object.fromEntries(Object.entries(current).filter(([projectId]) => nextProjectIds.has(projectId))),
    );
  };

  chatDeletedRef.current = (chatId) => {
    messages.clearMessagesForChat(chatId);
    streaming.clearRunUi(chatId);
  };

  useEffect(() => {
    if (auth.user) {
      return;
    }
    startTransition(() => {
      projects.setProjects([]);
      projects.setSelectedProjectId("");
      projects.setExpandedProjectId("");
      chats.setChats([]);
      chats.setChatListsByProject({});
      chats.setSelectedChatId("");
      messages.resetAll();
      streaming.resetAll();
      setError(auth.error || "");
      setStatus("idle");
    });
  }, [auth.error, auth.user]);

  useEffect(() => {
    const bootstrap = auth.bootstrap;
    if (!bootstrap) {
      return;
    }
    startTransition(() => {
      projects.setProjects(bootstrap.projects);
      chats.setChatListsByProject(bootstrap.chats_by_project);
      projects.setSelectedProjectId((current) =>
        bootstrap.projects.some((project) => project.id === current)
          ? current
          : bootstrap.projects[0]?.id || "",
      );
      chats.setSelectedChatId("");
      messages.resetAll();
      streaming.resetAll();
      setError(auth.error || "");
      setStatus("idle");
    });
  }, [auth.bootstrap, auth.error]);

  useEffect(() => {
    council.clearForChatSelection();
  }, [chats.selectedChatId]);

  useEffect(() => {
    if (!chats.selectedChatId || messages.messagesByChat[chats.selectedChatId]) {
      return;
    }

    void messages.reloadMessages(chats.selectedChatId).catch((err: Error) => {
      setError(err.message);
      setStatus("error");
    });
  }, [chats.selectedChatId, messages.messagesByChat]);

  useEffect(() => {
    council.syncLiveCouncilEntries(streaming.selectedRunUi?.councilEntries || [], streaming.selectedChatBusy);
  }, [council.viewingCouncilMessageId, streaming.selectedChatBusy, streaming.selectedRunUi?.councilEntries]);

  const displayedCouncilEntries =
    council.viewingCouncilMessageId !== null
      ? council.councilEntries
      : (streaming.selectedRunUi?.councilEntries || []);

  useEffect(() => {
    if (!council.councilStickToBottomRef.current) return;
    council.councilEndRef.current?.scrollIntoView({
      behavior: displayedCouncilEntries.some((entry) => !entry.complete) ? "auto" : "smooth",
      block: "end",
    });
  }, [council.councilEndRef, council.councilStickToBottomRef, displayedCouncilEntries]);

  const liveStatusKey = getStreamStatusKey(streaming.streamStatus);
  const liveStatusAliases = getStreamStatusAliases(streaming.streamStatus);
  const canPauseCouncilRun = selectedChatRunStatus === "running" && Boolean(streaming.selectedRunUi?.canPauseRun);

  useEffect(() => {
    setStatusAliasIndex(0);
    if (!streaming.selectedChatBusy || liveStatusAliases.length <= 1) {
      return;
    }

    const intervalId = window.setInterval(() => {
      setStatusAliasIndex((current) => (current + 1) % liveStatusAliases.length);
    }, 2400);

    return () => {
      window.clearInterval(intervalId);
    };
  }, [liveStatusAliases, liveStatusKey, streaming.selectedChatBusy]);

  useEffect(() => {
    function handleGlobalKeyDown(event: KeyboardEvent) {
      const target = event.target as Element;
      const inInput =
        target instanceof HTMLInputElement ||
        target instanceof HTMLTextAreaElement ||
        (target as HTMLElement).isContentEditable;

      if (event.key === "Tab" && !inInput && canPauseCouncilRun && council.councilActive) {
        event.preventDefault();
        void handlePauseCouncilRun();
        return;
      }

      if (event.key === "Escape" && !inInput && streaming.selectedChatBusy) {
        event.preventDefault();
        void streaming.handleCancelActiveRun();
      }
    }

    window.addEventListener("keydown", handleGlobalKeyDown);
    return () => window.removeEventListener("keydown", handleGlobalKeyDown);
  }, [canPauseCouncilRun, council.councilActive, streaming.selectedChatBusy]);

  function closeSidebarOnMobile() {
    if (typeof window !== "undefined" && window.innerWidth <= 960) {
      setSidebarCollapsed(true);
    }
  }

  function handleSelectProject(projectId: string, cachedChats?: ChatSession[]) {
    if (cachedChats) {
      chats.setChats(cachedChats);
      chats.setSelectedChatId((current) =>
        cachedChats.some((chat) => chat.id === current) ? current : cachedChats[0]?.id || "",
      );
    }

    projects.setSelectedProjectId(projectId);
    projects.setExpandedProjectId(projectId);
    closeSidebarOnMobile();
  }

  async function handleCreateChat() {
    if (!projects.selectedProjectId || chats.creatingChat) {
      return;
    }

    if (chats.selectedChat && messages.messages.length === 0) {
      closeSidebarOnMobile();
      return;
    }

    await chats.handleCreateChat("");
  }

  async function handleDeleteProject() {
    const deletingSelectedProject = projects.selectedProjectId === projects.editingProjectId;
    const deleted = await projects.handleDeleteProject();

    if (!deleted || !deletingSelectedProject) {
      return;
    }

    chats.setSelectedChatId("");
    messages.resetAll();
    streaming.resetAll();
  }

  async function handleSendMessage(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (composerLocked) {
      return;
    }

    // Paused council intervention path
    if (selectedChatRunStatus === "paused") {
      const content = messages.messageInput.trim();
      messages.setMessageInput("");
      if (content) {
        await handleResumeCouncilRunWithInput(content, "fact");
      } else {
        await handleResumeCouncilRun();
      }
      return;
    }

    if (!messages.messageInput.trim()) {
      return;
    }
    if (!projects.selectedProjectId) {
      setError("Create a project first.");
      return;
    }

    let activeChatId = chats.selectedChatId;
    if (!activeChatId) {
      const newChat = await chats.handleCreateChat("");
      if (!newChat) return;
      activeChatId = newChat.id;
    }

    setError("");
    const content = messages.messageInput;
    const clientRequestId = globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random()}`;

    try {
      messages.setMessageInput("");
      if (council.councilMode !== "off") {
        council.setCouncilActive(true);
        council.setCouncilPanelCollapsed(false);
        council.setCouncilEntries([]);
        council.setViewingCouncilMessageId(null);
        council.resetCouncilStickToBottom();
      }

      scroll.resetStickToBottom();
      requestAnimationFrame(() => {
        scroll.scrollMessagesToBottom("smooth");
      });

      const run = await api.createRun(activeChatId, content, {
        magi: council.councilMode,
        clientRequestId,
      });
      chats.updateChatRunStatus(activeChatId, run.id, run.status);
      await streaming.attachRunStream(activeChatId, run);
    } catch (err) {
      messages.setMessageInput(content);
      setError((err as Error).message);
    }
  }

  async function handlePauseCouncilRun() {
    await streaming.handlePauseActiveRun();
  }

  async function handleResumeCouncilRun() {
    const resumed = await streaming.handleResumeActiveRun("", undefined);
    if (resumed) {
      council.setCouncilInterventionInput("");
    }
  }

  async function handleResumeCouncilRunWithInput(
    inputText: string,
    inputKind?: "fact" | "correction" | "constraint" | "goal_clarification",
  ) {
    const resumed = await streaming.handleResumeActiveRun(inputText, inputKind);
    if (resumed) {
      council.setCouncilInterventionInput("");
    }
  }

  const appShellStyle = {
    "--sidebar-width": `${sidebarCollapsed ? 0 : sidebarWidth}px`,
  } as CSSProperties;
  let composerPlaceholder = "Reply...";
  if (!projects.selectedProject) {
    composerPlaceholder = "Create a project first.";
  } else if (selectedChatRunStatus === "paused") {
    composerPlaceholder = "Add context and press Enter to resume, or just press Enter to resume without input.";
  } else if (selectedChatRunStatus === "pause_requested") {
    composerPlaceholder = "Pausing the council...";
  } else if (messages.messages.length === 0) {
    composerPlaceholder = `Ask about ${projects.selectedProject.name}...`;
  }
  const liveStatusLabel = getStreamStatusLabel(streaming.streamStatus);
  const liveStatusSubtext = liveStatusAliases[statusAliasIndex] || liveStatusAliases[0] || liveStatusLabel;

  if (!auth.user) {
    return (
      <LoginScreen
        status={auth.loading ? "loading" : "idle"}
        error={auth.error || error}
        onSignIn={auth.signIn}
      />
    );
  }

  return (
    <>
      <div className="app-shell" style={appShellStyle}>
        <Sidebar
          user={auth.user}
          projects={projects.projects}
          chatListsByProject={chats.chatListsByProject}
          activeProjectChats={chats.chats}
          selectedProjectId={projects.selectedProjectId}
          selectedChatId={chats.selectedChatId}
          expandedProjectId={projects.expandedProjectId}
          sidebarCollapsed={sidebarCollapsed}
          sidebarWidth={sidebarWidth}
          isDebugMode={isDebugMode}
          debugPanelOpen={debugPanelOpen}
          creatingChat={chats.creatingChat}
          onCreateProject={projects.openCreateProjectDialog}
          onToggleDebugPanel={() => setDebugPanelOpen((current) => !current)}
          onCollapseSidebar={() => setSidebarCollapsed(true)}
          onExpandSidebar={() => setSidebarCollapsed(false)}
          onSidebarWidthChange={setSidebarWidth}
          onSelectProject={handleSelectProject}
          onToggleProjectExpansion={(projectId) =>
            projects.setExpandedProjectId((current) => (current === projectId ? "" : projectId))
          }
          onEditProject={projects.openEditProjectDialog}
          onCreateChat={handleCreateChat}
          onSelectChat={(chatId) => {
            chats.setSelectedChatId(chatId);
            closeSidebarOnMobile();
          }}
          onEditChat={chats.openEditChatDialog}
          onCloseMobileSidebar={closeSidebarOnMobile}
          onLogout={auth.logout}
        />

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
            {council.councilActive && council.councilPanelCollapsed && streaming.selectedChatBusy ? (
              <button
                type="button"
                className="council-reopen-pill"
                onClick={() => council.setCouncilPanelCollapsed(false)}
              >
                Council deliberating <span className="status-dot" aria-hidden="true" />
              </button>
            ) : null}
            <section className={`chat-stage${council.councilActive && !council.councilPanelCollapsed ? " council-open" : ""}`}>
              <CouncilPanel
                entries={displayedCouncilEntries}
                viewingPast={council.viewingCouncilMessageId !== null}
                runStatus={selectedChatRunStatus}
                selectedChatBusy={streaming.selectedChatBusy}
                canPauseRun={canPauseCouncilRun}
                onClose={() => council.setCouncilPanelCollapsed(true)}
                onResumeRun={handleResumeCouncilRun}
                onCouncilScroll={council.updateCouncilStickToBottom}
                councilFeedRef={council.councilFeedRef}
                councilEndRef={council.councilEndRef}
              />

              <ChatView
                messages={messages.messages}
                selectedProject={projects.selectedProject}
                selectedChat={chats.selectedChat}
                selectedChatId={chats.selectedChatId}
                selectedChatBusy={composerLocked}
                streamingAssistantId={streaming.streamingAssistantId}
                liveStatusLabel={liveStatusLabel}
                liveStatusSubtext={liveStatusSubtext}
                viewingCouncilMessageId={council.viewingCouncilMessageId}
                onViewCouncilEntries={council.handleViewCouncilEntries}
                messagesContainerRef={scroll.messagesContainerRef}
                messagesEndRef={scroll.messagesEndRef}
                onScroll={scroll.updateStickToBottom}
                onCreateProjectClick={projects.openCreateProjectDialog}
                onCreateChat={handleCreateChat}
                creatingChat={chats.creatingChat}
              />
            </section>
          </div>

          <MessageComposer
            error={error}
            councilMode={council.councilMode}
            selectedChatBusy={composerLocked}
            isPaused={selectedChatRunStatus === "paused"}
            selectedChatId={chats.selectedChatId}
            messageInput={messages.messageInput}
            placeholder={composerPlaceholder}
            onMessageChange={messages.setMessageInput}
            onSubmit={handleSendMessage}
            onCycleCouncilMode={council.cycleCouncilMode}
          />
        </main>

        {debugPanelOpen && isDebugMode ? (
          <DebugPanel chatId={chats.selectedChatId} onClose={() => setDebugPanelOpen(false)} />
        ) : null}
      </div>

      {projects.showCreateProjectDialog ? (
        <CreateProjectDialog
          projectName={projects.projectNameInput}
          projectDescription={projects.projectDescriptionInput}
          status={status}
          onProjectNameChange={projects.setProjectNameInput}
          onProjectDescriptionChange={projects.setProjectDescriptionInput}
          onSubmit={projects.handleCreateProject}
          onClose={projects.closeCreateProjectDialog}
        />
      ) : null}

      {projects.editingProjectId ? (
        <EditProjectDialog
          projectName={projects.editProjectNameInput}
          projectDescription={projects.editProjectDescriptionInput}
          error={error}
          status={status}
          onProjectNameChange={projects.setEditProjectNameInput}
          onProjectDescriptionChange={projects.setEditProjectDescriptionInput}
          onSubmit={projects.handleEditProject}
          onDelete={handleDeleteProject}
          onClose={projects.closeEditProjectDialog}
        />
      ) : null}

      {chats.editingChatId ? (
        <EditChatDialog
          chatTitle={chats.editChatTitleInput}
          error={error}
          status={status}
          onChatTitleChange={chats.setEditChatTitleInput}
          onSubmit={chats.handleEditChat}
          onDelete={chats.handleDeleteChat}
          onClose={chats.closeEditChatDialog}
        />
      ) : null}
    </>
  );
}
