from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from .models import CommandExecutionResult, ScenarioSpec, VerificationCheck

logger = logging.getLogger(__name__)

_SABOTAGE_PROSE_PREFIXES = frozenset(
    {
        "add",
        "check",
        "confirm",
        "delete",
        "create",
        "disable",
        "enable",
        "ensure",
        "fix",
        "kill",
        "install",
        "make",
        "remove",
        "restart",
        "restore",
        "set",
        "start",
        "stop",
        "update",
        "write",
    }
)
_SABOTAGE_PROSE_CONTEXT_WORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "be",
        "before",
        "because",
        "by",
        "for",
        "from",
        "in",
        "into",
        "is",
        "it",
        "of",
        "on",
        "or",
        "please",
        "so",
        "than",
        "that",
        "the",
        "then",
        "these",
        "those",
        "to",
        "using",
        "was",
        "were",
        "with",
        "would",
    }
)


class ScenarioValidationError(ValueError):
    """Raised when a scenario is not runnable."""


def evaluate_verification(check: VerificationCheck, result: CommandExecutionResult) -> bool:
    return check.is_satisfied_by(result)


def validate_sabotage_step(step: str, index: int, *, field_name: str = "sabotage_procedure") -> str | None:
    stripped = step.strip()
    if not stripped:
        return f"{field_name}[{index}] must not be empty"
    if "```" in stripped:
        return f"{field_name}[{index}] must not contain markdown code fences"
    if "`" in stripped:
        return f"{field_name}[{index}] must not contain backticks"
    if stripped.endswith(":"):
        return f"{field_name}[{index}] must be a raw shell command or shell snippet, not a narrative label"

    if ":" in stripped:
        prefix, suffix = stripped.split(":", 1)
        prefix = prefix.strip()
        if prefix and suffix.strip() and " " in prefix and not re.search(r"[`'\"$><|&;(){}]", prefix):
            first_word = prefix.split()[0].strip(",.").lower()
            if prefix[0].isupper() or first_word in _SABOTAGE_PROSE_PREFIXES:
                return (
                    f"{field_name}[{index}] must be a raw shell command or shell snippet, not a narrative label"
                )

    words = stripped.split()
    if words:
        first_word = words[0].strip(",.").lower()
        shell_like = bool(re.search(r"[`'\"$><|&;(){}=]", stripped))
        shell_like = shell_like or any(word.startswith("-") for word in words[1:])
        shell_like = shell_like or any(word.startswith("./") or word.startswith("~/") or "/" in word for word in words)
        has_prose_context = any(word.lower().strip(",.;:!?") in _SABOTAGE_PROSE_CONTEXT_WORDS for word in words[1:])
        if first_word in _SABOTAGE_PROSE_PREFIXES and not shell_like and (words[0][0].isupper() or has_prose_context):
            return f"{field_name}[{index}] must be a raw shell command or shell snippet, not prose"

    return None


def validate_scenario(spec: ScenarioSpec) -> None:
    errors: list[str] = []
    if not spec.scenario_name:
        errors.append("scenario_name is required")
    if not spec.title:
        errors.append("title is required")
    if not spec.summary:
        errors.append("summary is required")
    if not spec.what_it_tests:
        errors.append("what_it_tests must contain at least one item")
    if not spec.target_image:
        errors.append("target_image is required")
    if not spec.sabotage_procedure:
        errors.append("at least one sabotage_procedure step is required")
    if not spec.verification_probes:
        errors.append("at least one verification_probe is required")
    if not spec.repair_checks:
        errors.append("at least one repair_check is required")
    if not spec.observable_problem_statement:
        errors.append("observable_problem_statement is required")
    if not spec.judge_rubric:
        errors.append("judge_rubric must contain at least one rubric item")
    if spec.turn_budget <= 0:
        errors.append("turn_budget must be greater than 0")

    for index, step in enumerate(spec.sabotage_procedure, start=1):
        sabotage_error = validate_sabotage_step(step, index)
        if sabotage_error is not None:
            errors.append(sabotage_error)

    for label, checks in (("verification_probes", spec.verification_probes), ("repair_checks", spec.repair_checks)):
        for index, check in enumerate(checks, start=1):
            if not check.name:
                errors.append(f"{label}[{index}].name is required")
            if not check.command:
                errors.append(f"{label}[{index}].command is required")
            if check.timeout_seconds <= 0:
                errors.append(f"{label}[{index}].timeout_seconds must be greater than 0")
            if not check.has_machine_expectation():
                errors.append(
                    f"{label}[{index}] must include at least one machine-checkable expectation "
                    "(e.g., expected_exit_code, expected_substrings, expected_regexes, etc.)"
                )

    action_verbs = {"curl", "ssh", "nc", "dig", "systemd-run", "wget", "ping", "mysql", "psql"}
    has_action_verb = False
    for check in spec.repair_checks:
        if any(verb in check.command for verb in action_verbs):
            has_action_verb = True
            break
    if not has_action_verb and spec.repair_checks:
        logger.warning(
            f"Scenario '{spec.scenario_name}' has no common action verbs (curl, ssh, nc, etc.) "
            "in repair_checks. Consider adding end-to-end functional verification."
        )

    if errors:
        raise ScenarioValidationError("; ".join(errors))


def load_scenario(path: str | Path) -> ScenarioSpec:
    scenario_path = Path(path)
    payload = json.loads(scenario_path.read_text(encoding="utf-8"))
    spec = ScenarioSpec.from_dict(payload)
    validate_scenario(spec)
    return spec


def write_scenario(path: str | Path, spec: ScenarioSpec) -> Path:
    validate_scenario(spec)
    scenario_path = Path(path)
    scenario_path.parent.mkdir(parents=True, exist_ok=True)
    scenario_path.write_text(json.dumps(spec.to_dict(), indent=2), encoding="utf-8")
    return scenario_path
