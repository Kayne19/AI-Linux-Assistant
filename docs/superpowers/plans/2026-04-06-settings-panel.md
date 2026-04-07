# Settings Panel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an admin-only settings panel that lets admins view and edit the model, provider, and reasoning effort for all 17 backend components at runtime, persisted in Postgres.

**Architecture:** A singleton `app_settings` Postgres table stores per-component overrides (NULL = use .env/code default). A `load_effective_settings()` function merges DB overrides over `SETTINGS` defaults and is called by the worker before constructing each `ModelRouter`. Two admin-gated FastAPI endpoints (`GET`/`PUT /admin/settings`) expose read/write. A React modal (admin-only, gear icon in sidebar footer) uses a `useSettings` hook to fetch and save settings, with two tabs: Core & Magi (primary) and Advanced (secondary).

**Tech Stack:** Python 3.10+, SQLAlchemy, Alembic, FastAPI, Pydantic (backend); React 18, TypeScript (frontend); existing dialog/hook/CSS patterns throughout.

---

## File Map

**Backend — create:**
- `Back-end/alembic/versions/20260406_0005_add_app_settings.py`
- `Back-end/tests/test_load_effective_settings.py`
- `Back-end/tests/test_admin_settings_api.py`

**Backend — modify:**
- `Back-end/app/persistence/postgres_models.py` — add `AppSettingsModel`
- `Back-end/app/config/settings.py` — add `_load_settings_row`, `_apply_db_overrides`, `load_effective_settings`
- `Back-end/app/api.py` — add Pydantic request/response models + `GET`/`PUT /admin/settings` routes
- `Back-end/app/chat_run_worker.py` — pass `settings=load_effective_settings()` to `ModelRouter`

**Frontend — create:**
- `Front-end/src/hooks/useSettings.ts`
- `Front-end/src/components/dialogs/SettingsDialog.tsx`

**Frontend — modify:**
- `Front-end/src/types.ts` — add `COMPONENT_KEYS`, `ComponentKey`, `ComponentSettings`, `AppSettingsConfig`, `AppSettingsPatch`, `ComponentSettingsPatch`
- `Front-end/src/api.ts` — add `getSettings`, `updateSettings`
- `Front-end/src/App.tsx` — settings open state, `useSettings`, `SettingsDialog` render
- `Front-end/src/components/Sidebar.tsx` — gear button + `onOpenSettings` prop

---

### Task 1: AppSettings SQLAlchemy model

**Files:**
- Modify: `Back-end/app/persistence/postgres_models.py`

- [ ] **Step 1: Add `CheckConstraint` to the SQLAlchemy import and append `AppSettingsModel`**

In `Back-end/app/persistence/postgres_models.py`, add `CheckConstraint` to the existing import block:

```python
from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,   # ← add this line
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
```

Then append the following class at the bottom of the file (after the last existing model class):

```python
class AppSettingsModel(Base):
    """Singleton settings table. Always has exactly one row with id=1.
    NULL columns mean 'use the code/env default'. Non-null columns override the default."""
    __tablename__ = "app_settings"
    __table_args__ = (CheckConstraint("id = 1", name="app_settings_singleton"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    # Core pipeline
    classifier_provider: Mapped[str | None] = mapped_column(Text, nullable=True)
    classifier_model: Mapped[str | None] = mapped_column(Text, nullable=True)
    classifier_reasoning_effort: Mapped[str | None] = mapped_column(Text, nullable=True)
    contextualizer_provider: Mapped[str | None] = mapped_column(Text, nullable=True)
    contextualizer_model: Mapped[str | None] = mapped_column(Text, nullable=True)
    contextualizer_reasoning_effort: Mapped[str | None] = mapped_column(Text, nullable=True)
    responder_provider: Mapped[str | None] = mapped_column(Text, nullable=True)
    responder_model: Mapped[str | None] = mapped_column(Text, nullable=True)
    responder_reasoning_effort: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Magi full
    magi_eager_provider: Mapped[str | None] = mapped_column(Text, nullable=True)
    magi_eager_model: Mapped[str | None] = mapped_column(Text, nullable=True)
    magi_eager_reasoning_effort: Mapped[str | None] = mapped_column(Text, nullable=True)
    magi_skeptic_provider: Mapped[str | None] = mapped_column(Text, nullable=True)
    magi_skeptic_model: Mapped[str | None] = mapped_column(Text, nullable=True)
    magi_skeptic_reasoning_effort: Mapped[str | None] = mapped_column(Text, nullable=True)
    magi_historian_provider: Mapped[str | None] = mapped_column(Text, nullable=True)
    magi_historian_model: Mapped[str | None] = mapped_column(Text, nullable=True)
    magi_historian_reasoning_effort: Mapped[str | None] = mapped_column(Text, nullable=True)
    magi_arbiter_provider: Mapped[str | None] = mapped_column(Text, nullable=True)
    magi_arbiter_model: Mapped[str | None] = mapped_column(Text, nullable=True)
    magi_arbiter_reasoning_effort: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Magi lite
    magi_lite_eager_provider: Mapped[str | None] = mapped_column(Text, nullable=True)
    magi_lite_eager_model: Mapped[str | None] = mapped_column(Text, nullable=True)
    magi_lite_eager_reasoning_effort: Mapped[str | None] = mapped_column(Text, nullable=True)
    magi_lite_skeptic_provider: Mapped[str | None] = mapped_column(Text, nullable=True)
    magi_lite_skeptic_model: Mapped[str | None] = mapped_column(Text, nullable=True)
    magi_lite_skeptic_reasoning_effort: Mapped[str | None] = mapped_column(Text, nullable=True)
    magi_lite_historian_provider: Mapped[str | None] = mapped_column(Text, nullable=True)
    magi_lite_historian_model: Mapped[str | None] = mapped_column(Text, nullable=True)
    magi_lite_historian_reasoning_effort: Mapped[str | None] = mapped_column(Text, nullable=True)
    magi_lite_arbiter_provider: Mapped[str | None] = mapped_column(Text, nullable=True)
    magi_lite_arbiter_model: Mapped[str | None] = mapped_column(Text, nullable=True)
    magi_lite_arbiter_reasoning_effort: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Utility (advanced)
    history_summarizer_provider: Mapped[str | None] = mapped_column(Text, nullable=True)
    history_summarizer_model: Mapped[str | None] = mapped_column(Text, nullable=True)
    history_summarizer_reasoning_effort: Mapped[str | None] = mapped_column(Text, nullable=True)
    context_summarizer_provider: Mapped[str | None] = mapped_column(Text, nullable=True)
    context_summarizer_model: Mapped[str | None] = mapped_column(Text, nullable=True)
    context_summarizer_reasoning_effort: Mapped[str | None] = mapped_column(Text, nullable=True)
    memory_extractor_provider: Mapped[str | None] = mapped_column(Text, nullable=True)
    memory_extractor_model: Mapped[str | None] = mapped_column(Text, nullable=True)
    memory_extractor_reasoning_effort: Mapped[str | None] = mapped_column(Text, nullable=True)
    registry_updater_provider: Mapped[str | None] = mapped_column(Text, nullable=True)
    registry_updater_model: Mapped[str | None] = mapped_column(Text, nullable=True)
    registry_updater_reasoning_effort: Mapped[str | None] = mapped_column(Text, nullable=True)
    ingest_enricher_provider: Mapped[str | None] = mapped_column(Text, nullable=True)
    ingest_enricher_model: Mapped[str | None] = mapped_column(Text, nullable=True)
    ingest_enricher_reasoning_effort: Mapped[str | None] = mapped_column(Text, nullable=True)
    chat_namer_provider: Mapped[str | None] = mapped_column(Text, nullable=True)
    chat_namer_model: Mapped[str | None] = mapped_column(Text, nullable=True)
    chat_namer_reasoning_effort: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Metadata
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_by: Mapped[str | None] = mapped_column(Text, nullable=True)
```

