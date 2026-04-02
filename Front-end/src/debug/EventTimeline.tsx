import { useState } from "react";
import type { RunEvent } from "../types";
import type { DebugTab } from "./debugUtils";
import {
  eventSummary,
  eventTitle,
  formatDuration,
  formatTimestamp,
  getLatencyTone,
  getStateRows,
  summarizeNestedStateRows,
  TAB_FILTERS,
  type NestedStateRow,
  type StateRow,
} from "./debugUtils";

type EventTimelineProps = {
  events: RunEvent[];
  tab: DebugTab;
};

function EventBadge({ event }: { event: RunEvent }) {
  const label = event.type === "state" ? "state" : event.type;
  return <span className={`debug-event-badge tone-${label === "error" || label === "cancelled" ? "bad" : "neutral"}`}>{label}</span>;
}

function NestedStateTrace({
  rows,
}: {
  rows: NestedStateRow[];
}) {
  return (
    <div className="debug-substate-list">
      {rows.map((row) => (
        <div key={row.key} className="debug-substate-row">
          <div className="debug-event-meta">
            <span className="debug-event-seq">#{row.event.seq}</span>
            <span className="debug-event-badge tone-neutral">{row.phase}</span>
            <span className="debug-event-time">{formatTimestamp(row.event.created_at)}</span>
          </div>
          <div className="debug-event-body">
            <strong>{row.stateCode}</strong>
            <span className="debug-event-summary">{eventSummary(row.event)}</span>
          </div>
          <div className="debug-event-duration">{formatDuration(row.durationMs)}</div>
        </div>
      ))}
    </div>
  );
}

function StateTimelineRow({
  row,
}: {
  row: StateRow;
}) {
  const [expanded, setExpanded] = useState(false);
  const nestedSummary = summarizeNestedStateRows(row.nestedRows);
  const hasNestedRows = row.nestedRows.length > 0;
  const title = row.kind === "subsystem" ? eventTitle(row.event) : eventTitle(row.event);
  const summary = row.kind === "subsystem"
    ? eventSummary(row.event)
    : (hasNestedRows ? `${eventSummary(row.event)} • ${nestedSummary}` : eventSummary(row.event));

  return (
    <div className={`debug-event-row${row.durationMs !== null ? ` tone-${getLatencyTone(row.durationMs)}` : ""}`}>
      <div className="debug-event-meta">
        <span className="debug-event-seq">#{row.event.seq}</span>
        <EventBadge event={row.event} />
        <span className="debug-event-time">{formatTimestamp(row.event.created_at)}</span>
      </div>
      <div className="debug-event-body">
        <strong>{title}</strong>
        <span className="debug-event-summary">{summary}</span>
        {hasNestedRows ? (
          <>
            <button
              type="button"
              className="debug-state-disclosure"
              onClick={() => setExpanded((current) => !current)}
            >
              {expanded ? "Hide execution detail" : `Show execution detail (${row.nestedRows.length})`}
            </button>
            {expanded ? <NestedStateTrace rows={row.nestedRows} /> : null}
          </>
        ) : null}
      </div>
      <div className="debug-event-duration">{formatDuration(row.durationMs)}</div>
    </div>
  );
}

export function EventTimeline({ events, tab }: EventTimelineProps) {
  const filteredEvents = events.filter(TAB_FILTERS[tab]);

  if (filteredEvents.length === 0) {
    return <div className="debug-empty-state">No events in this tab yet.</div>;
  }

  if (tab === "Raw") {
    return (
      <div className="debug-timeline-list raw">
        {filteredEvents.map((event) => (
          <pre key={event.seq} className="debug-raw-event">
            {JSON.stringify(event, null, 2)}
          </pre>
        ))}
      </div>
    );
  }

  if (tab === "States") {
    const rows = getStateRows(filteredEvents);
    return (
      <div className="debug-timeline-list">
        {rows.map((row) => (
          <StateTimelineRow key={row.key} row={row} />
        ))}
      </div>
    );
  }

  return (
    <div className="debug-timeline-list">
      {filteredEvents.map((event) => (
        <div key={event.seq} className="debug-event-row">
          <div className="debug-event-meta">
            <span className="debug-event-seq">#{event.seq}</span>
            <EventBadge event={event} />
            <span className="debug-event-time">{formatTimestamp(event.created_at)}</span>
          </div>
          <div className="debug-event-body">
            <strong>{eventTitle(event)}</strong>
            <span className="debug-event-summary">{eventSummary(event)}</span>
          </div>
        </div>
      ))}
    </div>
  );
}
