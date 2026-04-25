"""Tests that the ingest CLI no longer blocks on input() when no path is given (T5)."""

import subprocess
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1]
_SCRIPT = _BACKEND / "scripts" / "ingest" / "ingest_pipeline.py"

PYTHON = sys.executable


def test_no_args_exits_code_2():
    result = subprocess.run(
        [PYTHON, str(_SCRIPT)],
        stdin=subprocess.DEVNULL,
        timeout=10,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2, (
        f"Expected exit code 2, got {result.returncode}.\nstderr: {result.stderr}"
    )


def test_no_args_prints_usage_to_stderr():
    result = subprocess.run(
        [PYTHON, str(_SCRIPT)],
        stdin=subprocess.DEVNULL,
        timeout=10,
        capture_output=True,
        text=True,
    )
    assert "Usage:" in result.stderr, (
        f"Expected 'Usage:' in stderr, got:\n{result.stderr}"
    )
