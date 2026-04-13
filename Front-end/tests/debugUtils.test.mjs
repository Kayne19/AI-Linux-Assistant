import test from "node:test";
import assert from "node:assert/strict";

import { eventSummary, getRetrievalEvents, TAB_FILTERS } from "../.test-dist/src/debug/debugUtils.js";

test("summarizes retrieval completion with merged block count and sources", () => {
  const summary = eventSummary({
    type: "event",
    seq: 14,
    code: "retrieval_complete",
    created_at: "2026-04-12T12:00:00Z",
    payload: {
      merged_blocks: 2,
      selected_sources: ["guide.pdf:Page 4", "manual.pdf:Pages 8-9"],
    },
  });

  assert.equal(summary, "2 merged blocks • guide.pdf:Page 4, manual.pdf:Pages 8-9");
});

test("summarizes memory resolution with committed counts and unresolved totals", () => {
  const summary = eventSummary({
    type: "event",
    seq: 22,
    code: "memory_resolved",
    created_at: "2026-04-12T12:00:00Z",
    payload: {
      committed: {
        facts: 1,
        issues: 0,
      },
      candidates: 2,
      conflicts: 1,
    },
  });

  assert.equal(summary, "facts=1 • issues=0 • candidates=2 • conflicts=1");
});

test("routes retrieval tool calls into the retrieval tab", () => {
  const event = {
    type: "event",
    seq: 9,
    code: "tool_start",
    created_at: "2026-04-12T12:00:00Z",
    payload: {
      name: "search_rag_database",
      args: {
        query: "journalctl ssh",
      },
    },
  };

  assert.equal(TAB_FILTERS.Retrieval(event), true);
});

test("summarizes retrieval tool completion with chunk count", () => {
  const summary = eventSummary({
    type: "event",
    seq: 10,
    code: "tool_complete",
    created_at: "2026-04-12T12:00:00Z",
    payload: {
      name: "search_rag_database",
      result_size: 84,
      result_blocks: [
        { source: "guide.pdf", pages: [4], page_label: "Page 4", text: "apt install foo" },
        { source: "guide.pdf", pages: [5], page_label: "Page 5", text: "systemctl restart ssh" },
      ],
    },
  });

  assert.equal(summary, "retrieval tool complete • 2 blocks • 84 chars");
});

test("keeps retrieval provider round events with retrieval tool calls", () => {
  const events = [
    {
      type: "event",
      seq: 1,
      code: "request_submitted",
      created_at: "2026-04-12T12:00:00Z",
      payload: { round: 2 },
    },
    {
      type: "event",
      seq: 2,
      code: "tool_calls_received",
      created_at: "2026-04-12T12:00:01Z",
      payload: {
        round: 2,
        count: 1,
        names: ["search_rag_database"],
      },
    },
    {
      type: "event",
      seq: 3,
      code: "tool_results_submitted",
      created_at: "2026-04-12T12:00:02Z",
      payload: { round: 2 },
    },
  ];

  assert.deepEqual(
    getRetrievalEvents(events).map((event) => event.code),
    ["request_submitted", "tool_calls_received", "tool_results_submitted"],
  );
});
