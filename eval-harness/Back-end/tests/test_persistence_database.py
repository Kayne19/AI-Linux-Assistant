from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType

import pytest

SRC_EVAL_HARNESS = Path(__file__).resolve().parents[1] / "src" / "eval_harness"
if "eval_harness" not in sys.modules:
    namespace_pkg = ModuleType("eval_harness")
    namespace_pkg.__path__ = [str(SRC_EVAL_HARNESS)]  # type: ignore[attr-defined]
    sys.modules["eval_harness"] = namespace_pkg

from eval_harness.persistence.database import get_database_url, normalize_database_url


def test_get_database_url_prefers_eval_harness_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://fallback")
    monkeypatch.setenv("EVAL_HARNESS_DATABASE_URL", "postgresql://preferred")
    assert get_database_url() == "postgresql://preferred"


def test_get_database_url_falls_back_to_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EVAL_HARNESS_DATABASE_URL", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://fallback")
    assert get_database_url() == "postgresql://fallback"


def test_get_database_url_raises_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EVAL_HARNESS_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(RuntimeError):
        get_database_url()


def test_normalize_database_url_strips_query_value_whitespace() -> None:
    raw = "postgresql+psycopg2://user:pass@host:5432/db?sslmode=require%20"
    assert normalize_database_url(raw) == "postgresql+psycopg2://user:pass@host:5432/db?sslmode=require"


def test_normalize_database_url_converts_postgres_alias() -> None:
    raw = "postgres://user:pass@host:5432/db?sslmode=require%20"
    assert normalize_database_url(raw) == "postgresql://user:pass@host:5432/db?sslmode=require"
