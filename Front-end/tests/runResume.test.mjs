import test from "node:test";
import assert from "node:assert/strict";

import {
  getResumeAfterSeq,
  mergeChatsPreservingActiveRunSnapshots,
  shouldReconcileDetachedRunUi,
} from "../.test-dist/src/runResume.js";

test("resumes from the highest durable sequence already seen", () => {
  assert.equal(getResumeAfterSeq(12, 18), 18);
  assert.equal(getResumeAfterSeq(22, 18), 22);
  assert.equal(getResumeAfterSeq(undefined, 0), 0);
});

test("reconciles detached run UI once backend no longer reports that run as active", () => {
  assert.equal(shouldReconcileDetachedRunUi("run-1", null, false), true);
  assert.equal(shouldReconcileDetachedRunUi("run-1", "run-2", false), true);
});

test("keeps live run UI intact while the controller is still attached or the run still matches", () => {
  assert.equal(shouldReconcileDetachedRunUi("run-1", "run-1", false), false);
  assert.equal(shouldReconcileDetachedRunUi("run-1", null, true), false);
  assert.equal(shouldReconcileDetachedRunUi("", null, false), false);
});

test("keeps paused run UI intact when a stale poll temporarily drops the active run id", () => {
  assert.equal(shouldReconcileDetachedRunUi("run-1", null, false, "paused"), false);
  assert.equal(shouldReconcileDetachedRunUi("run-1", null, false, "pause_requested"), false);
  assert.equal(shouldReconcileDetachedRunUi("run-1", "run-2", false, "paused"), true);
});

test("preserves a local paused run snapshot when refreshed chats omit the active run temporarily", () => {
  const currentChats = [
    { id: "chat-1", active_run_id: "run-1", active_run_status: "paused", title: "Chat 1" },
    { id: "chat-2", active_run_id: null, active_run_status: null, title: "Chat 2" },
  ];
  const nextChats = [
    { id: "chat-1", active_run_id: null, active_run_status: null, title: "Chat 1" },
    { id: "chat-2", active_run_id: null, active_run_status: null, title: "Chat 2" },
  ];

  assert.deepEqual(mergeChatsPreservingActiveRunSnapshots(currentChats, nextChats), [
    { id: "chat-1", active_run_id: "run-1", active_run_status: "paused", title: "Chat 1" },
    { id: "chat-2", active_run_id: null, active_run_status: null, title: "Chat 2" },
  ]);
});
