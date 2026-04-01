import type { RunEvent } from "../types";
import type { DebugTab } from "./debugUtils";
import {
  formatCompactTimestamp,
  formatDuration,
  getEventSummary,
  getEventTitle,
  getLatencyTone,
  getStateDurations,
  getTabEvents,
} from "./debugUtils";

type EventTimelineProps = {
  events: RunEvent[];
  tab: DebugTab;
  loading: boolean;
  error: string;
};

export function EventTimeline({ events, tab, loading, error }: EventTimelineProps) {
  const visibleEvents = getTabEvents(tab, events);
  const stateDurations = getStateDurations(visibleEvents);

  if (loading) {
    return <p className="debug-empty">Loading events…</p>;
  }

  if (error) {
    return <p className="debug-empty debug-error-text">{error}</p>;
  }

  if (visibleEvents.length === 0) {
    return <p className="debug-empty">No matching events.</p>;
  }

  if (tab === "Raw") {
    return (
      <div className="debug-event-list raw">
        {visibleEvents.map((event) => (
          <pre key={event.seq} className="debug-raw-event">
            {JSON.stringify(event, null, 2)}
          </pre>
        ))}
      </div>
    );
  }

  return (
    <div className="debug-event-list">
      {visibleEvents.map((event) => {
        const stateDuration = event.type === "state" ? stateDurations.get(event.seq) ?? null : null;
        const stateTone = stateDuration !== null ? getLatencyTone(stateDuration) : "neutral";
        return (
          <article key={event.seq} className="debug-event-row">
            <div className="debug-event-seq">#{event.seq}</div>
            <div className="debug-event-main">
              <div className="debug-event-head">
                <strong>{getEventTitle(event)}</strong>
                <span>{formatCompactTimestamp(event.created_at)}</span>
              </div>
              <p className="debug-event-summary">{getEventSummary(event)}</p>
            </div>
            {event.type === "state" && tab === "States" ? (
              <div className={`debug-event-duration tone-${stateTone}`}>{formatDuration(stateDuration)}</div>
            ) : null}
          </article>
        );
      })}
    </div>
  );
}
