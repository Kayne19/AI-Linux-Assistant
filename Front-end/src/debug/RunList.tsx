import type { ChatRun } from "../types";
import {
  formatDuration,
  formatTimestamp,
  isActiveRunStatus,
  parseTimestamp,
  truncateId,
} from "./debugUtils";

type RunListProps = {
  activeRun: ChatRun | null;
  historicalRuns: ChatRun[];
  selectedRunId: string;
  loading: boolean;
  loadingMore: boolean;
  error: string;
  hasMore: boolean;
  onSelect: (runId: string) => void;
  onLoadMore: () => void;
  onRefresh: () => void;
};

function RunCard({
  run,
  selected,
  active,
  onSelect,
}: {
  run: ChatRun;
  selected: boolean;
  active: boolean;
  onSelect: () => void;
}) {
  const createdMs = parseTimestamp(run.created_at);
  const elapsedMs = createdMs !== null ? Date.now() - createdMs : null;

  return (
    <button
      type="button"
      className={`debug-run-card${selected ? " selected" : ""}${active ? " active" : ""}`}
      onClick={onSelect}
    >
      <div className="debug-run-card-top">
        <span className={`debug-badge status status-${run.status}`}>{run.status}</span>
        {run.latest_state_code ? <span className="debug-badge state">{run.latest_state_code}</span> : null}
      </div>
      <div className="debug-run-card-title">
        <strong>{truncateId(run.id, 6)}</strong>
        {active ? <span className="debug-run-card-pin">active</span> : null}
      </div>
      <p className="debug-run-card-request">{run.request_content || "No request content"}</p>
      <div className="debug-run-card-meta">
        <span>{formatTimestamp(run.created_at)}</span>
        <span>{active && isActiveRunStatus(run.status) ? formatDuration(elapsedMs) : run.worker_id ? truncateId(run.worker_id, 5) : "idle"}</span>
      </div>
    </button>
  );
}

export function RunList({
  activeRun,
  historicalRuns,
  selectedRunId,
  loading,
  loadingMore,
  error,
  hasMore,
  onSelect,
  onLoadMore,
  onRefresh,
}: RunListProps) {
  return (
    <section className="debug-run-list">
      <div className="debug-section-header">
        <div>
          <p className="eyebrow">Run History</p>
          <h3>Selected chat</h3>
        </div>
        <button type="button" className="debug-link-button" onClick={onRefresh}>
          Refresh
        </button>
      </div>

      {loading ? <p className="debug-list-note">Loading runs…</p> : null}
      {error ? <p className="debug-list-error">{error}</p> : null}

      {activeRun ? (
        <div className="debug-run-group">
          <p className="debug-group-label">Active</p>
          <RunCard
            run={activeRun}
            selected={selectedRunId === activeRun.id}
            active
            onSelect={() => onSelect(activeRun.id)}
          />
        </div>
      ) : null}

      <div className="debug-run-group">
        <p className="debug-group-label">History</p>
        {historicalRuns.length > 0 ? (
          <div className="debug-run-list-scroll">
            {historicalRuns.map((run) => (
              <RunCard
                key={run.id}
                run={run}
                selected={selectedRunId === run.id}
                active={false}
                onSelect={() => onSelect(run.id)}
              />
            ))}
          </div>
        ) : (
          <p className="debug-list-note">No historical runs for this chat yet.</p>
        )}
      </div>

      {hasMore ? (
        <button type="button" className="debug-load-more" onClick={onLoadMore} disabled={loadingMore}>
          {loadingMore ? "Loading…" : "Load older runs"}
        </button>
      ) : null}
    </section>
  );
}
