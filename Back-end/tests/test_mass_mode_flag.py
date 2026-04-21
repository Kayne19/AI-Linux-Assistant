"""Tests for --mass-mode, --sanitize, and --min-page-coverage CLI flags.

Uses build_parser + build_config directly rather than subprocess so the test
stays fast and doesn't need a full environment import.
"""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Import script helpers (path manipulation mirrors what the script does)
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parent.parent
APP_DIR = SCRIPT_DIR / "app"

for p in (SCRIPT_DIR, APP_DIR):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))


def _get_build_config_helpers():
    """Import build_parser and build_config from the ingest_pipeline script."""
    import importlib.util

    script_path = SCRIPT_DIR / "scripts" / "ingest" / "ingest_pipeline.py"
    spec = importlib.util.spec_from_file_location("ingest_pipeline_script", script_path)
    mod = importlib.util.module_from_spec(spec)

    # Stub heavy imports that require env/settings at import time
    import types

    fake_settings = types.SimpleNamespace(
        ingest_enricher=types.SimpleNamespace(provider="local", model="m", reasoning_effort=None),
        registry_updater=types.SimpleNamespace(provider="local", model="m"),
    )
    with patch("app.config.settings.SETTINGS", fake_settings, create=True):
        sys.modules.setdefault("app.config.settings", types.ModuleType("app.config.settings"))
        sys.modules["app.config.settings"].SETTINGS = fake_settings
        spec.loader.exec_module(mod)

    return mod.build_parser, mod.build_config


class TestMassModeFlag:
    @pytest.fixture(autouse=True)
    def _helpers(self):
        self.build_parser, self.build_config = _get_build_config_helpers()

    def test_mass_mode_sets_all_three_fields(self):
        args = self.build_parser().parse_args(["--mass-mode", "dummy.pdf"])
        config = self.build_config(args)
        assert config.mass_mode is True
        assert config.sanitize is True
        assert config.min_page_coverage == pytest.approx(0.9)

    def test_defaults_without_mass_mode(self):
        args = self.build_parser().parse_args(["dummy.pdf"])
        config = self.build_config(args)
        assert config.mass_mode is False
        assert config.sanitize is False
        assert config.min_page_coverage == pytest.approx(0.9)

    def test_min_page_coverage_override(self):
        args = self.build_parser().parse_args(["--min-page-coverage", "0.75", "dummy.pdf"])
        config = self.build_config(args)
        assert config.min_page_coverage == pytest.approx(0.75)
        assert config.mass_mode is False

    def test_sanitize_flag_without_mass_mode(self):
        args = self.build_parser().parse_args(["--sanitize", "dummy.pdf"])
        config = self.build_config(args)
        assert config.sanitize is True
        assert config.mass_mode is False

    def test_mass_mode_with_custom_coverage(self):
        args = self.build_parser().parse_args(["--mass-mode", "--min-page-coverage", "0.8", "dummy.pdf"])
        config = self.build_config(args)
        assert config.mass_mode is True
        assert config.sanitize is True
        assert config.min_page_coverage == pytest.approx(0.8)

    def test_help_does_not_crash(self):
        """--help should exit cleanly (SystemExit with code 0)."""
        with pytest.raises(SystemExit) as exc_info:
            self.build_parser().parse_args(["--help"])
        assert exc_info.value.code == 0