- [ ] **Step 2: Commit**

```bash
git add Back-end/app/persistence/postgres_models.py
git commit -m "feat: add AppSettingsModel singleton table"
```

---

### Task 2: Alembic migration

**Files:**
- Create: `Back-end/alembic/versions/20260406_0005_add_app_settings.py`

- [ ] **Step 1: Create the migration file**

```python
"""add app_settings singleton table

Revision ID: 0005
Revises: 0004
Create Date: 2026-04-06

"""
from alembic import op
import sqlalchemy as sa

revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "app_settings",
        sa.Column("id", sa.Integer(), nullable=False),
        # Core pipeline
        sa.Column("classifier_provider", sa.Text(), nullable=True),
        sa.Column("classifier_model", sa.Text(), nullable=True),
        sa.Column("classifier_reasoning_effort", sa.Text(), nullable=True),
        sa.Column("contextualizer_provider", sa.Text(), nullable=True),
        sa.Column("contextualizer_model", sa.Text(), nullable=True),
        sa.Column("contextualizer_reasoning_effort", sa.Text(), nullable=True),
        sa.Column("responder_provider", sa.Text(), nullable=True),
        sa.Column("responder_model", sa.Text(), nullable=True),
        sa.Column("responder_reasoning_effort", sa.Text(), nullable=True),
        # Magi full
        sa.Column("magi_eager_provider", sa.Text(), nullable=True),
        sa.Column("magi_eager_model", sa.Text(), nullable=True),
        sa.Column("magi_eager_reasoning_effort", sa.Text(), nullable=True),
        sa.Column("magi_skeptic_provider", sa.Text(), nullable=True),
        sa.Column("magi_skeptic_model", sa.Text(), nullable=True),
        sa.Column("magi_skeptic_reasoning_effort", sa.Text(), nullable=True),
        sa.Column("magi_historian_provider", sa.Text(), nullable=True),
        sa.Column("magi_historian_model", sa.Text(), nullable=True),
        sa.Column("magi_historian_reasoning_effort", sa.Text(), nullable=True),
        sa.Column("magi_arbiter_provider", sa.Text(), nullable=True),
        sa.Column("magi_arbiter_model", sa.Text(), nullable=True),
        sa.Column("magi_arbiter_reasoning_effort", sa.Text(), nullable=True),
        # Magi lite
        sa.Column("magi_lite_eager_provider", sa.Text(), nullable=True),
        sa.Column("magi_lite_eager_model", sa.Text(), nullable=True),
        sa.Column("magi_lite_eager_reasoning_effort", sa.Text(), nullable=True),
        sa.Column("magi_lite_skeptic_provider", sa.Text(), nullable=True),
        sa.Column("magi_lite_skeptic_model", sa.Text(), nullable=True),
        sa.Column("magi_lite_skeptic_reasoning_effort", sa.Text(), nullable=True),
        sa.Column("magi_lite_historian_provider", sa.Text(), nullable=True),
        sa.Column("magi_lite_historian_model", sa.Text(), nullable=True),
        sa.Column("magi_lite_historian_reasoning_effort", sa.Text(), nullable=True),
        sa.Column("magi_lite_arbiter_provider", sa.Text(), nullable=True),
        sa.Column("magi_lite_arbiter_model", sa.Text(), nullable=True),
        sa.Column("magi_lite_arbiter_reasoning_effort", sa.Text(), nullable=True),
        # Utility (advanced)
        sa.Column("history_summarizer_provider", sa.Text(), nullable=True),
        sa.Column("history_summarizer_model", sa.Text(), nullable=True),
        sa.Column("history_summarizer_reasoning_effort", sa.Text(), nullable=True),
        sa.Column("context_summarizer_provider", sa.Text(), nullable=True),
        sa.Column("context_summarizer_model", sa.Text(), nullable=True),
        sa.Column("context_summarizer_reasoning_effort", sa.Text(), nullable=True),
        sa.Column("memory_extractor_provider", sa.Text(), nullable=True),
        sa.Column("memory_extractor_model", sa.Text(), nullable=True),
        sa.Column("memory_extractor_reasoning_effort", sa.Text(), nullable=True),
        sa.Column("registry_updater_provider", sa.Text(), nullable=True),
        sa.Column("registry_updater_model", sa.Text(), nullable=True),
        sa.Column("registry_updater_reasoning_effort", sa.Text(), nullable=True),
        sa.Column("ingest_enricher_provider", sa.Text(), nullable=True),
        sa.Column("ingest_enricher_model", sa.Text(), nullable=True),
        sa.Column("ingest_enricher_reasoning_effort", sa.Text(), nullable=True),
        sa.Column("chat_namer_provider", sa.Text(), nullable=True),
        sa.Column("chat_namer_model", sa.Text(), nullable=True),
        sa.Column("chat_namer_reasoning_effort", sa.Text(), nullable=True),
        # Metadata
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_by", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("id = 1", name="app_settings_singleton"),
    )
    # Pre-insert the singleton row so load_effective_settings always finds it
    op.execute("INSERT INTO app_settings (id) VALUES (1)")


def downgrade():
    op.drop_table("app_settings")
```

- [ ] **Step 2: Run migration and verify**

```bash
cd Back-end && conda run -n AI-Linux-Assistant alembic upgrade head
```

Expected: migration applies with no errors. Verify with:

```bash
conda run -n AI-Linux-Assistant python -c "
from persistence.database import get_session_factory
from persistence.postgres_models import AppSettingsModel
sf = get_session_factory()
with sf() as s:
    row = s.get(AppSettingsModel, 1)
    print('row exists:', row is not None)
    print('classifier_provider:', row.classifier_provider)
"
```

Expected output:
```
row exists: True
classifier_provider: None
```

- [ ] **Step 3: Commit**

```bash
git add Back-end/alembic/versions/20260406_0005_add_app_settings.py
git commit -m "feat: migration 0005 — add app_settings singleton table"
```

---

### Task 3: load_effective_settings function

**Files:**
- Modify: `Back-end/app/config/settings.py`
- Create: `Back-end/tests/test_load_effective_settings.py`

- [ ] **Step 1: Write the failing tests**

Create `Back-end/tests/test_load_effective_settings.py`:

