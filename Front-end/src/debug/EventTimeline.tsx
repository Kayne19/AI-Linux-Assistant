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
  isRetrievalToolEvent,
  isObjectRecord,
  summarizeNestedStateRows,
  TAB_FILTERS,
  type NestedStateRow,
  type StateRow,
} from "./debugUtils";

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
      {(canCollapse || text) ? (
        <div className="debug-text-controls">
          {canCollapse ? (
            <button type="button" className="debug-inline-link" onClick={() => setExpanded((current) => !current)}>
              {expanded ? "Hide" : "Show"}
            </button>
          ) : null}
          {text ? <CopyButton value={text} /> : null}
        </div>
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

function stringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string" && Boolean(item)) : [];
}

function numberOrNull(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function normalizeEntities(value: unknown): Record<string, string[]> | null {
  if (!isObjectRecord(value)) {
    return null;
  }
  const entries = Object.entries(value)
    .map(([key, bucket]) => [key, stringArray(bucket)] as const)
    .filter(([, bucket]) => bucket.length > 0);
  return entries.length > 0 ? Object.fromEntries(entries) : null;
}

function formatEntityPreview(entities: Record<string, string[]> | null | undefined): string {
  if (!entities) {
    return "";
  }
  return ["commands", "paths", "services", "packages", "daemons"]
    .map((key) => {
      const values = entities[key] || [];
      return values.length > 0 ? `${key}: ${values.slice(0, 3).join(", ")}` : "";
    })
    .filter(Boolean)
    .join(" · ");
}

function getEventPayload(event: RunEvent): Record<string, unknown> {
  return event.type === "event" && isObjectRecord(event.payload) ? event.payload : {};
}

function getRetrievalToolArgs(payload: Record<string, unknown>): Record<string, unknown> {
  return isObjectRecord(payload.args)
    ? payload.args
    : (isObjectRecord(payload.tool_args) ? payload.tool_args : {});
}

function getScopeHintSummary(args: Record<string, unknown>): string {
  const scopeHints = isObjectRecord(args.scope_hints) ? args.scope_hints : {};
  const hints = [
    ["os", scopeHints.os_family],
    ["src", scopeHints.source_family],
    ["pkg", scopeHints.package_managers],
    ["init", scopeHints.init_systems],
    ["subs", scopeHints.major_subsystems],
  ]
    .map(([label, value]) => {
      const values = Array.isArray(value) ? value.filter(Boolean) : (value ? [value] : []);
      return values.length > 0 ? `${label}=${values.join(",")}` : "";
    })
    .filter(Boolean);
  const ids = stringArray(args.canonical_source_ids);
  return [
    hints.length > 0 ? `scope_hints: ${hints.join(",")}` : "",
    ids.length > 0 ? `ids=${ids.length}` : "",
  ].filter(Boolean).join(" · ");
}

function RetrievalBlockList({
  blocks,
  fallbackText,
  showLabel,
  hideLabel,
  emptyText,
}: {
  blocks: RetrievedContextBlock[];
  fallbackText: string;
  showLabel?: string;
  hideLabel?: string;
  emptyText?: string;
}) {
  const [expanded, setExpanded] = useState(false);
  const hasBlocks = blocks.length > 0;
  const hasFallbackText = Boolean(fallbackText);

  if (!hasBlocks && !hasFallbackText) {
    return <div className="debug-empty-state">{emptyText || "No merged retrieval blocks recorded for this run."}</div>;
  }

  const label = showLabel || (hasBlocks
    ? `Show merged blocks (${blocks.length})`
    : "Show merged retrieval context");
  const collapseLabel = hideLabel || (hasBlocks ? "Hide merged blocks" : "Hide merged retrieval context");

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
          {collapseLabel}
        </button>
        <DetailText text={fallbackText} />
      </>
    );
  }

  return (
    <>
      <button type="button" className="debug-inline-link" onClick={() => setExpanded(false)}>
        {collapseLabel}
      </button>
      <div className="debug-detail-list">
        {blocks.map((block, index) => {
          const sectionPath = stringArray(block.section_path);
          const subsystems = stringArray(block.local_subsystems);
          const entityPreview = formatEntityPreview(block.entities);
          const pageLabel = block.citation_label || block.page_label;
          return (
            <div key={`${block.source}-${pageLabel}-${index}`} className="debug-detail-list-item">
              <strong>{block.source} • {pageLabel}</strong>
              {sectionPath.length > 0 ? <span className="debug-event-summary">§ {sectionPath.join(" › ")}</span> : null}
              {block.section_title && sectionPath.length === 0 ? <span className="debug-event-summary">§ {block.section_title}</span> : null}
              <div className="debug-detail-chip-list">
                {block.chunk_type ? <span className="debug-detail-chip">chunk_type: {block.chunk_type}</span> : null}
                {block.canonical_source_id ? <span className="debug-detail-chip">id: {block.canonical_source_id}</span> : null}
                {subsystems.length > 0 ? <span className="debug-detail-chip">subsystems: {subsystems.join(", ")}</span> : null}
                {block.page_start !== null && block.page_start !== undefined ? (
                  <span className="debug-detail-chip">
                    pages: {block.page_start}{block.page_end && block.page_end !== block.page_start ? `-${block.page_end}` : ""}
                  </span>
                ) : null}
              </div>
              {entityPreview ? <ExpandableText text={entityPreview} emptyText="—" collapsedLines={2} /> : null}
              <DetailText text={block.text} />
            </div>
          );
        })}
      </div>
    </>
  );
}

