"""Tests for Phase 3 CLI additions (argparser introspection only; no live DB)."""
from __future__ import annotations

import sys
from pathlib import Path

SRC_EVAL_HARNESS = Path(__file__).resolve().parents[1] / "src" / "eval_harness"
if "eval_harness" not in sys.modules:
    from types import ModuleType
    namespace_pkg = ModuleType("eval_harness")
    namespace_pkg.__path__ = [str(SRC_EVAL_HARNESS)]  # type: ignore[attr-defined]
    sys.modules["eval_harness"] = namespace_pkg

from eval_harness.cli import build_parser


def _subparser_actions(parser, command: str):
    """Return the argument actions for a given subcommand."""
    subparsers_action = next(
        a for a in parser._actions if hasattr(a, "_name_parser_map")
    )
    sub = subparsers_action._name_parser_map[command]
    return {a.dest: a for a in sub._actions}


def test_run_judge_job_has_mode_flag() -> None:
    parser = build_parser()
    actions = _subparser_actions(parser, "run-judge-job")
    assert "mode" in actions
    mode_action = actions["mode"]
    assert mode_action.choices == ["absolute", "pairwise"]
    assert mode_action.default is None


def test_run_judge_job_has_anchor_subject_flag() -> None:
    parser = build_parser()
    actions = _subparser_actions(parser, "run-judge-job")
    assert "anchor_subject" in actions


def test_run_judge_job_has_bootstrap_samples_flag() -> None:
    parser = build_parser()
    actions = _subparser_actions(parser, "run-judge-job")
    assert "bootstrap_samples" in actions
    assert actions["bootstrap_samples"].default == 200


def test_run_judge_job_has_rng_seed_flag() -> None:
    parser = build_parser()
    actions = _subparser_actions(parser, "run-judge-job")
    assert "rng_seed" in actions
    assert actions["rng_seed"].default is None


def test_run_judge_job_defaults_no_mode() -> None:
    """Without --mode the default is None (resolved from config at runtime)."""
    parser = build_parser()
    args = parser.parse_args(["run-judge-job", "--config", "x.json", "--benchmark-run-id", "abc"])
    assert args.mode is None
    assert args.anchor_subject is None
    assert args.bootstrap_samples == 200


def test_calibrate_judge_subcommand_exists() -> None:
    parser = build_parser()
    subparsers_action = next(
        a for a in parser._actions if hasattr(a, "_name_parser_map")
    )
    assert "calibrate-judge" in subparsers_action._name_parser_map


def test_calibrate_judge_accepts_expected_flags() -> None:
    parser = build_parser()
    args = parser.parse_args([
        "calibrate-judge",
        "--config", "cfg.json",
        "--benchmark-run-id", "bid",
        "--strong", "judge-a",
        "--candidate", "judge-b",
        "--max-pairs", "25",
        "--mode", "absolute",
        "--rng-seed", "42",
        "--out", "/tmp/report.json",
    ])
    assert args.strong == "judge-a"
    assert args.candidate == "judge-b"
    assert args.max_pairs == 25
    assert args.mode == "absolute"
    assert args.rng_seed == 42
    assert args.out == "/tmp/report.json"


def test_calibrate_judge_default_mode_is_pairwise() -> None:
    parser = build_parser()
    args = parser.parse_args([
        "calibrate-judge",
        "--config", "cfg.json",
        "--benchmark-run-id", "bid",
        "--strong", "sa",
        "--candidate", "ca",
    ])
    assert args.mode == "pairwise"
    assert args.max_pairs == 50


def test_existing_subcommands_unchanged() -> None:
    """Phase 3 must not break pre-existing subcommands."""
    parser = build_parser()
    args = parser.parse_args(["validate-scenario", "some/path.json"])
    assert args.command == "validate-scenario"

    args2 = parser.parse_args([
        "run-benchmark",
        "--config", "cfg.json",
        "--setup-run-id", "sid",
    ])
    assert args2.command == "run-benchmark"
