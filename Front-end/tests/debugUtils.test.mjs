import test from "node:test";
import assert from "node:assert/strict";

import { eventSummary, TAB_FILTERS } from "../.test-dist/src/debug/debugUtils.js";

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
