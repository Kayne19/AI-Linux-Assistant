import { useEffect, useState } from "react";
import type { ChatRun } from "../types";
import { ACTIVE_RUN_STATUSES, formatRelativeStart, formatTimestamp, isActiveRunStatus, toneForStatus, truncateMiddle } from "./debugUtils";

type RunListProps = {
  runs: ChatRun[];
  total: number;
  selectedRunId: string;
  loading: boolean;
  loadingMore: boolean;
  error: string;
  hasMore: boolean;
  onLoadMore: () => void;
  onReload: () => void;
  onSelect: (runId: string) => void;
};

function RunCard({
  run,
  selected,
  active,
  nowMs,
  onSelect,
}: {
  run: ChatRun;
  selected: boolean;
  active: boolean;
  nowMs: number;
  onSelect: (runId: string) => void;
}) {
  return (
    <button
      type="button"
      className={`debug-run-card${selected ? " selected" : ""}${active ? " active" : ""}`}
      onClick={() => onSelect(run.id)}
    >
      <div className="debug-run-card-top">
        <span className={`debug-badge tone-${toneForStatus(run.status)}`}>{run.status}</span>
        {run.latest_state_code ? <span className="debug-badge subtle">{run.latest_state_code}</span> : null}
      </div>
      <strong className="debug-run-card-title">{truncateMiddle(run.id, 6, 4)}</strong>
      <p className="debug-run-card-copy">{run.request_content || "No request content."}</p>
      <div className="debug-run-card-meta">
        <span>{active ? formatRelativeStart(run.created_at, nowMs) : formatTimestamp(run.created_at)}</span>
        {run.worker_id ? <span>{truncateMiddle(run.worker_id, 6, 4)}</span> : null}
      </div>
    </button>
  );
}

export function RunList({
  runs,
  total,
  selectedRunId,
  loading,
  loadingMore,
  error,
  hasMore,
  onLoadMore,
  onReload,
  onSelect,
}: RunListProps) {
  const [nowMs, setNowMs] = useState(() => Date.now());
  const activeRun = runs.find((run) => ACTIVE_RUN_STATUSES.has(run.status)) || null;
  const historicalRuns = runs.filter((run) => !isActiveRunStatus(run.status));

  useEffect(() => {
    if (!activeRun) {
      return;
    }
    const intervalId = window.setInterval(() => {
      setNowMs(Date.now());
    }, 1000);
    return () => {
      window.clearInterval(intervalId);
    };
  }, [activeRun]);

  return (
    <div className="debug-run-list">
      <div className="debug-run-list-header">
        <div>
          <span className="eyebrow">Runs</span>
          <strong>{total} captured</strong>
        </div>
        <button type="button" className="ghost-button compact" onClick={onReload}>
          Refresh
        </button>
      </div>

      {loading ? <div className="debug-empty-state">Loading run history…</div> : null}
      {error ? <div className="debug-inline-error">{error}</div> : null}

      {!loading && activeRun ? (
        <div className="debug-run-section">
          <div className="debug-run-section-label">Active</div>
          <RunCard
            run={activeRun}
            selected={activeRun.id === selectedRunId}
            active
            nowMs={nowMs}
            onSelect={onSelect}
          />
        </div>
      ) : null}

      {!loading && historicalRuns.length > 0 ? (
        <div className="debug-run-section">
          <div className="debug-run-section-label">History</div>
          <div className="debug-run-history">
            {historicalRuns.map((run) => (
              <RunCard
                key={run.id}
                run={run}
                selected={run.id === selectedRunId}
                active={false}
                nowMs={nowMs}
                onSelect={onSelect}
              />
            ))}
          </div>
        </div>
      ) : null}

      {!loading && runs.length === 0 ? <div className="debug-empty-state">No runs recorded for this chat.</div> : null}

      {hasMore ? (
        <button type="button" className="ghost-button compact debug-load-more" onClick={onLoadMore} disabled={loadingMore}>
          {loadingMore ? "Loading…" : "Load more"}
        </button>
      ) : null}
    </div>
  );
}
