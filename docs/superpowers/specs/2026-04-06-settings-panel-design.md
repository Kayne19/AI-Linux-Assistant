# Settings Panel — Design Spec

**Date:** 2026-04-06  
**Status:** Approved

## Overview

An admin-only settings panel that lets admin users view and edit the model, provider, and reasoning effort for each of the 17 configurable backend components at runtime — without a server restart. Changes take effect globally for all users on the next run.

---

## Goals

- Admin can change provider, model, and reasoning effort for any component from the UI
- Changes persist across restarts (DB-backed)
- Changes take effect on the next run for all users (no restart required)
- Non-admin users cannot access or see the settings panel
- `.env` / code defaults remain the fallback when no DB override exists

## Non-Goals

- Per-user model preferences
- Streaming or in-flight run reconfiguration
- Provider API key management
- Retrieval or memory policy configuration

---

## Data Model

### New table: `app_settings`

A singleton table — always exactly one row (`id = 1`). Columns are nullable; `NULL` means "use the default from `.env` / `settings.py`."

```sql
CREATE TABLE app_settings (
    id                              INTEGER PRIMARY KEY DEFAULT 1,
    -- Core pipeline
    classifier_provider             TEXT,
    classifier_model                TEXT,
    classifier_reasoning_effort     TEXT,
    contextualizer_provider         TEXT,
    contextualizer_model            TEXT,
    contextualizer_reasoning_effort TEXT,
    responder_provider              TEXT,
    responder_model                 TEXT,
    responder_reasoning_effort      TEXT,
    -- Magi full
    magi_eager_provider             TEXT,
    magi_eager_model                TEXT,
    magi_eager_reasoning_effort     TEXT,
    magi_skeptic_provider           TEXT,
    magi_skeptic_model              TEXT,
    magi_skeptic_reasoning_effort   TEXT,
    magi_historian_provider         TEXT,
    magi_historian_model            TEXT,
    magi_historian_reasoning_effort TEXT,
    magi_arbiter_provider           TEXT,
    magi_arbiter_model              TEXT,
    magi_arbiter_reasoning_effort   TEXT,
    -- Magi lite
    magi_lite_eager_provider             TEXT,
    magi_lite_eager_model                TEXT,
    magi_lite_eager_reasoning_effort     TEXT,
    magi_lite_skeptic_provider           TEXT,
    magi_lite_skeptic_model              TEXT,
    magi_lite_skeptic_reasoning_effort   TEXT,
    magi_lite_historian_provider         TEXT,
    magi_lite_historian_model            TEXT,
    magi_lite_historian_reasoning_effort TEXT,
    magi_lite_arbiter_provider           TEXT,
    magi_lite_arbiter_model              TEXT,
    magi_lite_arbiter_reasoning_effort   TEXT,
    -- Utility (advanced)
    history_summarizer_provider             TEXT,
    history_summarizer_model                TEXT,
    history_summarizer_reasoning_effort     TEXT,
    context_summarizer_provider             TEXT,
    context_summarizer_model                TEXT,
    context_summarizer_reasoning_effort     TEXT,
    memory_extractor_provider               TEXT,
    memory_extractor_model                  TEXT,
    memory_extractor_reasoning_effort       TEXT,
    registry_updater_provider               TEXT,
    registry_updater_model                  TEXT,
    registry_updater_reasoning_effort       TEXT,
    ingest_enricher_provider                TEXT,
    ingest_enricher_model                   TEXT,
    ingest_enricher_reasoning_effort        TEXT,
    chat_namer_provider                     TEXT,
    chat_namer_model                        TEXT,
    chat_namer_reasoning_effort             TEXT,
    -- Metadata
    updated_at                      TIMESTAMP,
    updated_by                      TEXT   -- Auth0 sub of the admin
);
```

A constraint ensures only one row exists: `CHECK (id = 1)`.

### Settings resolution order

For each field: **DB column value → `.env` override → `settings.py` hardcoded default**

