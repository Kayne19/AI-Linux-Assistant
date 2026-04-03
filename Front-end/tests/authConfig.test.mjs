import test from "node:test";
import assert from "node:assert/strict";

import { readAuth0Config } from "../.test-dist/src/authConfig.js";

test("readAuth0Config returns a usable Auth0 config when all required env vars are present", () => {
  const result = readAuth0Config({
    VITE_AUTH0_DOMAIN: "tenant.example.com",
    VITE_AUTH0_CLIENT_ID: "client-id",
    VITE_AUTH0_AUDIENCE: "https://api.example.com",
    VITE_AUTH0_REDIRECT_URI: "http://localhost:5173",
  });

  assert.equal(result.error, "");
  assert.deepEqual(result.config, {
    domain: "tenant.example.com",
    clientId: "client-id",
    audience: "https://api.example.com",
    redirectUri: "http://localhost:5173",
  });
});

test("readAuth0Config reports missing required variables", () => {
  const result = readAuth0Config({
    VITE_AUTH0_DOMAIN: "",
    VITE_AUTH0_CLIENT_ID: "client-id",
    VITE_AUTH0_AUDIENCE: "",
  });

  assert.equal(result.config, null);
  assert.match(result.error, /VITE_AUTH0_DOMAIN/);
  assert.match(result.error, /VITE_AUTH0_AUDIENCE/);
});