```python
"""Tests for load_effective_settings and _apply_db_overrides."""
from unittest.mock import patch

from config.settings import SETTINGS, _apply_db_overrides, load_effective_settings


class FakeRow:
    """Mock for AppSettingsModel with all columns null by default."""
    def __init__(self, **kwargs):
        for col in [
            "classifier_provider", "classifier_model", "classifier_reasoning_effort",
            "contextualizer_provider", "contextualizer_model", "contextualizer_reasoning_effort",
            "responder_provider", "responder_model", "responder_reasoning_effort",
            "magi_eager_provider", "magi_eager_model", "magi_eager_reasoning_effort",
            "magi_skeptic_provider", "magi_skeptic_model", "magi_skeptic_reasoning_effort",
            "magi_historian_provider", "magi_historian_model", "magi_historian_reasoning_effort",
            "magi_arbiter_provider", "magi_arbiter_model", "magi_arbiter_reasoning_effort",
            "magi_lite_eager_provider", "magi_lite_eager_model", "magi_lite_eager_reasoning_effort",
            "magi_lite_skeptic_provider", "magi_lite_skeptic_model", "magi_lite_skeptic_reasoning_effort",
            "magi_lite_historian_provider", "magi_lite_historian_model", "magi_lite_historian_reasoning_effort",
            "magi_lite_arbiter_provider", "magi_lite_arbiter_model", "magi_lite_arbiter_reasoning_effort",
            "history_summarizer_provider", "history_summarizer_model", "history_summarizer_reasoning_effort",
            "context_summarizer_provider", "context_summarizer_model", "context_summarizer_reasoning_effort",
            "memory_extractor_provider", "memory_extractor_model", "memory_extractor_reasoning_effort",
            "registry_updater_provider", "registry_updater_model", "registry_updater_reasoning_effort",
            "ingest_enricher_provider", "ingest_enricher_model", "ingest_enricher_reasoning_effort",
            "chat_namer_provider", "chat_namer_model", "chat_namer_reasoning_effort",
        ]:
            setattr(self, col, None)
        for key, val in kwargs.items():
            setattr(self, key, val)


def test_all_null_returns_base_defaults():
    row = FakeRow()
    result = _apply_db_overrides(SETTINGS, row)
    assert result.classifier.provider == SETTINGS.classifier.provider
    assert result.classifier.model == SETTINGS.classifier.model
    assert result.responder.provider == SETTINGS.responder.provider
    assert result.magi_arbiter.reasoning_effort == SETTINGS.magi_arbiter.reasoning_effort


def test_provider_and_model_override():
    row = FakeRow(responder_provider="anthropic", responder_model="claude-opus-4-6")
    result = _apply_db_overrides(SETTINGS, row)
    assert result.responder.provider == "anthropic"
    assert result.responder.model == "claude-opus-4-6"
    # Non-overridden field stays at base
    assert result.responder.reasoning_effort == SETTINGS.responder.reasoning_effort


def test_empty_string_reasoning_effort_is_valid_override():
    """Empty string means 'no reasoning effort' — distinct from NULL which means 'use default'."""
    row = FakeRow(responder_reasoning_effort="")
    result = _apply_db_overrides(SETTINGS, row)
    assert result.responder.reasoning_effort == ""


def test_null_reasoning_effort_keeps_base_default():
    row = FakeRow()  # responder_reasoning_effort is None
    result = _apply_db_overrides(SETTINGS, row)
    assert result.responder.reasoning_effort == SETTINGS.responder.reasoning_effort


def test_magi_lite_override():
    row = FakeRow(magi_lite_skeptic_model="gpt-5.4", magi_lite_skeptic_provider="openai")
    result = _apply_db_overrides(SETTINGS, row)
    assert result.magi_lite_skeptic.model == "gpt-5.4"
    assert result.magi_lite_skeptic.provider == "openai"


def test_load_effective_settings_fallback_on_db_error():
    with patch("config.settings._load_settings_row", side_effect=RuntimeError("db down")):
        result = load_effective_settings()
    assert result is SETTINGS


def test_load_effective_settings_applies_overrides():
    row = FakeRow(magi_arbiter_model="claude-opus-4-6", magi_arbiter_provider="anthropic")
    with patch("config.settings._load_settings_row", return_value=row):
        result = load_effective_settings()
    assert result.magi_arbiter.model == "claude-opus-4-6"
    assert result.magi_arbiter.provider == "anthropic"
```

- [ ] **Step 2: Run tests — confirm they fail**

```bash
cd Back-end && conda run -n AI-Linux-Assistant python -m pytest tests/test_load_effective_settings.py -v
```

Expected: `ImportError` — `_apply_db_overrides` and `load_effective_settings` are not yet defined.

- [ ] **Step 3: Implement the three functions in settings.py**

Append the following to the end of `Back-end/app/config/settings.py` (after the `SETTINGS = load_settings()` line):

```python
def _load_settings_row():
    """Fetch the singleton app_settings row. Returns None if absent or on DB error."""
    try:
        from persistence.database import get_session_factory
        from persistence.postgres_models import AppSettingsModel
        session_factory = get_session_factory()
        with session_factory() as session:
            return session.get(AppSettingsModel, 1)
    except Exception:
        import logging
        logging.getLogger(__name__).warning(
            "Failed to load app_settings row from DB.", exc_info=True
        )
        return None


def _apply_db_overrides(base: AppSettings, row) -> AppSettings:
    """Return a new AppSettings with non-null DB values overriding base fields.

    Resolution order per field: DB value → base value.
    NULL in DB means 'use base'. Empty string ("") in DB means 'no reasoning effort'.
    """
    def _role(base_role: RoleModelSettings, prefix: str) -> RoleModelSettings:
        provider = getattr(row, f"{prefix}_provider", None) or base_role.provider
        model = getattr(row, f"{prefix}_model", None) or base_role.model
        effort_raw = getattr(row, f"{prefix}_reasoning_effort", None)
        effort = effort_raw if effort_raw is not None else base_role.reasoning_effort
        return RoleModelSettings(provider=provider, model=model, reasoning_effort=effort)

    return AppSettings(
        provider_defaults=base.provider_defaults,
        classifier=_role(base.classifier, "classifier"),
        contextualizer=_role(base.contextualizer, "contextualizer"),
        responder=_role(base.responder, "responder"),
        history_summarizer=_role(base.history_summarizer, "history_summarizer"),
        context_summarizer=_role(base.context_summarizer, "context_summarizer"),
        memory_extractor=_role(base.memory_extractor, "memory_extractor"),
        registry_updater=_role(base.registry_updater, "registry_updater"),
        ingest_enricher=_role(base.ingest_enricher, "ingest_enricher"),
        chat_namer=_role(base.chat_namer, "chat_namer"),
        magi_eager=_role(base.magi_eager, "magi_eager"),
        magi_skeptic=_role(base.magi_skeptic, "magi_skeptic"),
        magi_historian=_role(base.magi_historian, "magi_historian"),
        magi_arbiter=_role(base.magi_arbiter, "magi_arbiter"),
        magi_lite_eager=_role(base.magi_lite_eager, "magi_lite_eager"),
        magi_lite_skeptic=_role(base.magi_lite_skeptic, "magi_lite_skeptic"),
        magi_lite_historian=_role(base.magi_lite_historian, "magi_lite_historian"),
        magi_lite_arbiter=_role(base.magi_lite_arbiter, "magi_lite_arbiter"),
        # Pass all non-model fields through unchanged
        response_tool_rounds=base.response_tool_rounds,
        classifier_temperature=base.classifier_temperature,
        contextualizer_temperature=base.contextualizer_temperature,
        history_summarizer_temperature=base.history_summarizer_temperature,
        history_max_recent_turns=base.history_max_recent_turns,
        history_summarize_turn_threshold=base.history_summarize_turn_threshold,
        history_summarize_char_threshold=base.history_summarize_char_threshold,
        magi_max_discussion_rounds=base.magi_max_discussion_rounds,
        magi_lite_max_discussion_rounds=base.magi_lite_max_discussion_rounds,
        max_active_runs_per_user_default=base.max_active_runs_per_user_default,
        chat_run_lease_seconds=base.chat_run_lease_seconds,
        chat_run_stream_poll_ms=base.chat_run_stream_poll_ms,
        chat_run_worker_poll_ms=base.chat_run_worker_poll_ms,
        chat_run_worker_concurrency=base.chat_run_worker_concurrency,
        redis_url=base.redis_url,
        auth0_enabled=base.auth0_enabled,
        auth0_domain=base.auth0_domain,
        auth0_issuer=base.auth0_issuer,
        auth0_audience=base.auth0_audience,
        auth0_jwks_ttl_seconds=base.auth0_jwks_ttl_seconds,
        frontend_origins=base.frontend_origins,
        enable_legacy_bootstrap_auth=base.enable_legacy_bootstrap_auth,
    )


def load_effective_settings() -> AppSettings:
    """Load runtime settings from DB merged with SETTINGS defaults. Falls back to SETTINGS on error."""
    try:
        row = _load_settings_row()
        if row is None:
            return SETTINGS
        return _apply_db_overrides(SETTINGS, row)
    except Exception:
        import logging
        logging.getLogger(__name__).warning(
            "load_effective_settings failed; using defaults.", exc_info=True
        )
        return SETTINGS
```