function normalizeRetrievedBlocks(value: unknown): RetrievedContextBlock[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value
    .filter(isObjectRecord)
    .map((block) => ({
      source: typeof block.source === "string" ? block.source : "Unknown",
      pages: Array.isArray(block.pages) ? block.pages.map((page) => Number(page)).filter(Number.isFinite) : [],
      page_label: typeof block.page_label === "string" ? block.page_label : (typeof block.citation_label === "string" ? block.citation_label : "Page ?"),
      text: typeof block.text === "string" ? block.text : "",
      section_path: typeof block.section_path === "string" ? [block.section_path] : stringArray(block.section_path),
      section_title: typeof block.section_title === "string" ? block.section_title : null,
      chunk_type: typeof block.chunk_type === "string" ? block.chunk_type : null,
      local_subsystems: stringArray(block.local_subsystems),
      entities: normalizeEntities(block.entities),
      canonical_source_id: typeof block.canonical_source_id === "string" ? block.canonical_source_id : null,
      page_start: numberOrNull(block.page_start),
      page_end: numberOrNull(block.page_end),
      citation_label: typeof block.citation_label === "string" ? block.citation_label : null,
    }))
    .filter((block) => Boolean(block.text));
}

function RetrievalScopeCard({ events }: { events: RunEvent[] }) {
  const scopeEvent = events.find((event) => event.type === "event" && event.code === "retrieval_scope_selected");
  if (!scopeEvent) {
    return null;
  }

  const payload = getEventPayload(scopeEvent);
  const candidateCount = typeof payload.candidate_count === "number" ? payload.candidate_count : null;
  const widenings = typeof payload.widenings_taken === "number" ? payload.widenings_taken : null;
  const winningFilter = isObjectRecord(payload.winning_filter)
    ? payload.winning_filter
    : (isObjectRecord(payload.filter) ? payload.filter : (isObjectRecord(payload.selected_filter) ? payload.selected_filter : {}));
  const rankingItems = Array.isArray(payload.tier_rankings) ? payload.tier_rankings.filter(isObjectRecord).slice(0, 10) : [];
  const toolStart = events.find((event) => event.type === "event" && event.code === "tool_start" && isRetrievalToolEvent(event));
  const toolInput = toolStart ? getScopeHintSummary(getRetrievalToolArgs(getEventPayload(toolStart))) : "";
  const filterRows = [
    ["os_family", winningFilter.os_family],
    ["source_family", winningFilter.source_family],
    ["package_managers", winningFilter.package_managers],
    ["init_systems", winningFilter.init_systems],
    ["major_subsystems", winningFilter.major_subsystems],
    ["explicit_doc_ids", winningFilter.explicit_doc_ids],
  ]
    .map(([key, value]) => {
      const values = Array.isArray(value) ? value.filter(Boolean) : (value ? [value] : []);
      return values.length > 0 ? `${key}: ${values.join(", ")}` : "";
    })
    .filter(Boolean);

  return (
    <DetailCard label="Retrieval Scope">
      <div className="debug-detail-list">
        <div className="debug-detail-list-item">
          <strong>
            {candidateCount !== null ? `${candidateCount} candidate doc${candidateCount === 1 ? "" : "s"}` : "Candidate docs unavailable"}
            {widenings !== null ? ` • widenings=${widenings}` : ""}
          </strong>
          {filterRows.length > 0 ? <DetailText text={filterRows.join("\n")} /> : null}
        </div>
        {rankingItems.length > 0 ? (
          <div className="debug-detail-list-item">
            <strong>tier rankings</strong>
            <DetailText
              text={rankingItems.map((item, index) => {
                const title = typeof item.canonical_title === "string"
                  ? item.canonical_title
                  : (typeof item.title === "string" ? item.title : "Untitled document");
                const score = typeof item.score === "number" ? item.score.toFixed(1) : String(item.score || "—");
                const matched = stringArray(item.matched_fields);
                return `${index + 1}. ${title} — score ${score}${matched.length > 0 ? ` [matched: ${matched.join(", ")}]` : ""}`;
              }).join("\n")}
            />
          </div>
        ) : null}
        {toolInput ? (
          <div className="debug-detail-list-item">
            <strong>Tool input</strong>
            <span className="debug-event-summary">{toolInput}</span>
          </div>
        ) : null}
      </div>
    </DetailCard>
  );
}

function RetrievalEventRow({ event }: { event: RunEvent }) {
  if (event.type !== "event" || !isObjectRecord(event.payload) || !isRetrievalToolEvent(event)) {
    return <GenericEventRow event={event} />;
  }

  const payload = event.payload;
  const resultBlocks = normalizeRetrievedBlocks(payload.result_blocks);
  const resultText = typeof payload.result_text === "string" ? payload.result_text : "";

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
        {event.code === "tool_complete" && (resultBlocks.length > 0 || resultText) ? (
          <div className="debug-detail-list">
            <RetrievalBlockList
              blocks={resultBlocks}
              fallbackText={resultText}
              showLabel={resultBlocks.length > 0 ? `Show tool call chunks (${resultBlocks.length})` : "Show tool call retrieval context"}
              hideLabel={resultBlocks.length > 0 ? "Hide tool call chunks" : "Hide tool call retrieval context"}
              emptyText="No retrieval text recorded for this tool call."
            />
          </div>
        ) : null}
      </div>
    </div>
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
      <RetrievalScopeCard events={events} />
      <div className="debug-timeline-list">
        {events.length === 0 ? <div className="debug-empty-state">No retrieval events for this run.</div> : null}
        {events.map((event) => (
          <RetrievalEventRow key={event.seq} event={event} />
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
        {filteredEvents.map((event) => {
          const json = JSON.stringify(event, null, 2);
          return (
            <div key={event.seq} className="debug-raw-event-wrapper">
              <pre className="debug-raw-event">{json}</pre>
              <CopyButton value={json} />
            </div>
          );
        })}
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
