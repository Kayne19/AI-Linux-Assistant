from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType

SRC_EVAL_HARNESS = Path(__file__).resolve().parents[1] / "src" / "eval_harness"
if "eval_harness" not in sys.modules:
    namespace_pkg = ModuleType("eval_harness")
    namespace_pkg.__path__ = [str(SRC_EVAL_HARNESS)]  # type: ignore[attr-defined]
    sys.modules["eval_harness"] = namespace_pkg

from eval_harness.cli import _redact_database_url


def test_redact_database_url_masks_password() -> None:
    value = "postgresql://user:secret@host:5432/dbname?sslmode=require"
    assert _redact_database_url(value) == "postgresql://user:***@host:5432/dbname?sslmode=require"
