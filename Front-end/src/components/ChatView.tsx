import type { RefObject, UIEventHandler } from "react";
import { renderMessageContent } from "../renderMessage";
import type { ChatMessage, ChatSession, Project } from "../types";
import { formatMessageTimestamp } from "../utils";

type ChatViewProps = {
  messages: ChatMessage[];
  selectedProject: Project | null;
  selectedChat: ChatSession | null;
  selectedChatId: string;
  selectedChatBusy: boolean;
  streamingAssistantId: number | null;
  liveStatusLabel: string;
  liveStatusSubtext: string;
  viewingCouncilMessageId: number | null;
  onViewCouncilEntries: (message: ChatMessage) => void;
  messagesContainerRef: RefObject<HTMLDivElement>;
  messagesEndRef: RefObject<HTMLDivElement>;
  onScroll: UIEventHandler<HTMLDivElement>;
  onCreateProjectClick: () => void;
  onCreateChat: () => void;
  creatingChat: boolean;
};

export function ChatView({
  messages,
  selectedProject,
  selectedChat,
  selectedChatId,
  selectedChatBusy,
  streamingAssistantId,
  liveStatusLabel,
  liveStatusSubtext,
  viewingCouncilMessageId,
  onViewCouncilEntries,
  messagesContainerRef,
  messagesEndRef,
  onScroll,
  onCreateProjectClick,
  onCreateChat,
  creatingChat,
}: ChatViewProps) {
  return (
    <section className="chat-card">
      <div ref={messagesContainerRef} className="messages" onScroll={onScroll}>
        {!selectedProject ? (
          <div className="empty-state">
            <p className="eyebrow">No project selected</p>
            <h3>Create a project to start organizing chats.</h3>
            <p>Projects are the container. Each project keeps its own set of chats and context.</p>
            <button type="button" className="empty-state-action" onClick={onCreateProjectClick}>
              Create project
            </button>
          </div>
        ) : !selectedChatId ? (
          <div className="empty-state">
            <p className="eyebrow">No chat selected</p>
            <h3>Open a chat inside {selectedProject.name}.</h3>
            <p>This project is active, but you still need a chat thread before you can start asking questions.</p>
            <button type="button" className="empty-state-action" onClick={onCreateChat} disabled={creatingChat}>
              Create chat
            </button>
          </div>
        ) : messages.length === 0 ? (
          <div className="empty-state">
            <p className="eyebrow">Ready</p>
            <h3>{selectedChat?.title || "Start a conversation tied to this project."}</h3>
            <p>
              The assistant keeps troubleshooting context scoped to {selectedProject.name}, so this thread can build on
              prior work.
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
                      <div className="message-content">{renderMessageContent(message.content)}</div>
                    ) : (
                      <div className="message-content live-status-content" />
                    )}
                  </>
                ) : (
                  <>
                    <div className="message-content">
                      {message.role === "user" ? <p>{message.content}</p> : renderMessageContent(message.content)}
                    </div>
                    <button
                      type="button"
                      className="message-copy-btn"
                      title="Copy message"
                      onClick={() => void navigator.clipboard.writeText(message.content)}
                    >
                      <svg viewBox="0 0 20 20" aria-hidden="true" width="12" height="12" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                        <rect x="7" y="3" width="10" height="13" rx="1.5" />
                        <path d="M3 6.5A1.5 1.5 0 0 1 4.5 5H7" />
                        <path d="M3 6.5v9A1.5 1.5 0 0 0 4.5 17H13" />
                      </svg>
                      Copy
                    </button>
                    {message.role !== "user" && message.council_entries?.length ? (
                      <button
                        type="button"
                        className={`council-replay-btn${viewingCouncilMessageId === message.id ? " active" : ""}`}
                        onClick={() => onViewCouncilEntries(message)}
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
  );
}