- [ ] **Step 4: Run tests — confirm they pass**

```bash
cd Back-end && conda run -n AI-Linux-Assistant python -m pytest tests/test_load_effective_settings.py -v
```

Expected: all 7 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add Back-end/app/config/settings.py Back-end/tests/test_load_effective_settings.py
git commit -m "feat: add load_effective_settings with DB override merge"
```

---

### Task 4: Admin API endpoints

**Files:**
- Modify: `Back-end/app/api.py`
- Create: `Back-end/tests/test_admin_settings_api.py`

- [ ] **Step 1: Write failing tests**

Create `Back-end/tests/test_admin_settings_api.py`:

```python
"""Tests for GET /admin/settings and PUT /admin/settings."""
import pytest
from unittest.mock import MagicMock, patch
from httpx import AsyncClient

from app.main import create_app
from config.settings import SETTINGS


@pytest.fixture
async def client():
    app = create_app()
    async with AsyncClient(app=app, base_url="http://test") as c:
        yield c


def _admin_user():
    u = MagicMock()
    u.role = "admin"
    u.id = "admin-sub"
    return u


class _NullRow:
    """Simulates a DB row where every override column is NULL."""
    def __getattr__(self, name):
        return None


@pytest.mark.asyncio
async def test_get_settings_non_admin_returns_403(client, mocker):
    non_admin = MagicMock()
    non_admin.role = "user"
    mocker.patch("app.api._require_current_user", return_value=non_admin)
    response = await client.get("/admin/settings")
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_get_settings_returns_17_components(client, mocker):
    mocker.patch("app.api._require_current_user", return_value=_admin_user())
    mocker.patch("config.settings._load_settings_row", return_value=None)

    response = await client.get("/admin/settings")
    assert response.status_code == 200
    data = response.json()

    expected_keys = {
        "classifier", "contextualizer", "responder",
        "magi_eager", "magi_skeptic", "magi_historian", "magi_arbiter",
        "magi_lite_eager", "magi_lite_skeptic", "magi_lite_historian", "magi_lite_arbiter",
        "history_summarizer", "context_summarizer", "memory_extractor",
        "registry_updater", "ingest_enricher", "chat_namer",
    }
    assert set(data.keys()) == expected_keys
    for key in expected_keys:
        assert {"provider", "model", "reasoning_effort", "is_default"} <= set(data[key].keys())


@pytest.mark.asyncio
async def test_get_settings_is_default_true_when_no_overrides(client, mocker):
    mocker.patch("app.api._require_current_user", return_value=_admin_user())
    mocker.patch("config.settings._load_settings_row", return_value=None)

    response = await client.get("/admin/settings")
    data = response.json()
    assert data["responder"]["is_default"] is True
    assert data["responder"]["provider"] == SETTINGS.responder.provider


