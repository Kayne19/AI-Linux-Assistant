# Authentication

This document describes the current user-authentication model for the web app.

## Purpose

The web app now uses Auth0 for authentication.

The goals are:

- backend-verified identity
- server-enforced ownership and authorization
- browser-compatible authenticated streaming
- a clear migration boundary from the old fake username bootstrap flow

CLI/TUI is not part of this web-auth flow.

## Current Model

Web authentication is split like this:

- React handles sign-in with Auth0 Universal Login
- the frontend requests Auth0 access tokens for the backend API audience
- FastAPI validates bearer access tokens
- the backend maps Auth0 `sub` to a local `users` row
- local DB `role` remains the authorization source for admin/debug privileges

Important:

- API requests use Auth0 access tokens, not ID tokens
- the frontend never sends `user_id` as authority
- ownership is enforced server-side on projects, chats, messages, runs, and run events

## Frontend Auth Flow

Relevant files:

- `Front-end/src/main.tsx`
- `Front-end/src/hooks/useAuth.ts`
- `Front-end/src/apiAuth.ts`
- `Front-end/src/authConfig.ts`
- `Front-end/src/api.ts`
- `Front-end/src/components/LoginScreen.tsx`

Current flow:

1. `Auth0Provider` boots in the browser.
2. The app shows a branded pre-auth screen (`LoginScreen`) with a "Sign in" CTA and a "Create an account" secondary link.
3. "Sign in" calls `loginWithRedirect({ screen_hint: "login" })`. "Create an account" calls `loginWithRedirect({ screen_hint: "signup" })`. Both redirect to Auth0 Universal Login.
4. After redirect back, the Auth0 SDK restores session state. The frontend calls `getAccessTokenSilently()` for the backend API audience.
5. The frontend calls `GET /app/bootstrap` through the authenticated API client.

No credentials are collected in the React app. No Resource Owner Password Grant. Authorization Code + PKCE via Auth0 Universal Login only.

Token handling rules:

- bearer tokens stay in the Auth0 React SDK flow and the in-memory frontend auth helper
- bearer tokens are not placed in query params
- bearer tokens are not stored in `localStorage`

## Backend Auth Enforcement

Relevant files:

- `Back-end/app/auth/auth0.py`
- `Back-end/app/api.py`
- `Back-end/app/persistence/postgres_app_store.py`
- `Back-end/app/persistence/postgres_run_store.py`

FastAPI auth enforcement:

- every normal web API route depends on the current authenticated user
- the backend requires `Authorization: Bearer <access_token>`
- Auth0 access tokens are validated with:
  - RS256
  - JWKS
  - issuer
  - audience
  - expiry
  - signature

Identity mapping rules:

- web users are keyed locally by `(auth_provider, auth_subject)`
- `email`, `display_name`, and `avatar_url` are synced profile fields only
- local `role` is still the authorization source for admin/debug actions
- existing fake-auth users are not auto-linked

Eval-harness note:

- the harness can call the public backend API with copied Auth0 user access tokens
- those tokens are still sent as normal `Authorization: Bearer ...` headers
- this is preferable to enabling the legacy bootstrap path on a public deployment

## Protected Routes

Normal authenticated routes:

- `GET /auth/me`
- `GET /app/bootstrap`
- `GET /projects`
- `POST /projects`
- `GET /projects/{project_id}`
- `PATCH /projects/{project_id}`
- `DELETE /projects/{project_id}`
- `GET /projects/{project_id}/chats`
- `POST /projects/{project_id}/chats`
- `GET /chats/{chat_session_id}`
- `PATCH /chats/{chat_session_id}`
- `DELETE /chats/{chat_session_id}`
- `GET /chats/{chat_session_id}/messages`
- `GET /chats/{chat_session_id}/runs`
- `POST /chats/{chat_session_id}/runs`
- `POST /chats/{chat_session_id}/messages`
- `POST /chats/{chat_session_id}/messages/stream`
- `GET /runs/{run_id}`
- `GET /runs/{run_id}/events`
- `GET /runs/{run_id}/events/stream`
- `POST /runs/{run_id}/cancel`
- `POST /runs/{run_id}/pause`
- `POST /runs/{run_id}/resume`

Admin-only routes:

- `POST /runs/{run_id}/fail`
- `POST /runs/{run_id}/requeue`

Still unauthenticated:

- `GET /health`

## Streaming Auth

The browser does not use native `EventSource` for authenticated streaming.

Instead:

- the frontend uses `fetch`
- the response body is consumed through `ReadableStream`
- the `Authorization` header is attached on both:
  - `GET /runs/{run_id}/events/stream`
  - `POST /chats/{chat_session_id}/messages/stream`

