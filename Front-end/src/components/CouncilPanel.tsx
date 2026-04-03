import type { RefObject } from "react";
import { renderMessageContent } from "../renderMessage";
import type { UICouncilEntry } from "../types";
import { formatCouncilPhase } from "../utils";

type CouncilPanelProps = {
  entries: UICouncilEntry[];
  viewingPast: boolean;
  runStatus: string;
  selectedChatBusy: boolean;
  canPauseRun: boolean;
  onClose: () => void;
  onResumeRun: () => void | Promise<void>;
  onCouncilScroll: () => void;
  councilFeedRef: RefObject<HTMLDivElement>;
  councilEndRef: RefObject<HTMLDivElement>;
};

export function CouncilPanel({
  entries,
  viewingPast,
  runStatus,
  selectedChatBusy,
  canPauseRun,
  onClose,
  onResumeRun,
  onCouncilScroll,
  councilFeedRef,
  councilEndRef,
}: CouncilPanelProps) {
  const paused = runStatus === "paused";
  const pauseRequested = runStatus === "pause_requested";

  return (
    <section className="council-panel">
      <div className="council-panel-header">
        <span className={`council-run-status${paused ? " paused" : pauseRequested ? " pause-requested" : ""}`}>
          {viewingPast ? "Past deliberation" : paused ? "Paused" : pauseRequested ? "Pausing" : selectedChatBusy ? "Live" : "Idle"}
        </span>
        <button
          type="button"
          className="council-copy-all-btn"
          title="Copy all council entries"
          onClick={() => {
            const text = entries.map((e) => `[${e.role}] ${e.text}`).join("\n\n");
            void navigator.clipboard.writeText(text);
          }}
        >
          <svg viewBox="0 0 20 20" aria-hidden="true" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
            <rect x="7" y="3" width="10" height="13" rx="1.5" />
            <path d="M3 6.5A1.5 1.5 0 0 1 4.5 5H7" />
            <path d="M3 6.5v9A1.5 1.5 0 0 0 4.5 17H13" />
          </svg>
        </button>
        <button
          type="button"
          className="council-panel-close"
          aria-label="Close council panel"
          onClick={onClose}
        >
          <svg viewBox="0 0 20 20" aria-hidden="true" width="14" height="14">
            <path d="M5 5l10 10M15 5L5 15" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
          </svg>
        </button>
      </div>
      <div className="council-feed" ref={councilFeedRef} onScroll={onCouncilScroll}>
        {entries.map((entry) => (
          <div
            key={entry.entryId}
            className={`council-entry council-role-${entry.role}${entry.entryKind === "user_intervention" ? " council-entry-intervention" : ""}${entry.complete ? "" : " pending"}`}
          >
            <div className="council-entry-header">
              <span className={`role-badge role-${entry.entryKind === "user_intervention" ? "user" : entry.role}`}>
                {entry.entryKind === "user_intervention" ? "You" : entry.role}
              </span>
              <span className="council-entry-phase">
                {entry.entryKind === "user_intervention" ? "User input" : formatCouncilPhase(entry.phase, entry.round)}
              </span>
              {entry.inputKind ? <span className="council-entry-input-kind">{entry.inputKind}</span> : null}
            </div>
            {entry.complete ? (
              <div className="council-entry-text">{renderMessageContent(entry.text)}</div>
            ) : entry.streamBuffer ? (
              <p className="council-entry-text streaming">
                {entry.streamPreview || entry.streamBuffer || "…"}
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
        {paused && !viewingPast ? (
          <div className="council-paused-indicator">
            <span className="status-dot status-dot-paused" aria-hidden="true" />
            <span>Council paused — press Enter in the composer to resume</span>
          </div>
        ) : null}
        <div ref={councilEndRef} />
      </div>
    </section>
  );
}
