import type { RunEvent } from "../types";
import {
  TAB_FILTERS,
  computeStateRows,
  formatAbsoluteTime,
  formatDuration,
  getEventCode,
  getEventMessage,
  getEventPayload,
  getLatencyTone,
  summarizeRunEvent,
  type DebugTab,
} from "./debugUtils";

type EventTimelineProps = {
  tab: DebugTab;
  events: RunEvent[];
  loading: boolean;
  error: string;
};

function renderPayload(event: RunEvent) {
  const payload = getEventPayload(event);
  if (!payload || Object.keys(payload).length === 0) {
    return null;
  }
  return (
    <details className="debug-event-details">
      <summary>Payload</summary>
      <pre>{JSON.stringify(payload, null, 2)}</pre>
    </details>
  );
}

export function EventTimeline({ tab, events, loading, error }: EventTimelineProps) {
  if (error) {
    return <p className="debug-empty-state">{error}</p>;
  }

  if (loading && events.length === 0) {
    return <p className="debug-empty-state">Loading events...</p>;
  }

  if (tab === "States") {
    const stateRows = computeStateRows(events);
    if (stateRows.length === 0) {
      return <p className="debug-empty-state">No state events for this run.</p>;
    }
    return (
      <div className="debug-event-list">
        {stateRows.map(({ event, durationMs }) => (
          <article key={event.seq} className="debug-event-row">
            <div className="debug-event-head">
              <span className="debug-event-seq">#{event.seq}</span>
              <span className="debug-event-code">{getEventCode(event)}</span>
              <span className={`debug-inline-metric tone-${getLatencyTone(durationMs)}`}>
                {formatDuration(durationMs)}
              </span>
            </div>
            <div className="debug-event-meta">
              <span>{formatAbsoluteTime(event.created_at)}</span>
              <span>{durationMs == null ? "Current state" : "State duration"}</span>
            </div>
          </article>
        ))}
      </div>
    );
  }

  const filteredEvents = tab === "Raw" ? events : events.filter(TAB_FILTERS[tab]);
  if (filteredEvents.length === 0) {
    return <p className="debug-empty-state">No events match this tab yet.</p>;
  }

  return (
    <div className="debug-event-list">
      {filteredEvents.map((event) => (
        <article key={event.seq} className="debug-event-row">
          <div className="debug-event-head">
            <span className="debug-event-seq">#{event.seq}</span>
            <span className="debug-event-code">{getEventCode(event)}</span>
            <span className="debug-event-kind">{event.type}</span>
          </div>
          <div className="debug-event-meta">
            <span>{formatAbsoluteTime(event.created_at)}</span>
            <span>{summarizeRunEvent(event)}</span>
          </div>
          {event.type === "error" || event.type === "cancelled" ? (
            <p className="debug-event-message">{getEventMessage(event)}</p>
          ) : null}
          {tab === "Raw" ? <pre>{JSON.stringify(event, null, 2)}</pre> : renderPayload(event)}
        </article>
      ))}
    </div>
  );
}