A new function `load_effective_settings(db) -> AppSettings` implements this merge. It reads the single `app_settings` row (or returns defaults if the row doesn't exist), then merges non-null DB values over the baseline `SETTINGS` object field by field.

---

## Backend API

Both routes live in `Back-end/app/api.py` inside `create_app()`, gated with `Depends(_require_admin)`.

### `GET /admin/settings`

Returns the full effective settings for all 17 components — DB values merged with defaults.

**Response:**
```json
{
  "classifier":           {"provider": "openai",     "model": "gpt-5.4-mini",      "reasoning_effort": ""},
  "contextualizer":       {"provider": "openai",     "model": "gpt-5.4-mini",      "reasoning_effort": ""},
  "responder":            {"provider": "openai",     "model": "gpt-5.4",           "reasoning_effort": "low"},
  "history_summarizer":   {"provider": "openai",     "model": "gpt-5.4-mini",      "reasoning_effort": ""},
  "context_summarizer":   {"provider": "openai",     "model": "gpt-5.4-mini",      "reasoning_effort": ""},
  "memory_extractor":     {"provider": "openai",     "model": "gpt-5.4-mini",      "reasoning_effort": ""},
  "registry_updater":     {"provider": "local",      "model": "qwen2.5:7b",        "reasoning_effort": ""},
  "ingest_enricher":      {"provider": "openai",     "model": "gpt-5.4-nano",      "reasoning_effort": ""},
  "chat_namer":           {"provider": "openai",     "model": "gpt-5.4-nano",      "reasoning_effort": ""},
  "magi_eager":           {"provider": "openai",     "model": "gpt-5.4",           "reasoning_effort": "medium"},
  "magi_skeptic":         {"provider": "openai",     "model": "gpt-5.4",           "reasoning_effort": "high"},
  "magi_historian":       {"provider": "anthropic",  "model": "claude-sonnet-4-6", "reasoning_effort": "medium"},
  "magi_arbiter":         {"provider": "openai",     "model": "gpt-5.4",           "reasoning_effort": "high"},
  "magi_lite_eager":      {"provider": "openai",     "model": "gpt-5.4-mini",      "reasoning_effort": "low"},
  "magi_lite_skeptic":    {"provider": "openai",     "model": "gpt-5.4-mini",      "reasoning_effort": "medium"},
  "magi_lite_historian":  {"provider": "openai",     "model": "gpt-5.4-mini",      "reasoning_effort": "medium"},
  "magi_lite_arbiter":    {"provider": "openai",     "model": "gpt-5.4-mini",      "reasoning_effort": "low"}
}
```

### `PUT /admin/settings`

Accepts a partial patch — only include the components you want to change. Upserts the `app_settings` row, sets `updated_at` and `updated_by`, returns the full updated effective settings in the same shape as GET.

**Request body:**
```json
{
  "responder": {"provider": "anthropic", "model": "claude-opus-4-6", "reasoning_effort": "high"}
}
```

---

## Worker Integration

In `Back-end/app/chat_run_worker.py`, before constructing the `ModelRouter` for a claimed run:

1. Call `load_effective_settings(db)` to get a merged `AppSettings`
2. Pass it to the `ModelRouter` constructor

The `ModelRouter.__init__` is updated to accept an optional `settings: AppSettings` argument, defaulting to the global `SETTINGS` if not provided (preserves backward compatibility with tests and the TUI).

`load_effective_settings` lives in `Back-end/app/config/settings.py` alongside the existing `load_settings()` function.

---

## Frontend

### Entry point

A gear icon (⚙) button added to the `sidebar-footer-actions` div in `Sidebar.tsx`. Rendered only when the `isDebugMode` prop is true (i.e. `auth.user?.role === "admin"`). Clicking it calls an `onOpenSettings` callback passed down from `App.tsx`.

### New files

| File | Purpose |
|------|---------|
| `Front-end/src/components/dialogs/SettingsDialog.tsx` | Modal dialog UI |
| `Front-end/src/hooks/useSettings.ts` | Fetch/save state, follows `useProjects` pattern |

### Modified files

| File | Change |
|------|--------|
| `Front-end/src/App.tsx` | Settings open/close state, wire `onOpenSettings` to Sidebar |
| `Front-end/src/components/Sidebar.tsx` | Add settings gear button to footer |
| `Front-end/src/api.ts` | Add `getSettings()` and `updateSettings(patch)` |
| `Front-end/src/types.ts` | Add `ComponentSettings` and `AppSettingsConfig` types |

### SettingsDialog layout

Two tabs: **Core & Magi** (primary) and **Advanced** (secondary).

**Core & Magi tab** — three sections:

1. *Core pipeline* — classifier, contextualizer, responder
2. *Magi (full)* — eager, skeptic, historian, arbiter
3. *Magi lite* — eager, skeptic, historian, arbiter

**Advanced tab** — one section:

- history_summarizer, context_summarizer, memory_extractor, registry_updater, ingest_enricher, chat_namer

**Each component row:**

- Label (e.g. "Responder")
- Provider dropdown: `openai | anthropic | local`
- Model combobox: provider-scoped known model list as options, free-text entry allowed
- Reasoning effort dropdown: `none | low | medium | high`
- Subtle "default" badge when the value matches the system default

**Known model lists (by provider):**

- `openai`: gpt-5.4, gpt-5.4-mini, gpt-5.4-nano
- `anthropic`: claude-opus-4-6, claude-sonnet-4-6, claude-haiku-4-5-20251001
- `local`: qwen2.5:7b (plus free-text)

**Save behavior:** Save button submits a diff of only changed fields. On success, the dialog shows a brief confirmation. On error, an inline error message is shown without closing the dialog.

### useSettings hook

```ts
interface UseSettingsResult {
  settings: AppSettingsConfig | null;
  loading: boolean;
  error: string | null;
  save: (patch: Partial<AppSettingsConfig>) => Promise<void>;
  saving: boolean;
}
```

Loads on dialog open via `useEffect`. Holds local draft state for the form; submits only changed fields on save.

---

## Migration

A new Alembic migration creates the `app_settings` table and inserts the initial singleton row with all columns null (full default fallback).

---

## Files to create or modify

**Backend:**
- `Back-end/alembic/versions/<hash>_add_app_settings.py` — new migration
- `Back-end/app/config/settings.py` — add `load_effective_settings(db)`
- `Back-end/app/orchestration/model_router.py` — accept optional `settings` arg
- `Back-end/app/chat_run_worker.py` — call `load_effective_settings` before router construction
- `Back-end/app/api.py` — add `GET /admin/settings` and `PUT /admin/settings`
- `Back-end/app/persistence/postgres_models.py` — add `AppSettingsModel`

**Frontend:**
- `Front-end/src/components/dialogs/SettingsDialog.tsx` — new
- `Front-end/src/hooks/useSettings.ts` — new
- `Front-end/src/App.tsx` — settings open state + wiring
- `Front-end/src/components/Sidebar.tsx` — gear button in footer
- `Front-end/src/api.ts` — getSettings / updateSettings
- `Front-end/src/types.ts` — AppSettingsConfig / ComponentSettings types

---

## Error handling

- `GET /admin/settings` returns 403 for non-admin users
- `PUT /admin/settings` validates that provider is one of `openai | anthropic | local`; returns 422 for invalid values
- `PUT /admin/settings` validates reasoning_effort is one of `"" | low | medium | high`; returns 422 otherwise. The UI "none" option maps to `""` in the API/DB. `NULL` in the DB means "fall back to code default"; `""` stored explicitly means "no reasoning effort."
- Frontend shows inline error on save failure without closing the dialog
- If `load_effective_settings` fails (DB error), the worker falls back to the global `SETTINGS` object and logs a warning — runs are not blocked

## Testing

- Unit test `load_effective_settings`: null columns resolve to defaults, non-null columns override
- API test: non-admin GET/PUT returns 403; admin GET returns full settings shape; admin PUT with partial patch updates only specified fields
- Frontend: no new tests required at this stage (settings dialog is admin tooling, not critical user path)
