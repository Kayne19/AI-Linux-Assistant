import { useEffect, useMemo, useState } from "react";

import type { ChatRun, RunEvent } from "../types";
import { EventTimeline } from "./EventTimeline";
import {
  DEBUG_TABS,
  copyText,
  computeTimings,
  formatDuration,
  formatTimestamp,
  getLatencyTone,
  getLeaseRemainingMs,
  isActiveRunStatus,
  truncateId,
  type DebugTab,
} from "./debugUtils";

type RunInspectorProps = {
  run: ChatRun | null;
  events: RunEvent[];
  eventsLoading: boolean;
  eventsError: string;
  snapshotLoading: boolean;
  snapshotError: string;
  totalRuns: number;
  onCancel: () => void;
};

function getLeaseTone(leaseRemainingMs: number | null) {
  if (leaseRemainingMs === null) {
    return "neutral";
  }
  if (leaseRemainingMs > 30_000) {
    return "good";
  }
  if (leaseRemainingMs > 10_000) {
    return "warn";
  }
  return "bad";
}

function renderTimingValue(value: number | null, active: boolean = false) {
  const formatted = formatDuration(value);
  if (!active || value === null) {
    return formatted;
  }
  return `∞ ${formatted}`;
}

export function RunInspector({
  run,
  events,
  eventsLoading,
  eventsError,
  snapshotLoading,
  snapshotError,
  totalRuns,
  onCancel,
}: RunInspectorProps) {
  const [activeTab, setActiveTab] = useState<DebugTab>("Timeline");
  const [requestExpanded, setRequestExpanded] = useState(false);
  const [copiedState, setCopiedState] = useState("");
  const timings = useMemo(() => (run ? computeTimings(run, events) : null), [events, run]);

  useEffect(() => {
    setActiveTab("Timeline");
    setRequestExpanded(false);
    setCopiedState("");
  }, [run?.id]);

  async function handleCopy(label: string, value: string) {
    const copied = await copyText(value);
    setCopiedState(copied ? `${label} copied` : "");
  }

  if (!run) {
    return (
      <section className="debug-inspector debug-inspector-empty">
        <p>Select a run from the list to inspect it.</p>
      </section>
    );
  }

  const activeRun = isActiveRunStatus(run.status);
  const leaseRemainingMs = getLeaseRemainingMs(run);
  const timingCells = timings
    ? [
        { label: "queue_wait", value: timings.queueWait, active: false },
        { label: "1st event", value: timings.firstEventLatency, active: false },
        { label: "1st text_δ", value: timings.firstTextDeltaLatency, active: false },
        ...(activeRun ? [{ label: "in state", value: timings.timeInCurrentState, active: false }] : []),
        { label: "total", value: timings.totalDuration, active: activeRun },
      ]
    : [];

  return (
    <section className="debug-inspector">
      <div className="debug-section-header">
        <div>
          <p className="eyebrow">Run Inspector</p>
          <h3>{truncateId(run.id, 8)}</h3>
        </div>
        <div className="debug-header-actions">
          <button type="button" className="debug-copy-chip" onClick={() => void handleCopy("Run id", run.id)}>
            Copy run id
          </button>
          {activeRun ? (
            <button type="button" className="debug-danger-button" onClick={onCancel}>
              Cancel
            </button>
          ) : null}
        </div>
      </div>

      <div className="debug-run-header">
        <div className="debug-run-header-row">
          <span className={`debug-badge status status-${run.status}`}>{run.status}</span>
          {run.latest_state_code ? <span className="debug-badge state">{run.latest_state_code}</span> : null}
          {copiedState ? <span className="debug-copy-state">{copiedState}</span> : null}
        </div>
        <div className="debug-run-stats">
          <span>{totalRuns} total runs</span>
          <span>magi: {run.magi}</span>
          <span>seq: {run.latest_event_seq}</span>
          <span>worker: {run.worker_id ? truncateId(run.worker_id, 5) : "unclaimed"}</span>
          <span>created: {formatTimestamp(run.created_at)}</span>
          <span>started: {run.started_at ? formatTimestamp(run.started_at) : "—"}</span>
          <span>finished: {run.finished_at ? formatTimestamp(run.finished_at) : "—"}</span>
          {activeRun ? (
            <span className={`tone-${getLeaseTone(leaseRemainingMs)}`}>lease: {formatDuration(leaseRemainingMs)}</span>
          ) : null}
          <span>
            client_req_id: {truncateId(run.client_request_id, 5)}{" "}
            <button
              type="button"
              className="debug-copy-chip"
              onClick={() => void handleCopy("Client request id", run.client_request_id)}
            >
              Copy
            </button>
          </span>
        </div>

        <div className="debug-request-block">
          <div className="debug-request-head">
            <span className="eyebrow">Request</span>
            <button type="button" className="debug-link-button" onClick={() => setRequestExpanded((current) => !current)}>
              {requestExpanded ? "Collapse" : "Expand"}
            </button>
          </div>
          <p className={`debug-request-text${requestExpanded ? " expanded" : ""}`}>
            {run.request_content || "No request content"}
          </p>
        </div>
      </div>

      {(run.status === "failed" || run.status === "cancelled") && run.error_message ? (
        <div className="debug-error-banner">
          <p>{run.error_message}</p>
        </div>
      ) : null}

      {snapshotError ? <p className="debug-list-error">{snapshotError}</p> : null}
      {snapshotLoading ? <p className="debug-list-note">Refreshing snapshot…</p> : null}

      {timingCells.length > 0 ? (
        <div className="debug-timing-bar">
          {timingCells.map((cell) => (
            <div key={cell.label} className={`debug-timing-cell tone-${getLatencyTone(cell.value)}`}>
              <span>{cell.label}</span>
              <strong>{renderTimingValue(cell.value, cell.active)}</strong>
            </div>
          ))}
        </div>
      ) : null}

      <div className="debug-tab-strip">
        {DEBUG_TABS.map((tab) => (
          <button
            key={tab}
            type="button"
            className={`debug-tab${activeTab === tab ? " active" : ""}`}
            onClick={() => setActiveTab(tab)}
          >
            {tab}
          </button>
        ))}
      </div>

      <EventTimeline events={events} tab={activeTab} loading={eventsLoading} error={eventsError} />
    </section>
  );
}
