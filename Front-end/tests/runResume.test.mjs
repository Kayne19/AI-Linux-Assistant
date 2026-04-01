import test from "node:test";
import assert from "node:assert/strict";

import {
  getResumeAfterSeq,
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
