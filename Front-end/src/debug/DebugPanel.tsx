import { useEffect, useState } from "react";
import { isActiveRunStatus } from "./debugUtils";
import "./debug.css";
import { RunInspector } from "./RunInspector";
import { RunList } from "./RunList";
import { useRunHistory } from "./useRunHistory";

type DebugPanelProps = {
  chatId: string;
  onClose: () => void;
};

export function DebugPanel({ chatId, onClose }: DebugPanelProps) {
  const { runs, total, hasMore, loading, loadingMore, error, loadMore, reload } = useRunHistory(chatId);
  const [selectedRunId, setSelectedRunId] = useState("");

  useEffect(() => {
    setSelectedRunId("");
  }, [chatId]);

  useEffect(() => {
    if (!runs.length) {
      setSelectedRunId("");
      return;
    }
    const runIds = new Set(runs.map((run) => run.id));
    if (selectedRunId && runIds.has(selectedRunId)) {
      return;
    }
    const activeRun = runs.find((run) => isActiveRunStatus(run.status));
    setSelectedRunId(activeRun?.id || runs[0].id);
  }, [runs, selectedRunId]);

  return (
    <>
      <button type="button" className="debug-drawer-backdrop" aria-label="Close debug panel" onClick={onClose} />
      <aside className="debug-panel" aria-label="Debug panel">
        <div className="debug-panel-header">
          <div>
            <p className="eyebrow">Debug</p>
            <h2>Run inspector</h2>
          </div>
          <button type="button" className="debug-panel-close" aria-label="Close debug panel" onClick={onClose}>
            <svg viewBox="0 0 20 20" aria-hidden="true" width="14" height="14">
              <path d="M5 5l10 10M15 5L5 15" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
            </svg>
            <span>Close</span>
          </button>
        </div>

        {!chatId ? (
          <div className="debug-panel-empty">Select a chat to inspect its runs.</div>
        ) : (
          <div className="debug-panel-body">
            <div className="debug-panel-column debug-panel-history">
              <RunList
                runs={runs}
                total={total}
                selectedRunId={selectedRunId}
                loading={loading}
                loadingMore={loadingMore}
                error={error}
                hasMore={hasMore}
                onLoadMore={loadMore}
                onReload={reload}
                onSelect={setSelectedRunId}
              />
            </div>
            <div className="debug-panel-column debug-panel-inspector">
              <RunInspector runId={selectedRunId} onHistoryRefresh={reload} />
            </div>
          </div>
        )}
      </aside>
    </>
  );
}
