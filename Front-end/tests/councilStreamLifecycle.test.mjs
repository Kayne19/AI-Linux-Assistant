import test from "node:test";
import assert from "node:assert/strict";

import {
  getCouncilCompletionCatchupDelta,
  hasPendingCouncilWorkForChat,
  shouldDeferCouncilCompletion,
  takeReadyCouncilCompletion,
} from "../.test-dist/src/councilStreamLifecycle.js";

test("defers council completion while buffered delta text is still pending", () => {
  assert.equal(
    shouldDeferCouncilCompletion({ delta: "partial text", frameId: null }),
    true,
  );
});

test("defers council completion while a render frame is still scheduled", () => {
  assert.equal(
    shouldDeferCouncilCompletion({ delta: "", frameId: 42 }),
    true,
  );
});

test("allows council completion immediately once the batch has drained", () => {
  const completion = { role: "skeptic", phase: "discussion", text: "final answer" };

  assert.deepEqual(
    takeReadyCouncilCompletion(completion, undefined),
    completion,
  );
});

test("does not release pending council completion before the batch has drained", () => {
  const completion = { role: "eager", phase: "opening_arguments", text: "complete text" };

  assert.equal(
    takeReadyCouncilCompletion(completion, { delta: "", frameId: 1 }),
    null,
  );
  assert.equal(
    takeReadyCouncilCompletion(completion, { delta: "x", frameId: null }),
    null,
  );
});

test("treats pending council batches or deferred completions as in-flight chat work", () => {
  assert.equal(
    hasPendingCouncilWorkForChat(
      "chat-1",
      { "chat-1:discussion-skeptic-0": { delta: "", frameId: 7 } },
      {},
    ),
    true,
  );
  assert.equal(
    hasPendingCouncilWorkForChat(
      "chat-1",
      {},
      { "chat-1:discussion-skeptic-0": { text: "final" } },
    ),
    true,
  );
  assert.equal(
    hasPendingCouncilWorkForChat(
      "chat-1",
      { "chat-2:discussion-skeptic-0": { delta: "", frameId: 7 } },
      {},
    ),
    false,
  );
});

test("queues only the missing suffix when council completion is ahead of visible text", () => {
  assert.equal(
    getCouncilCompletionCatchupDelta(
      "Check disk usage",
      "Check disk usage before rotating logs again",
    ),
    " before rotating logs again",
  );
});

test("does not synthesize a catch-up delta when the visible text diverged", () => {
  assert.equal(
    getCouncilCompletionCatchupDelta(
      "Check inode usage",
      "Check disk usage before rotating logs again",
    ),
    "",
  );
});