@pytest.mark.asyncio
async def test_put_settings_non_admin_returns_403(client, mocker):
    non_admin = MagicMock()
    non_admin.role = "user"
    mocker.patch("app.api._require_current_user", return_value=non_admin)
    response = await client.put("/admin/settings", json={"responder": {"provider": "openai"}})
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_put_settings_invalid_provider_returns_422(client, mocker):
    mocker.patch("app.api._require_current_user", return_value=_admin_user())
    response = await client.put(
        "/admin/settings",
        json={"responder": {"provider": "not-a-real-provider"}},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_put_settings_invalid_reasoning_effort_returns_422(client, mocker):
    mocker.patch("app.api._require_current_user", return_value=_admin_user())
    response = await client.put(
        "/admin/settings",
        json={"responder": {"reasoning_effort": "ultra"}},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_put_settings_partial_patch_updates_only_specified_fields(client, mocker):
    mocker.patch("app.api._require_current_user", return_value=_admin_user())

    # Fake a DB row that comes back after the commit with our patch applied
    fake_row = _NullRow()
    fake_row.responder_provider = "anthropic"
    fake_row.responder_model = "claude-opus-4-6"
    fake_row.responder_reasoning_effort = "high"

    mock_session = MagicMock()
    mock_session.__enter__ = MagicMock(return_value=mock_session)
    mock_session.__exit__ = MagicMock(return_value=False)
    mock_session.get = MagicMock(return_value=fake_row)
    mocker.patch("app.api.get_session_factory", return_value=MagicMock(return_value=mock_session))

    response = await client.put(
        "/admin/settings",
        json={"responder": {"provider": "anthropic", "model": "claude-opus-4-6", "reasoning_effort": "high"}},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["responder"]["provider"] == "anthropic"
    assert data["responder"]["is_default"] is False
    # Other components unaffected (still default)
    assert data["classifier"]["is_default"] is True
```

- [ ] **Step 2: Run tests — confirm they fail**

```bash
cd Back-end && conda run -n AI-Linux-Assistant python -m pytest tests/test_admin_settings_api.py -v 2>&1 | head -30
```

Expected: `ImportError` or 404 (routes not yet defined).

- [ ] **Step 3: Add imports to api.py**

Add these to the existing import block at the top of `Back-end/app/api.py`:

```python
from datetime import datetime, timezone
from typing import Literal
from persistence.database import get_session_factory
```

- [ ] **Step 4: Add Pydantic models to api.py**

Add alongside the existing Pydantic models (e.g. after `class ChatResumeRequest`):

```python
class ComponentSettingsResponse(BaseModel):
    provider: str
    model: str
    reasoning_effort: str
    is_default: bool


class AppSettingsResponse(BaseModel):
    classifier: ComponentSettingsResponse
    contextualizer: ComponentSettingsResponse
    responder: ComponentSettingsResponse
    history_summarizer: ComponentSettingsResponse
    context_summarizer: ComponentSettingsResponse
    memory_extractor: ComponentSettingsResponse
    registry_updater: ComponentSettingsResponse
    ingest_enricher: ComponentSettingsResponse
    chat_namer: ComponentSettingsResponse
    magi_eager: ComponentSettingsResponse
    magi_skeptic: ComponentSettingsResponse
    magi_historian: ComponentSettingsResponse
    magi_arbiter: ComponentSettingsResponse
    magi_lite_eager: ComponentSettingsResponse
    magi_lite_skeptic: ComponentSettingsResponse
    magi_lite_historian: ComponentSettingsResponse
    magi_lite_arbiter: ComponentSettingsResponse


class ComponentSettingsPatch(BaseModel):
    provider: Literal["openai", "anthropic", "local"] | None = None
    model: str | None = None
    reasoning_effort: Literal["", "low", "medium", "high"] | None = None


class AppSettingsPatch(BaseModel):
    classifier: ComponentSettingsPatch | None = None
    contextualizer: ComponentSettingsPatch | None = None
    responder: ComponentSettingsPatch | None = None
    history_summarizer: ComponentSettingsPatch | None = None
    context_summarizer: ComponentSettingsPatch | None = None
    memory_extractor: ComponentSettingsPatch | None = None
    registry_updater: ComponentSettingsPatch | None = None
    ingest_enricher: ComponentSettingsPatch | None = None
    chat_namer: ComponentSettingsPatch | None = None
    magi_eager: ComponentSettingsPatch | None = None
    magi_skeptic: ComponentSettingsPatch | None = None
    magi_historian: ComponentSettingsPatch | None = None
    magi_arbiter: ComponentSettingsPatch | None = None
    magi_lite_eager: ComponentSettingsPatch | None = None
    magi_lite_skeptic: ComponentSettingsPatch | None = None
    magi_lite_historian: ComponentSettingsPatch | None = None
    magi_lite_arbiter: ComponentSettingsPatch | None = None
```

Add this constant at module level (outside `create_app`, alongside other module-level constants):

```python
_SETTINGS_COMPONENT_NAMES = [
    "classifier", "contextualizer", "responder",
    "magi_eager", "magi_skeptic", "magi_historian", "magi_arbiter",
    "magi_lite_eager", "magi_lite_skeptic", "magi_lite_historian", "magi_lite_arbiter",
    "history_summarizer", "context_summarizer", "memory_extractor",
    "registry_updater", "ingest_enricher", "chat_namer",
]
```

- [ ] **Step 5: Add the two routes inside create_app()**

Add these after the existing `/runs/{run_id}/requeue` route, inside `create_app()`:

```python
    @app.get("/admin/settings", response_model=AppSettingsResponse)
    def get_admin_settings(_admin=Depends(_require_admin)):
        from config.settings import _load_settings_row, _apply_db_overrides, SETTINGS
        row = _load_settings_row()
        effective = _apply_db_overrides(SETTINGS, row) if row is not None else SETTINGS

        def _c(role_settings, prefix):
            is_default = row is None or (
                getattr(row, f"{prefix}_provider", None) is None
                and getattr(row, f"{prefix}_model", None) is None
                and getattr(row, f"{prefix}_reasoning_effort", None) is None
            )
            return ComponentSettingsResponse(
                provider=role_settings.provider,
                model=role_settings.model,
                reasoning_effort=role_settings.reasoning_effort or "",
                is_default=is_default,
            )

        return AppSettingsResponse(
            classifier=_c(effective.classifier, "classifier"),
            contextualizer=_c(effective.contextualizer, "contextualizer"),
            responder=_c(effective.responder, "responder"),
            history_summarizer=_c(effective.history_summarizer, "history_summarizer"),
            context_summarizer=_c(effective.context_summarizer, "context_summarizer"),
            memory_extractor=_c(effective.memory_extractor, "memory_extractor"),
            registry_updater=_c(effective.registry_updater, "registry_updater"),
            ingest_enricher=_c(effective.ingest_enricher, "ingest_enricher"),
            chat_namer=_c(effective.chat_namer, "chat_namer"),
            magi_eager=_c(effective.magi_eager, "magi_eager"),
            magi_skeptic=_c(effective.magi_skeptic, "magi_skeptic"),
            magi_historian=_c(effective.magi_historian, "magi_historian"),
            magi_arbiter=_c(effective.magi_arbiter, "magi_arbiter"),
            magi_lite_eager=_c(effective.magi_lite_eager, "magi_lite_eager"),
            magi_lite_skeptic=_c(effective.magi_lite_skeptic, "magi_lite_skeptic"),
            magi_lite_historian=_c(effective.magi_lite_historian, "magi_lite_historian"),
            magi_lite_arbiter=_c(effective.magi_lite_arbiter, "magi_lite_arbiter"),
        )

    @app.put("/admin/settings", response_model=AppSettingsResponse)
    def update_admin_settings(patch: AppSettingsPatch, current_user=Depends(_require_admin)):
        from config.settings import _apply_db_overrides, SETTINGS
        from persistence.postgres_models import AppSettingsModel

        session_factory = get_session_factory()
        with session_factory() as session:
            row = session.get(AppSettingsModel, 1)
            if row is None:
                row = AppSettingsModel(id=1)
                session.add(row)

            patch_dict = patch.model_dump()
            for comp_name in _SETTINGS_COMPONENT_NAMES:
                comp_patch = patch_dict.get(comp_name)
                if comp_patch is None:
                    continue
                for field_name in ("provider", "model", "reasoning_effort"):
                    value = comp_patch.get(field_name)
                    if value is not None:
                        setattr(row, f"{comp_name}_{field_name}", value)

            row.updated_at = datetime.now(timezone.utc)
            row.updated_by = current_user.id
            session.commit()
            session.refresh(row)

        effective = _apply_db_overrides(SETTINGS, row)

        def _c(role_settings, prefix):
            is_default = (
                getattr(row, f"{prefix}_provider", None) is None
                and getattr(row, f"{prefix}_model", None) is None
                and getattr(row, f"{prefix}_reasoning_effort", None) is None
            )
            return ComponentSettingsResponse(
                provider=role_settings.provider,
                model=role_settings.model,
                reasoning_effort=role_settings.reasoning_effort or "",
                is_default=is_default,
            )

        return AppSettingsResponse(
            classifier=_c(effective.classifier, "classifier"),
            contextualizer=_c(effective.contextualizer, "contextualizer"),
            responder=_c(effective.responder, "responder"),
            history_summarizer=_c(effective.history_summarizer, "history_summarizer"),
            context_summarizer=_c(effective.context_summarizer, "context_summarizer"),
            memory_extractor=_c(effective.memory_extractor, "memory_extractor"),
            registry_updater=_c(effective.registry_updater, "registry_updater"),
            ingest_enricher=_c(effective.ingest_enricher, "ingest_enricher"),
            chat_namer=_c(effective.chat_namer, "chat_namer"),
            magi_eager=_c(effective.magi_eager, "magi_eager"),
            magi_skeptic=_c(effective.magi_skeptic, "magi_skeptic"),
            magi_historian=_c(effective.magi_historian, "magi_historian"),
            magi_arbiter=_c(effective.magi_arbiter, "magi_arbiter"),
            magi_lite_eager=_c(effective.magi_lite_eager, "magi_lite_eager"),
            magi_lite_skeptic=_c(effective.magi_lite_skeptic, "magi_lite_skeptic"),
            magi_lite_historian=_c(effective.magi_lite_historian, "magi_lite_historian"),
            magi_lite_arbiter=_c(effective.magi_lite_arbiter, "magi_lite_arbiter"),
        )
```

- [ ] **Step 6: Run tests — confirm they pass**

```bash
cd Back-end && conda run -n AI-Linux-Assistant python -m pytest tests/test_admin_settings_api.py -v
```

Expected: all tests PASS.

- [ ] **Step 7: Run the full test suite to check for regressions**

```bash
cd Back-end && conda run -n AI-Linux-Assistant python -m pytest tests/ -v 2>&1 | tail -20
```

Expected: no regressions.

- [ ] **Step 8: Commit**

```bash
git add Back-end/app/api.py Back-end/tests/test_admin_settings_api.py
git commit -m "feat: add GET/PUT /admin/settings endpoints"
```

---

### Task 5: Wire worker to use effective settings

**Files:**
- Modify: `Back-end/app/chat_run_worker.py`

- [ ] **Step 1: Add load_effective_settings to the existing SETTINGS import**

Find this line near the top of `Back-end/app/chat_run_worker.py`:

```python
from config.settings import SETTINGS
```

Change it to:

```python
from config.settings import SETTINGS, load_effective_settings
```

- [ ] **Step 2: Add settings= to the ModelRouter constructor call**

Find the `ModelRouter(...)` call in the worker (search for `ModelRouter(` in the file). It currently looks like:

```python
return ModelRouter(
    database=VectorDB(runtime_components=self._shared_retrieval_components),
    memory_store=PostgresMemoryStore(project_id=run.project_id),
    chat_store=self.app_store,
    chat_session_id=run.chat_session_id,
    cancel_check=lambda checkpoint: self.run_store.is_cancel_requested(run.id),
    pause_check=lambda checkpoint: self.run_store.is_pause_requested(run.id),
    persist_turn_messages=False,
    project_description=project_description,
)
```

Add `settings=load_effective_settings()` as the first keyword argument:

```python
return ModelRouter(
    settings=load_effective_settings(),
    database=VectorDB(runtime_components=self._shared_retrieval_components),
    memory_store=PostgresMemoryStore(project_id=run.project_id),
    chat_store=self.app_store,
    chat_session_id=run.chat_session_id,
    cancel_check=lambda checkpoint: self.run_store.is_cancel_requested(run.id),
    pause_check=lambda checkpoint: self.run_store.is_pause_requested(run.id),
    persist_turn_messages=False,
    project_description=project_description,
)
```

`ModelRouter.__init__` already accepts `settings=None` and does `self.settings = settings or SETTINGS` (line 105 of `model_router.py`). No other changes needed.

- [ ] **Step 3: Smoke test the import**

```bash
cd Back-end && conda run -n AI-Linux-Assistant python -c "
import sys; sys.path.insert(0, 'app')
from app.chat_run_worker import ChatRunWorker
print('import ok')
"
```

Expected: `import ok` with no errors.

- [ ] **Step 4: Commit**

```bash
git add Back-end/app/chat_run_worker.py
git commit -m "feat: worker passes load_effective_settings() to ModelRouter"
```

---

### Task 6: Frontend types and API client

**Files:**
- Modify: `Front-end/src/types.ts`
- Modify: `Front-end/src/api.ts`

- [ ] **Step 1: Append settings types to types.ts**

Add the following to the end of `Front-end/src/types.ts`:

```typescript
export const COMPONENT_KEYS = [
  "classifier",
  "contextualizer",
  "responder",
  "magi_eager",
  "magi_skeptic",
  "magi_historian",
  "magi_arbiter",
  "magi_lite_eager",
  "magi_lite_skeptic",
  "magi_lite_historian",
  "magi_lite_arbiter",
  "history_summarizer",
  "context_summarizer",
  "memory_extractor",
  "registry_updater",
  "ingest_enricher",
  "chat_namer",
] as const;

export type ComponentKey = (typeof COMPONENT_KEYS)[number];

/** What the API returns per component (includes is_default flag). */
export type ComponentSettings = {
  provider: string;
  model: string;
  reasoning_effort: string;
  is_default: boolean;
};

/** What we send to PUT /admin/settings for a single component. */
export type ComponentSettingsPatch = {
  provider?: string;
  model?: string;
  reasoning_effort?: string;
};

/** Full settings response from GET /admin/settings. */
export type AppSettingsConfig = Record<ComponentKey, ComponentSettings>;

/** Partial update body for PUT /admin/settings. */
export type AppSettingsPatch = Partial<Record<ComponentKey, ComponentSettingsPatch>>;
```

- [ ] **Step 2: Add getSettings and updateSettings to api.ts**

In `Front-end/src/api.ts`, add `AppSettingsConfig` and `AppSettingsPatch` to the type import block at the top:

```typescript
import type {
  AppBootstrapResponse,
  AppSettingsConfig,   // ← add
  AppSettingsPatch,    // ← add
  ChatMessage,
  ChatRunListResponse,
  ChatRun,
  ChatSession,
  Project,
  RunEvent,
  SendMessageResponse,
  User,
} from "./types";
```

Add the following two functions to the `api` export object (after existing functions):

```typescript
  async getSettings(): Promise<AppSettingsConfig> {
    return request<AppSettingsConfig>("/admin/settings");
  },

  async updateSettings(patch: AppSettingsPatch): Promise<AppSettingsConfig> {
    return request<AppSettingsConfig>("/admin/settings", {
      method: "PUT",
      body: JSON.stringify(patch),
    });
  },
```

- [ ] **Step 3: Verify TypeScript compiles**

```bash
cd Front-end && npm run build 2>&1 | head -30
```

Expected: build succeeds with no new type errors.

- [ ] **Step 4: Commit**

```bash
git add Front-end/src/types.ts Front-end/src/api.ts
git commit -m "feat: frontend settings types and API client"
```

---

### Task 7: useSettings hook

**Files:**
- Create: `Front-end/src/hooks/useSettings.ts`

- [ ] **Step 1: Create the hook**

Create `Front-end/src/hooks/useSettings.ts`:

```typescript
import { useState } from "react";
import { api } from "../api";
import type { AppSettingsConfig, AppSettingsPatch } from "../types";

export type UseSettingsResult = {
  settings: AppSettingsConfig | null;
  loading: boolean;
  saving: boolean;
  error: string | null;
  fetchSettings: () => Promise<void>;
  saveSettings: (patch: AppSettingsPatch) => Promise<void>;
};

export function useSettings(): UseSettingsResult {
  const [settings, setSettings] = useState<AppSettingsConfig | null>(null);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function fetchSettings() {
    setLoading(true);
    setError(null);
    try {
      const result = await api.getSettings();
      setSettings(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load settings.");
    } finally {
      setLoading(false);
    }
  }

  async function saveSettings(patch: AppSettingsPatch): Promise<void> {
    setSaving(true);
    setError(null);
    try {
      const result = await api.updateSettings(patch);
      setSettings(result);
    } catch (err) {
      const message = err instanceof Error ? err.message : "Failed to save settings.";
      setError(message);
      throw err;
    } finally {
      setSaving(false);
    }
  }

  return { settings, loading, saving, error, fetchSettings, saveSettings };
}
```

- [ ] **Step 2: Verify TypeScript compiles**

```bash
cd Front-end && npm run build 2>&1 | head -30
```

Expected: no new errors.

- [ ] **Step 3: Commit**

```bash
git add Front-end/src/hooks/useSettings.ts
git commit -m "feat: add useSettings hook"
```

---

### Task 8: SettingsDialog component

**Files:**
- Create: `Front-end/src/components/dialogs/SettingsDialog.tsx`

- [ ] **Step 1: Create the component**

Create `Front-end/src/components/dialogs/SettingsDialog.tsx`:

```tsx
import { useEffect, useState } from "react";
import type {
  AppSettingsConfig,
  AppSettingsPatch,
  ComponentKey,
  ComponentSettings,
  ComponentSettingsPatch,
} from "../../types";
import { COMPONENT_KEYS } from "../../types";

const MODEL_OPTIONS: Record<string, string[]> = {
  openai: ["gpt-5.4", "gpt-5.4-mini", "gpt-5.4-nano"],
  anthropic: ["claude-opus-4-6", "claude-sonnet-4-6", "claude-haiku-4-5-20251001"],
  local: ["qwen2.5:7b"],
};

const COMPONENT_LABELS: Record<ComponentKey, string> = {
  classifier: "Classifier",
  contextualizer: "Contextualizer",
  responder: "Responder",
  magi_eager: "Magi — Eager",
  magi_skeptic: "Magi — Skeptic",
  magi_historian: "Magi — Historian",
  magi_arbiter: "Magi — Arbiter",
  magi_lite_eager: "Magi Lite — Eager",
  magi_lite_skeptic: "Magi Lite — Skeptic",
  magi_lite_historian: "Magi Lite — Historian",
  magi_lite_arbiter: "Magi Lite — Arbiter",
  history_summarizer: "History Summarizer",
  context_summarizer: "Context Summarizer",
  memory_extractor: "Memory Extractor",
  registry_updater: "Registry Updater",
  ingest_enricher: "Ingest Enricher",
  chat_namer: "Chat Namer",
};

const CORE_MAGI_KEYS: ComponentKey[] = [
  "classifier", "contextualizer", "responder",
  "magi_eager", "magi_skeptic", "magi_historian", "magi_arbiter",
  "magi_lite_eager", "magi_lite_skeptic", "magi_lite_historian", "magi_lite_arbiter",
];

const ADVANCED_KEYS: ComponentKey[] = [
  "history_summarizer", "context_summarizer", "memory_extractor",
  "registry_updater", "ingest_enricher", "chat_namer",
];

type SettingsDialogProps = {
  settings: AppSettingsConfig | null;
  loading: boolean;
  saving: boolean;
  error: string | null;
  onSave: (patch: AppSettingsPatch) => Promise<void>;
  onClose: () => void;
};

function computePatch(original: AppSettingsConfig, draft: AppSettingsConfig): AppSettingsPatch {
  const patch: AppSettingsPatch = {};
  for (const key of COMPONENT_KEYS) {
    const o = original[key];
    const d = draft[key];
    if (d.provider !== o.provider || d.model !== o.model || d.reasoning_effort !== o.reasoning_effort) {
      patch[key] = { provider: d.provider, model: d.model, reasoning_effort: d.reasoning_effort };
    }
  }
  return patch;
}

function ComponentRow({
  componentKey,
  value,
  onChange,
}: {
  componentKey: ComponentKey;
  value: ComponentSettings;
  onChange: (patch: Partial<ComponentSettingsPatch>) => void;
}) {
  const listId = `models-${componentKey}`;
  return (
    <div className="settings-row">
      <span className="settings-label">
        {COMPONENT_LABELS[componentKey]}
        {value.is_default && <span className="settings-default-badge">default</span>}
      </span>
      <select
        value={value.provider}
        onChange={(e) => onChange({ provider: e.target.value, model: "" })}
      >
        <option value="openai">openai</option>
        <option value="anthropic">anthropic</option>
        <option value="local">local</option>
      </select>
      <input
        list={listId}
        value={value.model}
        onChange={(e) => onChange({ model: e.target.value })}
        placeholder="model name"
        className="settings-model-input"
      />
      <datalist id={listId}>
        {(MODEL_OPTIONS[value.provider] ?? []).map((m) => (
          <option key={m} value={m} />
        ))}
      </datalist>
      <select
        value={value.reasoning_effort}
        onChange={(e) => onChange({ reasoning_effort: e.target.value })}
      >
        <option value="">none</option>
        <option value="low">low</option>
        <option value="medium">medium</option>
        <option value="high">high</option>
      </select>
    </div>
  );
}

export function SettingsDialog({
  settings,
  loading,
  saving,
  error,
  onSave,
  onClose,
}: SettingsDialogProps) {
  const [tab, setTab] = useState<"core" | "advanced">("core");
  const [draft, setDraft] = useState<AppSettingsConfig | null>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    if (settings !== null) {
      setDraft(settings);
    }
  }, [settings]);

  function updateDraft(key: ComponentKey, patch: Partial<ComponentSettingsPatch>) {
    setDraft((prev) => {
      if (!prev) return prev;
      return { ...prev, [key]: { ...prev[key], ...patch, is_default: false } };
    });
  }

  async function handleSave() {
    if (!draft || !settings) return;
    const patch = computePatch(settings, draft);
    if (Object.keys(patch).length === 0) {
      onClose();
      return;
    }
    try {
      await onSave(patch);
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch {
      // error shown via props.error
    }
  }

  const activeKeys = tab === "core" ? CORE_MAGI_KEYS : ADVANCED_KEYS;

  return (
    <div className="dialog-backdrop" onClick={onClose}>
      <div
        className="dialog-card settings-dialog-card"
        role="dialog"
        aria-modal="true"
        aria-labelledby="settings-dialog-title"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="dialog-header">
          <div>
            <p className="eyebrow">Admin</p>
            <h2 id="settings-dialog-title">Model Settings</h2>
          </div>
          <button type="button" className="icon-button" aria-label="Close settings" onClick={onClose}>
            ×
          </button>
        </div>

        <div className="settings-tabs">
          <button
            type="button"
            className={`settings-tab${tab === "core" ? " active" : ""}`}
            onClick={() => setTab("core")}
          >
            Core &amp; Magi
          </button>
          <button
            type="button"
            className={`settings-tab${tab === "advanced" ? " active" : ""}`}
            onClick={() => setTab("advanced")}
          >
            Advanced
          </button>
        </div>

        <div className="settings-body">
          {loading && <p className="settings-status">Loading settings…</p>}
          {!loading && draft === null && <p className="settings-status">No settings loaded.</p>}
          {draft !== null && (
            <div className="settings-rows">
              {activeKeys.map((key) => (
                <ComponentRow
                  key={key}
                  componentKey={key}
                  value={draft[key]}
                  onChange={(patch) => updateDraft(key, patch)}
                />
              ))}
            </div>
          )}
        </div>

        {error && <p className="settings-error">{error}</p>}
        {saved && <p className="settings-saved">Saved.</p>}

        <div className="dialog-actions">
          <button type="button" className="ghost-button compact" onClick={onClose}>
            Cancel
          </button>
          <button
            type="button"
            onClick={handleSave}
            disabled={saving || loading || draft === null}
          >
            {saving ? "Saving…" : "Save"}
          </button>
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Verify TypeScript compiles**

```bash
cd Front-end && npm run build 2>&1 | head -30
```

Expected: no new errors.

- [ ] **Step 3: Commit**

```bash
git add Front-end/src/components/dialogs/SettingsDialog.tsx
git commit -m "feat: add SettingsDialog component"
```

---

### Task 9: Wire into App.tsx and Sidebar.tsx

**Files:**
- Modify: `Front-end/src/App.tsx`
- Modify: `Front-end/src/components/Sidebar.tsx`

- [ ] **Step 1: Find insertion points in App.tsx**

```bash
grep -n "debugPanelOpen\|isDebugMode\|DebugPanel\|<Sidebar" Front-end/src/App.tsx | head -20
```

Note the line numbers — use them to guide the following edits.

- [ ] **Step 2: Add useSettings import and SettingsDialog import to App.tsx**

Add these two lines to the imports in `Front-end/src/App.tsx`:

```typescript
import { useSettings } from "./hooks/useSettings";
import { SettingsDialog } from "./components/dialogs/SettingsDialog";
```

- [ ] **Step 3: Add settings state and handlers to App.tsx component body**

Add the following in the component body alongside the other hook calls and state declarations:

```typescript
const [settingsPanelOpen, setSettingsPanelOpen] = useState(false);
const {
  settings,
  loading: settingsLoading,
  saving: settingsSaving,
  error: settingsError,
  fetchSettings,
  saveSettings,
} = useSettings();

function handleOpenSettings() {
  setSettingsPanelOpen(true);
  fetchSettings();
}

function handleCloseSettings() {
  setSettingsPanelOpen(false);
}
```

- [ ] **Step 4: Pass onOpenSettings to the Sidebar component in App.tsx JSX**

Find the `<Sidebar ...>` JSX in App.tsx and add the prop:

```tsx
onOpenSettings={handleOpenSettings}
```

- [ ] **Step 5: Add SettingsDialog to App.tsx JSX**

Add the following conditional render adjacent to where `<DebugPanel>` is rendered:

```tsx
{settingsPanelOpen && isDebugMode && (
  <SettingsDialog
    settings={settings}
    loading={settingsLoading}
    saving={settingsSaving}
    error={settingsError}
    onSave={saveSettings}
    onClose={handleCloseSettings}
  />
)}
```

- [ ] **Step 6: Add onOpenSettings to SidebarProps type in Sidebar.tsx**

In `Front-end/src/components/Sidebar.tsx`, add to the `SidebarProps` type:

```typescript
  onOpenSettings: () => void;
```

Add it to the destructured props in the `Sidebar` function signature:

```typescript
  onOpenSettings,
```

- [ ] **Step 7: Add the gear button to sidebar-footer-actions in Sidebar.tsx**

Find the `sidebar-footer-actions` div (around line 350). The current content is:

```tsx
{isDebugMode ? (
  <button
    type="button"
    className={`debug-chip${debugPanelOpen ? " active" : ""}`}
    onClick={onToggleDebugPanel}
  >
    [DBG]
  </button>
) : null}
```

Replace with:

```tsx
{isDebugMode ? (
  <>
    <button
      type="button"
      className={`debug-chip${debugPanelOpen ? " active" : ""}`}
      onClick={onToggleDebugPanel}
    >
      [DBG]
    </button>
    <button
      type="button"
      className="debug-chip"
      onClick={onOpenSettings}
      aria-label="Open model settings"
    >
      ⚙
    </button>
  </>
) : null}
```

- [ ] **Step 8: Verify TypeScript compiles**

```bash
cd Front-end && npm run build 2>&1 | head -40
```

Expected: clean build, no new type errors.

- [ ] **Step 9: Smoke test in the browser**

```bash
cd Front-end && npm run dev
```

Sign in as an admin user and verify:
- The ⚙ button appears in the sidebar footer (admin only — not visible for role=user)
- Clicking ⚙ opens the settings modal
- "Core & Magi" tab shows 11 rows (classifier, contextualizer, responder, 4 magi, 4 magi-lite)
- "Advanced" tab shows 6 rows (history_summarizer, context_summarizer, memory_extractor, registry_updater, ingest_enricher, chat_namer)
- Each row shows label, provider dropdown, model combobox (with datalist suggestions), reasoning effort dropdown
- Rows with no DB override show the "default" badge
- Changing a provider clears the model field
- Clicking "Save" with no changes closes the dialog
- Clicking "Save" with changes sends PUT /admin/settings and shows "Saved." confirmation
- An API error keeps the dialog open and shows the error message inline
- Backdrop click closes the dialog

- [ ] **Step 10: Commit**

```bash
git add Front-end/src/App.tsx Front-end/src/components/Sidebar.tsx
git commit -m "feat: wire SettingsDialog into App and Sidebar"
```

---

## Self-Review

### Spec coverage

| Spec requirement | Covered by |
|---|---|
| Admin-only access (403 for non-admin) | Task 4 (`_require_admin`), Task 9 (`isDebugMode` gate) |
| All 17 components editable | Tasks 1, 3, 4, 6, 8 |
| provider / model / reasoning_effort per component | Tasks 1, 3, 4, 6, 8 |
| DB-backed persistence | Tasks 1, 2, 4 |
| Changes effective on next run, no restart | Task 5 |
| Global (not per-user) settings | Task 1 (singleton row), Task 4 |
| Layered resolution: DB → .env → code default | Task 3 |
| DB fallback on error (runs not blocked) | Task 3 (`load_effective_settings` catches) |
| 403 for non-admin | Task 4 (tests verify) |
| 422 for invalid provider or reasoning_effort | Task 4 (`Literal` types enforce) |
| Core & Magi tab / Advanced tab | Task 8 |
| Hybrid model combobox | Task 8 (`<datalist>`) |
| "default" badge | Tasks 4 (`is_default`), Task 8 |
| Inline error on save failure | Task 8 (`error` prop) |
| Migration | Task 2 |
| `load_effective_settings` unit tests | Task 3 |
| API tests (admin/non-admin, GET/PUT) | Task 4 |
| NULL = default, "" = explicit no-reasoning | Task 3 (comment + test) |

All spec sections are covered. No gaps.

### Placeholder scan

No TBDs, TODOs, or vague steps found.

### Type consistency

- `ComponentKey` defined in Task 6 (`types.ts`) — used in Tasks 7, 8 with `COMPONENT_KEYS` array ✓
- `AppSettingsConfig` / `AppSettingsPatch` defined in Task 6 — used in Tasks 7, 8, and `api.ts` ✓
- `ComponentSettingsPatch` used in Task 8's `updateDraft` and `computePatch` — defined in Task 6 ✓
- `_apply_db_overrides` / `_load_settings_row` defined in Task 3 — imported in Task 4 routes ✓
- `load_effective_settings` defined in Task 3 — imported in Task 5 worker ✓
- `_SETTINGS_COMPONENT_NAMES` defined at module level in Task 4 — used inside routes in same task ✓
- `get_session_factory` imported in Task 4 api.py — used in `update_admin_settings` route ✓
- `AppSettingsModel` imported locally inside routes in Task 4 — defined in Task 1 ✓
