import { useState, type ReactNode } from "react";
import type { ChatRun, RetrievedContextBlock, RunEvent } from "../types";
import type { DebugTab } from "./debugUtils";
import {
  eventSummary,
  eventTitle,
  formatDuration,
  formatTimestamp,
  getRetrievalEvents,
  getLatencyTone,
  getStateRows,
  isObjectRecord,
  summarizeNestedStateRows,
  TAB_FILTERS,
  type NestedStateRow,
  type StateRow,
} from "./debugUtils";

type EventTimelineProps = {
  events: RunEvent[];
  tab: DebugTab;
  run?: ChatRun | null;
};

function EventBadge({ event }: { event: RunEvent }) {
  const label = event.type === "state" ? "state" : event.type;
  return <span className={`debug-event-badge tone-${label === "error" || label === "cancelled" ? "bad" : "neutral"}`}>{label}</span>;
}

function GenericEventRow({ event }: { event: RunEvent }) {
  return (
    <div className="debug-event-row">
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
  );
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
            <strong>{eventTitle(row.event)}</strong>
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
  const title = eventTitle(row.event);
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

function DetailText({ text }: { text: string }) {
  return <pre className="debug-detail-text compact">{text || "—"}</pre>;
}

function ExpandableText({
  text,
  emptyText,
  initiallyExpanded = false,
  collapsedLines = 6,
}: {
  text: string;
  emptyText: string;
  initiallyExpanded?: boolean;
  collapsedLines?: number;
}) {
  const [expanded, setExpanded] = useState(initiallyExpanded);
  const lines = text.split("\n").length;
  const canCollapse = Boolean(text) && lines > collapsedLines;

  return (
    <>
      <pre className={`debug-detail-text compact${canCollapse && !expanded ? " collapsed" : ""}`}>{text || emptyText}</pre>
      {canCollapse ? (
        <button type="button" className="debug-inline-link" onClick={() => setExpanded((current) => !current)}>
          {expanded ? "Hide" : "Show"}
        </button>
      ) : null}
    </>
  );
}

function DetailCard({
  label,
  children,
}: {
  label: string;
  children: ReactNode;
}) {
  return (
    <div className="debug-detail-card">
      <span className="debug-request-label">{label}</span>
      {children}
    </div>
  );
}

function RetrievalBlockList({
  blocks,
  fallbackText,
}: {
  blocks: RetrievedContextBlock[];
  fallbackText: string;
}) {
  const [expanded, setExpanded] = useState(false);
  const hasBlocks = blocks.length > 0;
  const hasFallbackText = Boolean(fallbackText);

  if (!hasBlocks && !hasFallbackText) {
    return <div className="debug-empty-state">No merged retrieval blocks recorded for this run.</div>;
  }

  const label = hasBlocks
    ? `Show merged blocks (${blocks.length})`
    : "Show merged retrieval context";

  if (!expanded) {
    return (
      <button type="button" className="debug-inline-link" onClick={() => setExpanded(true)}>
        {label}
      </button>
    );
  }

  if (!hasBlocks) {
    return (
      <>
        <button type="button" className="debug-inline-link" onClick={() => setExpanded(false)}>
          Hide merged retrieval context
        </button>
        <DetailText text={fallbackText} />
      </>
    );
  }

  return (
    <>
      <button type="button" className="debug-inline-link" onClick={() => setExpanded(false)}>
        Hide merged blocks
      </button>
      <div className="debug-detail-list">
        {blocks.map((block, index) => (
          <div key={`${block.source}-${block.page_label}-${index}`} className="debug-detail-list-item">
            <strong>{block.source} • {block.page_label}</strong>
            <DetailText text={block.text} />
          </div>
        ))}
      </div>
    </>
  );
}

function ContextTab({ events, run }: { events: RunEvent[]; run?: ChatRun | null }) {
  const inputs = run?.normalized_inputs;
  const recentTurns = inputs?.recent_turns || [];

  return (
    <div className="debug-detail-stack">
      <DetailCard label="conversation summary">
        <ExpandableText
          text={inputs?.conversation_summary_text || ""}
          emptyText="No conversation summary recorded for this run."
        />
      </DetailCard>
      <DetailCard label="recent turns">
        {recentTurns.length === 0 ? <div className="debug-empty-state">No recent turns captured for this run.</div> : (
          <div className="debug-detail-list">
            {recentTurns.map((turn, index) => (
              <div key={`${turn.role}-${index}`} className="debug-detail-list-item">
                <strong>{turn.role}</strong>
                <ExpandableText text={turn.content} emptyText="—" collapsedLines={4} />
              </div>
            ))}
          </div>
        )}
      </DetailCard>
      <div className="debug-timeline-list">
        {events.length === 0 ? <div className="debug-empty-state">No context events for this run.</div> : null}
        {events.map((event) => (
          <GenericEventRow key={event.seq} event={event} />
        ))}
      </div>
    </div>
  );
}

function RetrievalTab({ events, run }: { events: RunEvent[]; run?: ChatRun | null }) {
  const inputs = run?.normalized_inputs;
  return (
    <div className="debug-detail-stack">
      <DetailCard label="retrieval query">
        <DetailText text={inputs?.retrieval_query || "No retrieval query recorded."} />
      </DetailCard>
      <DetailCard label="merged context blocks">
        <RetrievalBlockList
          blocks={inputs?.retrieved_context_blocks || []}
          fallbackText={inputs?.retrieved_context_text || ""}
        />
      </DetailCard>
      <div className="debug-timeline-list">
        {events.length === 0 ? <div className="debug-empty-state">No retrieval events for this run.</div> : null}
        {events.map((event) => (
          <GenericEventRow key={event.seq} event={event} />
        ))}
      </div>
    </div>
  );
}

function renderMemoryItemSummary(item: Record<string, unknown>): string {
  if (typeof item.fact_key === "string" && typeof item.fact_value === "string") {
    return `${item.fact_key} = ${item.fact_value}`;
  }
  if (typeof item.title === "string") {
    return item.summary ? `${item.title} • ${String(item.summary)}` : item.title;
  }
  if (typeof item.action === "string" || typeof item.command === "string") {
    return [item.action, item.command, item.outcome].filter((value) => typeof value === "string" && value).join(" • ");
  }
  if (typeof item.constraint_key === "string" && typeof item.constraint_value === "string") {
    return `${item.constraint_key} = ${item.constraint_value}`;
  }
  if (typeof item.preference_key === "string" && typeof item.preference_value === "string") {
    return `${item.preference_key} = ${item.preference_value}`;
  }
  if (typeof item.summary === "string") {
    return item.summary;
  }
  return JSON.stringify(item);
}

function MemoryStructuredList({
  label,
  items,
}: {
  label: string;
  items: unknown[];
}) {
  const normalizedItems = items.filter(isObjectRecord);
  if (normalizedItems.length === 0) {
    return null;
  }
  return (
    <div className="debug-detail-list-item">
      <strong>{label}</strong>
      <div className="debug-detail-chip-list">
        {normalizedItems.map((item, index) => (
          <div key={`${label}-${index}`} className="debug-detail-chip">
            {renderMemoryItemSummary(item)}
          </div>
        ))}
      </div>
    </div>
  );
}

function MemoryResolutionList({
  label,
  items,
}: {
  label: string;
  items: unknown[];
}) {
  const normalizedItems = items.filter(isObjectRecord);
  if (normalizedItems.length === 0) {
    return null;
  }
  return (
    <div className="debug-detail-list-item">
      <strong>{label}</strong>
      <div className="debug-detail-list">
        {normalizedItems.map((item, index) => (
          <div key={`${label}-${index}`} className="debug-detail-chip">
            <div>{renderMemoryItemSummary(isObjectRecord(item.payload) ? item.payload : item)}</div>
            {"reason" in item && typeof item.reason === "string" ? <span>{item.reason}</span> : null}
            {"status" in item && typeof item.status === "string" ? <span>{item.status}</span> : null}
          </div>
        ))}
      </div>
    </div>
  );
}

function MemoryEventRow({ event }: { event: RunEvent }) {
  if (event.type !== "event" || !isObjectRecord(event.payload)) {
    return <GenericEventRow event={event} />;
  }
  const payload = event.payload;

  return (
    <div className="debug-event-row">
      <div className="debug-event-meta">
        <span className="debug-event-seq">#{event.seq}</span>
        <EventBadge event={event} />
        <span className="debug-event-time">{formatTimestamp(event.created_at)}</span>
      </div>
      <div className="debug-event-body">
        <strong>{eventTitle(event)}</strong>
        <span className="debug-event-summary">{eventSummary(event)}</span>
        {event.code === "memory_extracted" ? (
          <div className="debug-detail-list">
            {isObjectRecord(payload.items) ? (
              <>
                <MemoryStructuredList label="facts" items={Array.isArray(payload.items.facts) ? payload.items.facts : []} />
                <MemoryStructuredList label="issues" items={Array.isArray(payload.items.issues) ? payload.items.issues : []} />
                <MemoryStructuredList label="attempts" items={Array.isArray(payload.items.attempts) ? payload.items.attempts : []} />
                <MemoryStructuredList label="constraints" items={Array.isArray(payload.items.constraints) ? payload.items.constraints : []} />
                <MemoryStructuredList label="preferences" items={Array.isArray(payload.items.preferences) ? payload.items.preferences : []} />
                {typeof payload.items.session_summary === "string" && payload.items.session_summary ? (
                  <MemoryStructuredList label="session summary" items={[{ summary: payload.items.session_summary }]} />
                ) : null}
              </>
            ) : null}
          </div>
        ) : null}
        {event.code === "memory_resolved" || event.code === "memory_committed" ? (
          <div className="debug-detail-list">
            {isObjectRecord(payload.committed_full) ? (
              <>
                <MemoryStructuredList label="committed facts" items={Array.isArray(payload.committed_full.facts) ? payload.committed_full.facts : []} />
                <MemoryStructuredList label="committed issues" items={Array.isArray(payload.committed_full.issues) ? payload.committed_full.issues : []} />
                <MemoryStructuredList label="committed attempts" items={Array.isArray(payload.committed_full.attempts) ? payload.committed_full.attempts : []} />
                <MemoryStructuredList label="committed constraints" items={Array.isArray(payload.committed_full.constraints) ? payload.committed_full.constraints : []} />
                <MemoryStructuredList label="committed preferences" items={Array.isArray(payload.committed_full.preferences) ? payload.committed_full.preferences : []} />
              </>
            ) : null}
            <MemoryResolutionList label="candidates" items={Array.isArray(payload.candidates_full) ? payload.candidates_full : []} />
            <MemoryResolutionList label="conflicts" items={Array.isArray(payload.conflicts_full) ? payload.conflicts_full : []} />
          </div>
        ) : null}
      </div>
    </div>
  );
}

function MemoryTab({ events, run }: { events: RunEvent[]; run?: ChatRun | null }) {
  const snapshotText = run?.normalized_inputs?.memory_snapshot_text || "";
  return (
    <div className="debug-detail-stack">
      <DetailCard label="loaded memory snapshot">
        <DetailText text={snapshotText || "No memory snapshot loaded for this run."} />
      </DetailCard>
      <div className="debug-timeline-list">
        {events.length === 0 ? <div className="debug-empty-state">No memory events for this run.</div> : null}
        {events.map((event) => (
          <MemoryEventRow key={event.seq} event={event} />
        ))}
      </div>
    </div>
  );
}

export function EventTimeline({ events, tab, run }: EventTimelineProps) {
  const filteredEvents = events.filter(TAB_FILTERS[tab]);

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
    if (rows.length === 0) {
      return <div className="debug-empty-state">No events in this tab yet.</div>;
    }
    return (
      <div className="debug-timeline-list">
        {rows.map((row) => (
          <StateTimelineRow key={row.key} row={row} />
        ))}
      </div>
    );
  }

  if (tab === "Context") {
    return <ContextTab events={filteredEvents} run={run} />;
  }

  if (tab === "Retrieval") {
    return <RetrievalTab events={getRetrievalEvents(events)} run={run} />;
  }

  if (tab === "Memory") {
    return <MemoryTab events={filteredEvents} run={run} />;
  }

  if (filteredEvents.length === 0) {
    return <div className="debug-empty-state">No events in this tab yet.</div>;
  }

  return (
    <div className="debug-timeline-list">
      {filteredEvents.map((event) => (
        <GenericEventRow key={event.seq} event={event} />
      ))}
    </div>
  );
}
