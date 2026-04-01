import { useEffect, useState } from "react";

import type { ChatRun } from "../types";
import { RunInspector } from "./RunInspector";
import { RunList } from "./RunList";
import { useRunHistory } from "./useRunHistory";
import "./debug.css";

type DebugPanelProps = {
  chatId: string;
  onClose: () => void;
};

export function DebugPanel({ chatId, onClose }: DebugPanelProps) {
  const {
    runs,
    activeRun,
    historicalRuns,
    hasMore,
    loading,
    loadingMore,
    error,
    loadMore,
    refresh,
  } = useRunHistory(chatId);
  const [selectedRunId, setSelectedRunId] = useState("");

  useEffect(() => {
    setSelectedRunId("");
  }, [chatId]);

  useEffect(() => {
    if (!runs.length) {
      setSelectedRunId("");
      return;
    }
    if (selectedRunId && runs.some((run) => run.id === selectedRunId)) {
      return;
    }
    setSelectedRunId(activeRun?.id || historicalRuns[0]?.id || "");
  }, [activeRun?.id, historicalRuns, runs, selectedRunId]);

  const selectedRun = runs.find((run) => run.id === selectedRunId) || null;

  return (
    <>
      <button type="button" className="debug-panel-backdrop" aria-label="Close debug panel" onClick={onClose} />
      <aside className="debug-panel" aria-label="Debug panel">
        <div className="debug-panel-header">
          <div>
            <p className="eyebrow">Dev / Admin</p>
            <h2>Run Debug Panel</h2>
          </div>
          <button type="button" className="debug-panel-close" onClick={onClose}>
            Close
          </button>
        </div>

        {!chatId ? (
          <div className="debug-panel-empty">
            <p>Select a chat to inspect its runs.</p>
          </div>
        ) : (
          <div className="debug-panel-grid">
            <RunList
              activeRun={activeRun}
              historicalRuns={historicalRuns}
              selectedRunId={selectedRunId}
              loading={loading}
              loadingMore={loadingMore}
              error={error}
              hasMore={hasMore}
              onSelect={setSelectedRunId}
              onLoadMore={loadMore}
              onRefresh={() => void refresh()}
            />
            <RunInspector
              runId={selectedRunId}
              initialRun={selectedRun as ChatRun | null}
              onRunMutated={() => void refresh()}
              onRunTerminal={() => void refresh()}
            />
          </div>
        )}
      </aside>
    </>
  );
}
