import type { ChatRun } from "../types";
import { formatAbsoluteTime, formatDuration, isActiveRunStatus, truncateMiddle } from "./debugUtils";

type RunListProps = {
  activeRun: ChatRun | null;
  historicalRuns: ChatRun[];
  selectedRunId: string;
  total: number;
  page: number;
  hasMore: boolean;
  loading: boolean;
  error: string;
  onPageChange: (page: number) => void;
  onSelect: (runId: string) => void;
};

function RunCard({
  label,
  run,
  selected,
  onSelect,
}: {
  label: string;
  run: ChatRun;
  selected: boolean;
  onSelect: (runId: string) => void;
}) {
  const createdMs = Date.parse(run.created_at);
  const elapsedMs = Number.isNaN(createdMs) ? null : Date.now() - createdMs;

  return (
    <button
      type="button"
      className={`debug-run-card${selected ? " selected" : ""}`}
      onClick={() => onSelect(run.id)}
    >
      <div className="debug-run-card-top">
        <span className="debug-run-card-label">{label}</span>
        <span className={`debug-status-badge status-${run.status}`}>{run.status}</span>
      </div>
      <p className="debug-run-request">{run.request_content || "No request content"}</p>
      <div className="debug-run-card-meta">
        <span>{run.latest_state_code || "no-state"}</span>
        <span>{formatDuration(elapsedMs, { active: isActiveRunStatus(run.status) })}</span>
      </div>
      <div className="debug-run-card-meta">
        <span>{formatAbsoluteTime(run.created_at)}</span>
        <span>{run.worker_id ? truncateMiddle(run.worker_id) : "unclaimed"}</span>
      </div>
    </button>
  );
}

export function RunList({
  activeRun,
  historicalRuns,
  selectedRunId,
  total,
  page,
  hasMore,
  loading,
  error,
  onPageChange,
  onSelect,
}: RunListProps) {
  return (
    <section className="debug-panel-list">
      <div className="debug-panel-section-head">
        <div>
          <p className="eyebrow">Run History</p>
          <h3>{total} runs</h3>
        </div>
      </div>

      {error ? <p className="debug-empty-state">{error}</p> : null}

      {activeRun ? <RunCard label="Active" run={activeRun} selected={selectedRunId === activeRun.id} onSelect={onSelect} /> : null}

      <div className="debug-history-section">
        <div className="debug-history-head">
          <span className="eyebrow">History</span>
          <span className="debug-panel-subtle">Newest first</span>
        </div>
        {historicalRuns.length === 0 && !loading ? (
          <p className="debug-empty-state">No historical runs for this chat yet.</p>
        ) : (
          historicalRuns.map((run) => (
            <RunCard
              key={run.id}
              label="Run"
              run={run}
              selected={selectedRunId === run.id}
              onSelect={onSelect}
            />
          ))
        )}
      </div>

      <div className="debug-pagination">
        <button type="button" className="ghost-button compact" onClick={() => onPageChange(page - 1)} disabled={page <= 1 || loading}>
          Newer
        </button>
        <span className="debug-panel-subtle">Page {page}</span>
        <button type="button" className="ghost-button compact" onClick={() => onPageChange(page + 1)} disabled={!hasMore || loading}>
          Older
        </button>
      </div>
    </section>
  );
}