This keeps bearer auth browser-compatible without putting tokens in the URL.

## Environment Variables

Backend:

- `AUTH0_DOMAIN`
- `AUTH0_ISSUER`
- `AUTH0_AUDIENCE`
- `FRONTEND_ORIGIN` or `FRONTEND_ORIGINS`
- optional `AUTH0_ENABLED`
- optional `AUTH0_JWKS_TTL_SECONDS`
- optional `ENABLE_LEGACY_BOOTSTRAP_AUTH`

Frontend:

- `VITE_AUTH0_DOMAIN`
- `VITE_AUTH0_CLIENT_ID`
- `VITE_AUTH0_AUDIENCE`
- `VITE_API_BASE_URL`
- optional `VITE_AUTH0_REDIRECT_URI`

Recommended local-dev split:

- `Back-end/.env` for backend env vars
- `Front-end/.env` or `Front-end/.env.local` for Vite env vars

Recommended public API additions:

- keep `ENABLE_LEGACY_BOOTSTRAP_AUTH=false`
- include both `http://localhost:5173` and `https://app.<your-domain>` in `FRONTEND_ORIGINS` if you plan to expose the frontend later

## Auth0 Dashboard Setup

You need:

1. A SPA application
2. An API resource for the backend audience

SPA application:

- Application Type: `Single Page Application`
- Allowed Callback URLs:
  - `http://localhost:5173`
  - `http://localhost:5173/`
- Allowed Logout URLs:
  - `http://localhost:5173`
  - `http://localhost:5173/`
- Allowed Web Origins:
  - `http://localhost:5173`

API:

- Identifier must match `AUTH0_AUDIENCE` and `VITE_AUTH0_AUDIENCE`
- Signing Algorithm: `RS256`

If using a Cloudflare Tunnel or another HTTPS host for testers, add that exact HTTPS origin to the SPA application settings too.

## Secure-Origin Rule

Auth0 SPA auth requires:

- `http://localhost...`
- or `https://...`

It will fail on insecure LAN origins such as:

- `http://192.168.x.x:5173`

For remote development:

- use SSH port forwarding and open the app at `http://localhost:5173`
- or expose the frontend through HTTPS, such as a Cloudflare Tunnel

For a public API-only deployment:

- expose the backend separately at `https://api.<your-domain>`
- do not expose the frontend until `https://app.<your-domain>` is ready and registered in Auth0

## Auth0 Dashboard Branding

The Auth0 Universal Login page is the actual credential-entry surface. It can be customized to match this app's look without implementing embedded login.

### Minimum setup (recommended)

Go to **Auth0 Dashboard → Branding → Universal Login**:

1. **Logo** — upload a square PNG/SVG of the product logo. Appears in the hosted login box.
2. **Primary color** — set to `#7b68ee` (the app's accent color).
3. **Background color** — set to `#0a0b10` (the app's dark background).
4. **Page background color** — same dark value, or a very slightly lighter tone so the login card reads against it.
5. **Favicon** — optional, upload the same logo asset.

### Universal Login version

Stay on **New Universal Login** (the default). Do not switch to Classic Login. New Universal Login supports:

- the `screen_hint` parameter (`login` vs `signup` routing from the app)
- better branding support
- PKCE flows

### Text / copy customization

Go to **Auth0 Dashboard → Branding → Universal Login → Advanced Options → Custom Text**:

- You can override the login page heading, subtext, and button labels per locale.
- Useful overrides: set the login heading to match your product voice (e.g. "Sign in to AI Linux Assistant").

### Template-level customization (optional, more effort)

If you want pixel-level control over the hosted page layout, Auth0 supports a **Custom Login Page** template under **New Universal Login → Advanced**. This lets you add custom CSS to the hosted page. The login widget itself (the form, buttons) remains Auth0-controlled. You can change:

- font family (match `var(--sans)` — Geist or Inter)
- card background, border, border-radius
- input field styles
- button styles

Avoid touching JavaScript on the hosted page — keep form logic Auth0-owned.

### What NOT to do

- Do not switch to Classic Login — it does not support `screen_hint` and has a worse security model.
- Do not implement embedded password collection in the React app.
- Do not enable Resource Owner Password Grant — it is not needed and it weakens the auth model.

---

## Legacy Auth Audit Note

The old fake username bootstrap path still exists only as a dev-only compatibility path behind:

- `ENABLE_LEGACY_BOOTSTRAP_AUTH=false`

Legacy routes:

- `POST /auth/login`
- `POST /auth/bootstrap`

These remain intentionally easy to audit and remove later.

Useful grep:

```bash
rg "ENABLE_LEGACY_BOOTSTRAP_AUTH|/auth/login|/auth/bootstrap|legacy"
```
