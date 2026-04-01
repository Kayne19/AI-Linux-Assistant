import { useEffect, useState } from "react";
import { api } from "../api";
import type { ChatRun, RunEvent } from "../types";
import { EventTimeline } from "./EventTimeline";
import {
  computeTimings,
  DEBUG_TABS,
  formatDuration,
  formatTimestamp,
  getLatencyTone,
  getLeaseRemainingMs,
  getLeaseTone,
  hasErrorBanner,
  isActiveRunStatus,
  toneForStatus,
  truncateMiddle,
  type DebugTab,
} from "./debugUtils";
import { useRunEvents } from "./useRunEvents";
import { useRunSnapshot } from "./useRunSnapshot";
import { useRunStream } from "./useRunStream";

type RunInspectorProps = {
  runId: string;
  onRunChange: (run: ChatRun) => void;
};

function CopyButton({ value }: { value: string }) {
  const [copied, setCopied] = useState(false);

  async function handleCopy() {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 900);
    } catch {
      setCopied(false);
    }
  }

  return (
    <button type="button" className="debug-copy-button" onClick={handleCopy}>
      {copied ? "copied" : "copy"}
    </button>
  );
}

function TimingCell({
  label,
  value,
  active,
  activePrefix,
}: {
  label: string;
  value: number | null;
  active?: boolean;
  activePrefix?: boolean;
}) {
  const tone = getLatencyTone(value);
  const formatted = value === null ? "—" : `${active && activePrefix ? "∞ " : ""}${formatDuration(value)}`;
  return (
    <div className={`debug-timing-cell tone-${tone}`}>
      <span>{label}</span>
      <strong>{formatted}</strong>
    </div>
  );
}

function TimingBar({ run, events, nowMs }: { run: ChatRun; events: RunEvent[]; nowMs: number }) {
  const timings = computeTimings(run, events, nowMs);
  const active = isActiveRunStatus(run.status);

  return (
    <div className={`debug-timing-bar${active ? " active" : ""}`}>
      <TimingCell label="queue_wait" value={timings.queueWaitMs} />
      <TimingCell label="1st event" value={timings.firstEventLatencyMs} />
      <TimingCell label="1st text_δ" value={timings.firstTextDeltaLatencyMs} />
      {active ? <TimingCell label="in state" value={timings.timeInCurrentStateMs} /> : null}
      <TimingCell label="total" value={timings.totalDurationMs} active={active} activePrefix={active} />
    </div>
  );
}

export function RunInspector({ runId, onRunChange }: RunInspectorProps) {
  const { run, loading, error, refresh } = useRunSnapshot(runId);
  const { events, loading: eventsLoading, error: eventsError, highestSeqSeen, appendLiveEvent, backfill } = useRunEvents(runId);
  const [selectedTab, setSelectedTab] = useState<DebugTab>("Timeline");
  const [requestExpanded, setRequestExpanded] = useState(false);
  const [nowMs, setNowMs] = useState(() => Date.now());
  const isActive = run ? isActiveRunStatus(run.status) : false;

  useEffect(() => {
    setSelectedTab("Timeline");
    setRequestExpanded(false);
  }, [runId]);

  useEffect(() => {
    if (!run) {
      return;
    }
    onRunChange(run);
  }, [onRunChange, run]);

  useEffect(() => {
    if (!run || !isActiveRunStatus(run.status)) {
      return;
    }
    const intervalId = window.setInterval(() => {
      setNowMs(Date.now());
    }, 1000);
    return () => {
      window.clearInterval(intervalId);
    };
  }, [run]);

  useRunStream({
    runId,
    enabled: Boolean(runId && isActive),
    initialAfterSeq: highestSeqSeen,
    onLiveEvent: appendLiveEvent,
    onBackfill: backfill,
    onTerminal: () => {
      void refresh();
    },
  });

  async function handleCancel() {
    if (!runId) {
      return;
    }
    try {
      const nextRun = await api.cancelRun(runId);
      onRunChange(nextRun);
      await refresh();
    } catch {
      await refresh();
    }
  }

  if (!runId) {
    return <div className="debug-empty-state">Select a run to inspect it.</div>;
  }

  if (loading && !run) {
    return <div className="debug-empty-state">Loading run snapshot…</div>;
  }

  if (!run) {
    return <div className="debug-inline-error">{error || "Run snapshot unavailable."}</div>;
  }

  const leaseRemainingMs = getLeaseRemainingMs(run, nowMs);
  const leaseTone = getLeaseTone(leaseRemainingMs);
  const requestText = requestExpanded || run.request_content.length <= 220
    ? run.request_content
    : `${run.request_content.slice(0, 220)}…`;

  return (
    <div className="debug-run-inspector">
      <div className="debug-inspector-header">
        <div className="debug-inspector-topline">
          <span className={`debug-badge tone-${toneForStatus(run.status)}`}>{run.status}</span>
          {run.latest_state_code ? <span className="debug-badge subtle">{run.latest_state_code}</span> : null}
          <code className="debug-run-id">{truncateMiddle(run.id, 10, 6)}</code>
          <CopyButton value={run.id} />
          {isActive ? (
            <button type="button" className="ghost-button compact debug-cancel-button" onClick={handleCancel}>
              Cancel
            </button>
          ) : null}
        </div>

        <div className="debug-header-grid">
          {isActive && run.worker_id ? <div>worker: <code>{truncateMiddle(run.worker_id, 8, 6)}</code></div> : null}
          {isActive && leaseRemainingMs !== null ? (
            <div className={`tone-${leaseTone}`}>lease: {leaseRemainingMs > 0 ? `${formatDuration(leaseRemainingMs)} left` : "expired"}</div>
          ) : null}
          <div>magi: <code>{run.magi || "off"}</code></div>
          <div>
            client_req_id: <code>{truncateMiddle(run.client_request_id || "—", 8, 4)}</code>
            {run.client_request_id ? <CopyButton value={run.client_request_id} /> : null}
          </div>
          <div>created: {formatTimestamp(run.created_at)}</div>
          <div>started: {formatTimestamp(run.started_at)}</div>
          <div>finished: {formatTimestamp(run.finished_at)}</div>
        </div>

        <div className="debug-request-block">
          <span className="debug-request-label">request</span>
          <p>{requestText || "No request text stored."}</p>
          {run.request_content.length > 220 ? (
            <button type="button" className="debug-inline-link" onClick={() => setRequestExpanded((current) => !current)}>
              {requestExpanded ? "Collapse" : "Expand"}
            </button>
          ) : null}
        </div>
      </div>

      {hasErrorBanner(run) ? (
        <div className="debug-error-banner">
          <strong>ERROR</strong>
          <p>{run.error_message}</p>
        </div>
      ) : null}

      <TimingBar run={run} events={events} nowMs={nowMs} />

      <div className="debug-tab-strip">
        {DEBUG_TABS.map((tab) => (
          <button
            key={tab}
            type="button"
            className={`debug-tab${selectedTab === tab ? " active" : ""}`}
            onClick={() => setSelectedTab(tab)}
          >
            {tab}
          </button>
        ))}
      </div>

      {error ? <div className="debug-inline-error">{error}</div> : null}
      {eventsError ? <div className="debug-inline-error">{eventsError}</div> : null}
      {eventsLoading && events.length === 0 ? <div className="debug-empty-state">Loading event history…</div> : null}

      <EventTimeline events={events} tab={selectedTab} />
    </div>
  );
}
