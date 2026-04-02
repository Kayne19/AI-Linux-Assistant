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
  interventionInput: string;
  onInterventionChange: (value: string) => void;
  onClose: () => void;
  onPauseRun: () => void | Promise<void>;
  onResumeRun: () => void | Promise<void>;
  onResumeRunWithInput: (
    inputText: string,
    inputKind?: "fact" | "correction" | "constraint" | "goal_clarification",
  ) => void | Promise<void>;
  onCancelRun: () => void | Promise<void>;
  councilFeedRef: RefObject<HTMLDivElement>;
  councilEndRef: RefObject<HTMLDivElement>;
};

export function CouncilPanel({
  entries,
  viewingPast,
  runStatus,
  selectedChatBusy,
  canPauseRun,
  interventionInput,
  onInterventionChange,
  onClose,
  onPauseRun,
  onResumeRun,
  onResumeRunWithInput,
  onCancelRun,
  councilFeedRef,
  councilEndRef,
}: CouncilPanelProps) {
  const paused = runStatus === "paused";
  const pauseRequested = runStatus === "pause_requested";
  const canPause = canPauseRun && !paused && !pauseRequested && !viewingPast;
  const canResume = paused && !viewingPast;
  const trimmedInput = interventionInput.trim();

  return (
    <section className="council-panel">
      <div className="council-panel-header">
        <span className="eyebrow">Council</span>
        <span className="council-panel-label">{viewingPast ? "Past deliberation" : "Agents deliberating"}</span>
        {!viewingPast ? (
          <span className={`council-run-status${paused ? " paused" : pauseRequested ? " pause-requested" : ""}`}>
            {paused ? "Paused" : pauseRequested ? "Pausing" : selectedChatBusy ? "Live" : "Idle"}
          </span>
        ) : null}
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
      <div className="council-feed" ref={councilFeedRef}>
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
        <div ref={councilEndRef} />
      </div>
      {!viewingPast ? (
        <div className="council-panel-footer">
          {canPause ? (
            <div className="council-intervention-actions single-row">
              <button type="button" className="council-action-btn primary" onClick={onPauseRun}>
                Pause
              </button>
              <button type="button" className="council-action-btn" onClick={onCancelRun}>
                Cancel
              </button>
            </div>
          ) : null}

          {canResume ? (
            <div className="council-intervention-composer">
              <textarea
                value={interventionInput}
                onChange={(event) => onInterventionChange(event.target.value)}
                placeholder="Add a fact, correction, constraint, or goal clarification before resuming."
                rows={3}
              />
              <div className="council-intervention-note">
                This input is rendered inside the council transcript, not as a chat message.
              </div>
              <div className="council-intervention-actions">
                <button
                  type="button"
                  className="council-action-btn primary"
                  onClick={onResumeRun}
                >
                  Resume
                </button>
                <button
                  type="button"
                  className="council-action-btn primary subtle"
                  onClick={() => onResumeRunWithInput(trimmedInput, "fact")}
                  disabled={!trimmedInput}
                >
                  Resume with input
                </button>
                <button type="button" className="council-action-btn" onClick={onCancelRun}>
                  Cancel
                </button>
              </div>
            </div>
          ) : pauseRequested ? (
            <div className="council-intervention-actions single-row">
              <div className="council-intervention-note">
                Pause requested. Waiting for the run to reach a safe checkpoint.
              </div>
              <button type="button" className="council-action-btn" onClick={onCancelRun}>
                Cancel
              </button>
            </div>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
