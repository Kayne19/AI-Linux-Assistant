import type { RefObject } from "react";
import type { UICouncilEntry } from "../types";
import { formatCouncilPhase } from "../utils";

type CouncilPanelProps = {
  entries: UICouncilEntry[];
  viewingPast: boolean;
  onClose: () => void;
  councilFeedRef: RefObject<HTMLDivElement>;
  councilEndRef: RefObject<HTMLDivElement>;
};

export function CouncilPanel({
  entries,
  viewingPast,
  onClose,
  councilFeedRef,
  councilEndRef,
}: CouncilPanelProps) {
  return (
    <section className="council-panel">
      <div className="council-panel-header">
        <span className="eyebrow">Council</span>
        <span className="council-panel-label">{viewingPast ? "Past deliberation" : "Agents deliberating"}</span>
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
    </section>
  );
}
