import { useEffect, useRef, useState } from "react";
import type { ChatSession, Project, User } from "../types";
import { formatChatTimestamp } from "../utils";

type SidebarProps = {
  user: User;
  projects: Project[];
  chatListsByProject: Record<string, ChatSession[]>;
  activeProjectChats: ChatSession[];
  selectedProjectId: string;
  selectedChatId: string;
  expandedProjectId: string;
  sidebarCollapsed: boolean;
  sidebarWidth: number;
  isDebugMode: boolean;
  debugPanelOpen: boolean;
  creatingChat: boolean;
  onCreateProject: () => void;
  onToggleDebugPanel: () => void;
  onCollapseSidebar: () => void;
  onExpandSidebar: () => void;
  onSidebarWidthChange: (width: number) => void;
  onSelectProject: (projectId: string, cachedChats?: ChatSession[]) => void;
  onToggleProjectExpansion: (projectId: string) => void;
  onEditProject: (project: Project) => void;
  onCreateChat: () => void;
  onSelectChat: (chatId: string) => void;
  onEditChat: (chat: ChatSession) => void;
  onCloseMobileSidebar: () => void;
  onLogout: () => void | Promise<void>;
};

export function Sidebar({
  user,
  projects,
  chatListsByProject,
  activeProjectChats,
  selectedProjectId,
  selectedChatId,
  expandedProjectId,
  sidebarCollapsed,
  sidebarWidth,
  isDebugMode,
  debugPanelOpen,
  creatingChat,
  onCreateProject,
  onToggleDebugPanel,
  onCollapseSidebar,
  onExpandSidebar,
  onSidebarWidthChange,
  onSelectProject,
  onToggleProjectExpansion,
  onEditProject,
  onCreateChat,
  onSelectChat,
  onEditChat,
  onCloseMobileSidebar,
  onLogout,
}: SidebarProps) {
  const dragStateRef = useRef<{ active: boolean; width: number }>({ active: false, width: sidebarWidth });
  const previousTitlesRef = useRef<Record<string, string>>({});
  const titleAnimationTimersRef = useRef<Record<string, number>>({});
  const [animatedTitles, setAnimatedTitles] = useState<Record<string, string>>({});

  useEffect(
    () => () => {
      Object.values(titleAnimationTimersRef.current).forEach((timerId) => {
        window.clearInterval(timerId);
      });
      titleAnimationTimersRef.current = {};
    },
    [],
  );

  useEffect(() => {
    const currentTitles: Record<string, string> = {};
    Object.values(chatListsByProject).forEach((items) => {
      items.forEach((chat) => {
        currentTitles[chat.id] = (chat.title || "").trim();
      });
    });
    activeProjectChats.forEach((chat) => {
      currentTitles[chat.id] = (chat.title || "").trim();
    });

    Object.entries(currentTitles).forEach(([chatId, nextTitle]) => {
      const previousTitle = (previousTitlesRef.current[chatId] || "").trim();
      if (previousTitle || !nextTitle || animatedTitles[chatId] === nextTitle) {
        return;
      }

      window.clearInterval(titleAnimationTimersRef.current[chatId]);
      let visibleChars = 0;
      setAnimatedTitles((current) => ({ ...current, [chatId]: "" }));
      titleAnimationTimersRef.current[chatId] = window.setInterval(() => {
        visibleChars += 1;
        const nextSlice = nextTitle.slice(0, visibleChars);
        setAnimatedTitles((current) => ({ ...current, [chatId]: nextSlice }));
        if (visibleChars >= nextTitle.length) {
          window.clearInterval(titleAnimationTimersRef.current[chatId]);
          delete titleAnimationTimersRef.current[chatId];
          window.setTimeout(() => {
            setAnimatedTitles((current) => {
              if (current[chatId] !== nextTitle) {
                return current;
              }
              const next = { ...current };
              delete next[chatId];
              return next;
            });
          }, 250);
        }
      }, 18);
    });

    previousTitlesRef.current = currentTitles;
  }, [activeProjectChats, animatedTitles, chatListsByProject]);

  useEffect(() => {
    dragStateRef.current.width = sidebarWidth;
  }, [sidebarWidth]);

  useEffect(
    () => () => {
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
    },
    [],
  );

  useEffect(() => {
    function onPointerMove(event: PointerEvent) {
      if (!dragStateRef.current.active || sidebarCollapsed) {
        return;
      }
      const nextWidth = Math.min(360, Math.max(220, event.clientX));
      dragStateRef.current.width = nextWidth;
      onSidebarWidthChange(nextWidth);
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
  }, [onSidebarWidthChange, sidebarCollapsed]);

  function startSidebarResize() {
    if (sidebarCollapsed) {
      return;
    }
    dragStateRef.current.active = true;
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
  }

  return (
    <>
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
                <div className="user-chip" title={user.display_name || user.username || user.email}>
                  <div className="user-meta">
                    <span className="eyebrow">Signed in</span>
                    <strong className="user-name">{user.display_name || user.username || user.email}</strong>
                    {user.email ? <small>{user.email}</small> : null}
                  </div>
                  <button type="button" className="subtle-action" onClick={() => void onLogout()}>
                    Sign out
                  </button>
                </div>
              </section>

              <section className="rail-section">
                <div className="rail-section-header">
                  <div>
                    <p className="eyebrow">Projects</p>
                    <h2>{projects.length} workspaces</h2>
                  </div>
                  <button type="button" className="subtle-action" onClick={onCreateProject} aria-label="Create project">
                    + New
                  </button>
                </div>

                <div className="project-tree">
                  {projects.map((project) => {
                    const active = project.id === selectedProjectId;
                    const expanded = project.id === expandedProjectId;
                    const projectChats = chatListsByProject[project.id] ?? (active ? activeProjectChats : []);

                    return (
                      <div key={project.id} className={`project-tree-item ${active ? "active" : ""}`}>
                        <div className="project-row">
                          <button
                            className={`rail-item project-item ${active ? "active" : ""}`}
                            title={project.name}
                            onClick={() => {
                              if (active) {
                                onToggleProjectExpansion(project.id);
                                return;
                              }

                              onSelectProject(project.id, projectChats);
                              onCloseMobileSidebar();
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
                              onEditProject(project);
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
                              <button type="button" className="subtle-action" onClick={onCreateChat} disabled={creatingChat}>
                                + New
                              </button>
                            </div>

                            {projectChats.length > 0 ? (
                              <div className="nested-chat-list">
                                {projectChats.map((chat) => (
                                  <div key={chat.id} className={`nested-chat-row ${chat.id === selectedChatId ? "active" : ""}`}>
                                    {(() => {
                                      const visibleTitle = animatedTitles[chat.id] || chat.title || "Untitled chat";
                                      const fullTitle = chat.title || "Untitled chat";
                                      return (
                                    <button
                                      className={`nested-chat-item ${chat.id === selectedChatId ? "active" : ""}`}
                                      title={fullTitle}
                                      onClick={() => {
                                        onSelectChat(chat.id);
                                        onCloseMobileSidebar();
                                      }}
                                    >
                                      <span className="nested-chat-copy">
                                        <strong>{visibleTitle}</strong>
                                        <small>{formatChatTimestamp(chat.updated_at || chat.created_at)}</small>
                                      </span>
                                    </button>
                                      );
                                    })()}
                                    <button
                                      type="button"
                                      className="chat-edit-trigger"
                                      aria-label={`Edit ${chat.title || "chat"}`}
                                      onClick={(event) => {
                                        event.stopPropagation();
                                        onEditChat(chat);
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
                                <button type="button" className="ghost-button compact" onClick={onCreateChat} disabled={creatingChat}>
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
                    onClick={onToggleDebugPanel}
                  >
                    [DBG]
                  </button>
                ) : null}
                <button type="button" className="collapse-button" onClick={onCollapseSidebar} aria-label="Collapse sidebar">
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
          onClick={onCollapseSidebar}
        />
      ) : null}

      <div
        className={`sidebar-resize-handle ${sidebarCollapsed ? "disabled" : ""}`}
        onPointerDown={startSidebarResize}
        onClick={() => {
          if (sidebarCollapsed) {
            onExpandSidebar();
          }
        }}
        role="button"
        aria-label={sidebarCollapsed ? "Expand sidebar" : "Resize sidebar"}
      >
        {sidebarCollapsed ? <span className="sidebar-expand-glyph">»</span> : null}
      </div>
    </>
  );
}
