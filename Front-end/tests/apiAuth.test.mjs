import test from "node:test";
import assert from "node:assert/strict";

import {
  ApiError,
  clearApiAuth,
  configureApiAuth,
  getAuthorizationHeader,
  handleUnauthorizedStatus,
} from "../.test-dist/src/apiAuth.js";

test("getAuthorizationHeader returns a bearer token from the configured provider", async () => {
  configureApiAuth({
    getAccessToken: async () => "token-123",
  });

  await assert.deepEqual(await getAuthorizationHeader(true), {
    Authorization: "Bearer token-123",
  });
});

test("getAuthorizationHeader rejects when auth is required but no provider is configured", async () => {
  clearApiAuth();
  await assert.rejects(() => getAuthorizationHeader(true), (error) => {
    assert.equal(error instanceof ApiError, true);
    assert.equal(error.status, 401);
    return true;
  });
});

test("handleUnauthorizedStatus triggers the registered hard-401 handler", async () => {
  let unauthorizedCount = 0;
  configureApiAuth({
    getAccessToken: async () => "token-123",
    onUnauthorized: () => {
      unauthorizedCount += 1;
    },
  });

  await handleUnauthorizedStatus(401);
  await handleUnauthorizedStatus(500);

  assert.equal(unauthorizedCount, 1);
});
