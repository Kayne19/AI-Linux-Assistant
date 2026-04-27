"""AWS profile resolution and preflight checks for the eval harness.

This module provides two public helpers:

- ``resolve_aws_profile()`` — determines the active AWS profile and region from
  environment variables and the boto3 session, returning ``(profile, region)``.
- ``preflight_aws()`` — runs all credential and tool checks, raising
  ``AwsPreflightError`` with a single actionable remediation message on the first
  failure.

Integration sites must call ``preflight_aws()`` exactly once per
backend/controller/AMI-build session — not on every request.
"""

from __future__ import annotations

import logging
import os
import shutil

logger = logging.getLogger(__name__)

__all__ = ["AwsPreflightError", "preflight_aws", "resolve_aws_profile"]

# ---------------------------------------------------------------------------
# Optional dependency — boto3/botocore may not be installed in every env.
# Module-level import so tests can patch eval_harness.aws_auth.boto3 easily.
# ---------------------------------------------------------------------------
try:
    import boto3 as boto3  # noqa: PLC0414
    import botocore.exceptions as botocore_exceptions
    _BOTO3_AVAILABLE = True
except ImportError:  # pragma: no cover
    boto3 = None  # type: ignore[assignment]
    botocore_exceptions = None  # type: ignore[assignment]
    _BOTO3_AVAILABLE = False


class AwsPreflightError(Exception):
    """Raised when an AWS preflight check fails.

    The ``message`` argument (and ``str(exc)``) is the human-readable,
    operator-facing remediation text.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message

    def __str__(self) -> str:
        return self.message


def resolve_aws_profile() -> tuple[str | None, str | None]:
    """Return ``(profile, region)`` using the harness-specific precedence rules.

    Profile precedence (first non-empty value wins):
      1. ``EVAL_HARNESS_AWS_PROFILE``
      2. ``AWS_PROFILE``
      3. ``None`` — let boto3 use its default credential chain.

    Region precedence:
      1. ``AWS_REGION``
      2. ``AWS_DEFAULT_REGION``
      3. The region resolved by ``boto3.Session(profile_name=profile).region_name``
      4. ``None``
    """
    profile: str | None = (
        os.environ.get("EVAL_HARNESS_AWS_PROFILE", "").strip() or
        os.environ.get("AWS_PROFILE", "").strip() or
        None
    )

    region: str | None = (
        os.environ.get("AWS_REGION", "").strip() or
        os.environ.get("AWS_DEFAULT_REGION", "").strip() or
        None
    )

    if region is None and boto3 is not None:
        try:
            session = boto3.Session(profile_name=profile)
            region = session.region_name or None
        except Exception:  # noqa: BLE001
            region = None

    return profile, region


def preflight_aws(
    *,
    require_packer: bool = False,
    require_aws_cli: bool = False,
) -> None:
    """Run all AWS preflight checks and raise ``AwsPreflightError`` on failure.

    Check order (fail-fast):

    1. Resolve profile + region; log them at INFO.
    2. Call ``sts.get_caller_identity()`` to verify credential validity.
    3. Check for ``aws`` CLI on PATH if ``require_aws_cli`` is ``True``.
    4. Check for ``packer`` on PATH if ``require_packer`` is ``True``.

    Args:
        require_packer: When ``True``, assert that the ``packer`` binary is
            available on ``PATH``.
        require_aws_cli: When ``True``, assert that the ``aws`` CLI is available
            on ``PATH``.

    Raises:
        AwsPreflightError: On any credential, SSO, or missing-tool failure.
    """
    if not _BOTO3_AVAILABLE:  # pragma: no cover
        raise AwsPreflightError(
            "boto3 / botocore are not installed. Install eval-harness[aws]."
        )

    profile, region = resolve_aws_profile()
    profile_display = profile or "(default)"
    logger.info(
        "AWS preflight: profile=%s region=%s",
        profile_display,
        region or "(unset)",
    )

    # --- credential check ---
    _SSO_ERROR_CODES = {"ExpiredToken", "ExpiredTokenException", "InvalidClientTokenId"}

    # Build the SSO exception tuple dynamically so we gracefully handle older
    # botocore builds that may not have all three classes.
    _sso_exceptions: list[type[BaseException]] = []
    for _name in ("SSOTokenLoadError", "UnauthorizedSSOTokenError", "TokenRetrievalError"):
        _cls = getattr(botocore_exceptions, _name, None)
        if _cls is not None:
            _sso_exceptions.append(_cls)
    _sso_exception_tuple = tuple(_sso_exceptions) if _sso_exceptions else (type(None),)

    try:
        session = boto3.Session(profile_name=profile)
        sts = session.client("sts")
        sts.get_caller_identity()
    except _sso_exception_tuple as exc:
        raise AwsPreflightError(
            f"AWS SSO session expired or missing for profile '{profile_display}'. "
            f"Run: aws sso login --profile {profile or '<your-profile>'}"
        ) from exc
    except botocore_exceptions.ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")  # type: ignore[union-attr]
        if code in _SSO_ERROR_CODES:
            raise AwsPreflightError(
                f"AWS SSO session expired or missing for profile '{profile_display}'. "
                f"Run: aws sso login --profile {profile or '<your-profile>'}"
            ) from exc
        # Other ClientErrors (e.g. permission issues) — re-raise with context.
        raise AwsPreflightError(
            f"AWS credential check failed for profile '{profile_display}': {exc}"
        ) from exc
    except botocore_exceptions.NoCredentialsError as exc:
        raise AwsPreflightError(
            f"No AWS credentials found for profile '{profile_display}'. "
            "Set EVAL_HARNESS_AWS_PROFILE or AWS_PROFILE to a configured profile, "
            "or configure credentials via environment variables / instance role."
        ) from exc
    except Exception as exc:  # noqa: BLE001
        # Catch ProfileNotFound and any other botocore errors by name since the
        # class may not exist in all botocore versions.
        exc_type_name = type(exc).__name__
        if exc_type_name == "ProfileNotFound":
            raise AwsPreflightError(
                f"AWS profile '{profile_display}' was not found in ~/.aws/config. "
                "Set EVAL_HARNESS_AWS_PROFILE or AWS_PROFILE to a valid profile name."
            ) from exc
        raise

    # --- tool checks ---
    if require_aws_cli and shutil.which("aws") is None:
        raise AwsPreflightError(
            "aws CLI not found on PATH; install AWS CLI v2"
        )

    if require_packer and shutil.which("packer") is None:
        raise AwsPreflightError(
            "packer not found on PATH; install HashiCorp Packer"
        )
