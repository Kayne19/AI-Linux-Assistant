import type { RunEvent } from "../types";
import type { DebugTab } from "./debugUtils";
import { eventSummary, eventTitle, formatDuration, formatTimestamp, getLatencyTone, getStateRows, TAB_FILTERS } from "./debugUtils";

type EventTimelineProps = {
  events: RunEvent[];
  tab: DebugTab;
};

function EventBadge({ event }: { event: RunEvent }) {
  const label = event.type === "state" ? "state" : event.type;
  return <span className={`debug-event-badge tone-${label === "error" || label === "cancelled" ? "bad" : "neutral"}`}>{label}</span>;
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
        {rows.map(({ event, durationMs }) => (
          <div key={event.seq} className={`debug-event-row${durationMs !== null ? ` tone-${getLatencyTone(durationMs)}` : ""}`}>
            <div className="debug-event-meta">
              <span className="debug-event-seq">#{event.seq}</span>
              <EventBadge event={event} />
              <span className="debug-event-time">{formatTimestamp(event.created_at)}</span>
            </div>
            <div className="debug-event-body">
              <strong>{eventTitle(event)}</strong>
              <span className="debug-event-summary">{eventSummary(event)}</span>
            </div>
            <div className="debug-event-duration">{formatDuration(durationMs)}</div>
          </div>
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
