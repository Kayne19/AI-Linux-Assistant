import { useEffect, useMemo, useState } from "react";
import type { ChatRun, RunEvent } from "../types";
import { EventTimeline } from "./EventTimeline";
import {
  DEBUG_TABS,
  computeTimings,
  formatAbsoluteTime,
  formatDuration,
  getLatencyTone,
  getLeaseTone,
  isActiveRunStatus,
  truncateMiddle,
  type DebugTab,
} from "./debugUtils";

type RunInspectorProps = {
  run: ChatRun | null;
  events: RunEvent[];
  eventsLoading: boolean;
  eventsError: string;
  snapshotLoading: boolean;
  snapshotError: string;
  onCancel: () => void;
};

type TimingCell = {
  label: string;
  value: number | null;
  active?: boolean;
};

async function copyText(value: string) {
  if (!value) {
    return;
  }
  try {
    await navigator.clipboard.writeText(value);
  } catch {
    // Ignore clipboard failures in the debug UI.
  }
}

export function RunInspector({
  run,
  events,
  eventsLoading,
  eventsError,
  snapshotLoading,
  snapshotError,
  onCancel,
}: RunInspectorProps) {
  const [activeTab, setActiveTab] = useState<DebugTab>("Timeline");
  const timings = useMemo(() => (run ? computeTimings(run, events) : null), [events, run]);
  const isActive = run ? isActiveRunStatus(run.status) : false;

  useEffect(() => {
    setActiveTab("Timeline");
  }, [run?.id]);

  const timingCells: TimingCell[] = timings
    ? [
        { label: "queue_wait", value: timings.queueWait },
        { label: "1st event", value: timings.firstEventLatency },
        { label: "1st text_\u03b4", value: timings.firstTextDeltaLatency },
        ...(isActive ? [{ label: "in state", value: timings.timeInCurrentState }] : []),
        { label: "total", value: timings.totalDuration, active: isActive },
      ]
    : [];

  if (!run) {
    return (
      <section className="debug-panel-inspector">
        <div className="debug-inspector-empty">
          <p className="eyebrow">Inspector</p>
          <h3>Select a run</h3>
          <p className="debug-empty-state">Choose an active or historical run to inspect its timeline and timings.</p>
        </div>
      </section>
    );
  }

  const leaseRemainingMs = run.lease_expires_at ? Date.parse(run.lease_expires_at) - Date.now() : null;

  return (
    <section className="debug-panel-inspector">
      <div className="debug-panel-section-head">
        <div>
          <p className="eyebrow">Inspector</p>
          <h3>{truncateMiddle(run.id, 12, 8)}</h3>
        </div>
        <button type="button" className="ghost-button compact" onClick={() => copyText(run.id)}>
          Copy run id
        </button>
      </div>

      <div className="debug-run-header">
        <div className="debug-run-badges">
          <span className={`debug-status-badge status-${run.status}`}>{run.status}</span>
          <span className="debug-state-badge">{run.latest_state_code || "no-state"}</span>
        </div>
        {isActive ? (
          <button type="button" className="ghost-button compact" onClick={onCancel}>
            Cancel
          </button>
        ) : null}
      </div>

      <div className="debug-run-fields">
        {isActive ? (
          <p className="debug-field-row">
            <span>worker: {run.worker_id ? truncateMiddle(run.worker_id) : "unclaimed"}</span>
            <span className={`tone-${getLeaseTone(run)}`}>lease: {formatDuration(leaseRemainingMs)}</span>
          </p>
        ) : null}
        <p className="debug-field-row">
          <span>magi: {run.magi}</span>
          <span>
            client_req_id: {truncateMiddle(run.client_request_id || "none", 10, 6)}{" "}
            <button type="button" className="debug-inline-button" onClick={() => copyText(run.client_request_id)}>
              copy
            </button>
          </span>
        </p>
        <details className="debug-request-details">
          <summary>request</summary>
          <p>{run.request_content || "No request content"}</p>
        </details>
        <p className="debug-field-row">
          <span>created: {formatAbsoluteTime(run.created_at)}</span>
          <span>started: {run.started_at ? formatAbsoluteTime(run.started_at) : "\u2014"}</span>
        </p>
        <p className="debug-field-row">
          <span>finished: {run.finished_at ? formatAbsoluteTime(run.finished_at) : "\u2014"}</span>
          <span>latest seq: {run.latest_event_seq}</span>
        </p>
      </div>

      {(run.status === "failed" || run.status === "cancelled") && run.error_message ? (
        <div className="debug-error-banner">
          <strong>ERROR</strong>
          <p>{run.error_message}</p>
        </div>
      ) : null}

      {timings ? (
        <div className="debug-timing-bar">
          {timingCells.map((cell) => (
            <div key={cell.label} className="debug-timing-cell">
              <span className="debug-timing-label">{cell.label}</span>
              <strong className={`tone-${getLatencyTone(cell.value)}`}>
                {formatDuration(cell.value, { active: cell.active })}
              </strong>
            </div>
          ))}
        </div>
      ) : null}

      {snapshotError ? <p className="debug-empty-state">{snapshotError}</p> : null}
      {snapshotLoading ? <p className="debug-panel-subtle">Refreshing run snapshot...</p> : null}

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

      <EventTimeline tab={activeTab} events={events} loading={eventsLoading} error={eventsError} />
    </section>
  );
}
