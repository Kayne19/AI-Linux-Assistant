"""Tests for eval_harness.aws_auth: resolve_aws_profile and preflight_aws."""

from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Bootstrap path so the package is importable without installation.
# ---------------------------------------------------------------------------
SRC_EVAL_HARNESS = Path(__file__).resolve().parents[1] / "src" / "eval_harness"
if "eval_harness" not in sys.modules:
    namespace_pkg = ModuleType("eval_harness")
    namespace_pkg.__path__ = [str(SRC_EVAL_HARNESS)]  # type: ignore[attr-defined]
    sys.modules["eval_harness"] = namespace_pkg

import eval_harness.aws_auth as aws_auth_mod
from eval_harness.aws_auth import AwsPreflightError, preflight_aws, resolve_aws_profile


# ---------------------------------------------------------------------------
# Fake botocore exceptions used across multiple tests.
# ---------------------------------------------------------------------------

class _FakeExc:
    """Namespace of fake botocore exception classes."""

    class SSOTokenLoadError(Exception):
        pass

    class UnauthorizedSSOTokenError(Exception):
        pass

    class TokenRetrievalError(Exception):
        pass

    class ClientError(Exception):
        def __init__(self, response: dict, operation_name: str) -> None:
            self.response = response
            super().__init__(str(response))

    class NoCredentialsError(Exception):
        pass

    class ProfileNotFound(Exception):
        pass


# ---------------------------------------------------------------------------
# Context manager: patch both boto3 and botocore_exceptions in aws_auth_mod.
# ---------------------------------------------------------------------------

def _patched_aws(
    session_instance: Any,
    *,
    botocore_exc: Any = _FakeExc,
):
    """
    Return a context-manager that patches boto3 and botocore_exceptions used
    inside eval_harness.aws_auth with the given session_instance and exception
    namespace.
    """
    mock_boto3 = MagicMock()
    mock_boto3.Session.return_value = session_instance

    return patch.multiple(
        "eval_harness.aws_auth",
        boto3=mock_boto3,
        botocore_exceptions=botocore_exc,
        _BOTO3_AVAILABLE=True,
    )


def _ok_session(region: str | None = "us-east-1") -> MagicMock:
    """Session mock whose sts.get_caller_identity() succeeds."""
    sts = MagicMock()
    sts.get_caller_identity.return_value = {"Account": "123456789012"}
    session = MagicMock()
    session.client.return_value = sts
    session.region_name = region
    return session


def _failing_session(exc: Exception, region: str | None = "us-east-1") -> MagicMock:
    """Session mock whose sts.get_caller_identity() raises *exc*."""
    sts = MagicMock()
    sts.get_caller_identity.side_effect = exc
    session = MagicMock()
    session.client.return_value = sts
    session.region_name = region
    return session


# ---------------------------------------------------------------------------
# resolve_aws_profile — precedence tests
# ---------------------------------------------------------------------------


class TestResolveAwsProfilePrecedence:
    def test_harness_env_beats_aws_profile(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("EVAL_HARNESS_AWS_PROFILE", "foo")
        monkeypatch.setenv("AWS_PROFILE", "bar")
        with _patched_aws(_ok_session()):
            profile, _ = resolve_aws_profile()
        assert profile == "foo"

    def test_aws_profile_when_harness_env_absent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("EVAL_HARNESS_AWS_PROFILE", raising=False)
        monkeypatch.setenv("AWS_PROFILE", "bar")
        with _patched_aws(_ok_session()):
            profile, _ = resolve_aws_profile()
        assert profile == "bar"

    def test_none_when_both_absent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("EVAL_HARNESS_AWS_PROFILE", raising=False)
        monkeypatch.delenv("AWS_PROFILE", raising=False)
        with _patched_aws(_ok_session(region=None)):
            profile, _ = resolve_aws_profile()
        assert profile is None

    def test_region_from_aws_region_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AWS_REGION", "eu-west-1")
        monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)
        _, region = resolve_aws_profile()
        assert region == "eu-west-1"

    def test_region_from_aws_default_region_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("AWS_REGION", raising=False)
        monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-southeast-1")
        _, region = resolve_aws_profile()
        assert region == "ap-southeast-1"

    def test_region_falls_back_to_boto3_session(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("AWS_REGION", raising=False)
        monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)
        with _patched_aws(_ok_session(region="ca-central-1")):
            _, region = resolve_aws_profile()
        assert region == "ca-central-1"

    def test_region_none_when_all_absent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("AWS_REGION", raising=False)
        monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)
        with _patched_aws(_ok_session(region=None)):
            _, region = resolve_aws_profile()
        assert region is None


# ---------------------------------------------------------------------------
# preflight_aws — SSO/expired-session errors
# ---------------------------------------------------------------------------


class TestExpiredSso:
    def test_expired_sso_actionable_message_unauthorized_token(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("EVAL_HARNESS_AWS_PROFILE", "myprof")
        monkeypatch.delenv("AWS_PROFILE", raising=False)

        exc = _FakeExc.UnauthorizedSSOTokenError("token expired")
        session = _failing_session(exc)

        with _patched_aws(session), pytest.raises(AwsPreflightError) as exc_info:
            preflight_aws()

        msg = str(exc_info.value)
        assert "myprof" in msg
        assert "aws sso login --profile myprof" in msg

    def test_expired_sso_actionable_message_sso_token_load_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("EVAL_HARNESS_AWS_PROFILE", "myprof")
        monkeypatch.delenv("AWS_PROFILE", raising=False)

        exc = _FakeExc.SSOTokenLoadError("load failed")
        session = _failing_session(exc)

        with _patched_aws(session), pytest.raises(AwsPreflightError) as exc_info:
            preflight_aws()

        msg = str(exc_info.value)
        assert "myprof" in msg
        assert "aws sso login --profile myprof" in msg

    def test_expired_token_via_client_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("EVAL_HARNESS_AWS_PROFILE", "myprof")
        monkeypatch.delenv("AWS_PROFILE", raising=False)

        exc = _FakeExc.ClientError(
            {"Error": {"Code": "ExpiredToken", "Message": "The security token is expired"}},
            "GetCallerIdentity",
        )
        session = _failing_session(exc)

        with _patched_aws(session), pytest.raises(AwsPreflightError) as exc_info:
            preflight_aws()

        msg = str(exc_info.value)
        assert "myprof" in msg
        assert "aws sso login --profile myprof" in msg

    def test_expired_token_exception_code(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("EVAL_HARNESS_AWS_PROFILE", "myprof")
        monkeypatch.delenv("AWS_PROFILE", raising=False)

        exc = _FakeExc.ClientError(
            {"Error": {"Code": "ExpiredTokenException"}},
            "GetCallerIdentity",
        )
        session = _failing_session(exc)

        with _patched_aws(session), pytest.raises(AwsPreflightError) as exc_info:
            preflight_aws()

        assert "aws sso login" in str(exc_info.value)


# ---------------------------------------------------------------------------
# preflight_aws — missing tool checks
# ---------------------------------------------------------------------------


class TestMissingTools:
    def test_missing_aws_cli(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("EVAL_HARNESS_AWS_PROFILE", raising=False)
        monkeypatch.delenv("AWS_PROFILE", raising=False)
        session = _ok_session()

        with _patched_aws(session), \
             patch("eval_harness.aws_auth.shutil.which", side_effect=lambda name: None if name == "aws" else "/usr/bin/packer"), \
             pytest.raises(AwsPreflightError) as exc_info:
            preflight_aws(require_aws_cli=True)

        assert "aws CLI" in str(exc_info.value)

    def test_missing_packer(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("EVAL_HARNESS_AWS_PROFILE", raising=False)
        monkeypatch.delenv("AWS_PROFILE", raising=False)
        session = _ok_session()

        with _patched_aws(session), \
             patch("eval_harness.aws_auth.shutil.which", side_effect=lambda name: "/usr/bin/aws" if name == "aws" else None), \
             pytest.raises(AwsPreflightError) as exc_info:
            preflight_aws(require_packer=True, require_aws_cli=True)

        assert "packer" in str(exc_info.value)


# ---------------------------------------------------------------------------
# aws_packer.build_golden_ami calls preflight before subprocess
# ---------------------------------------------------------------------------


class TestPackerPathCallsPreflightFirst:
    """Assert that preflight_aws raises before any subprocess call."""

    def test_preflight_blocks_subprocess_when_credentials_expired(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("EVAL_HARNESS_AWS_PROFILE", "myprof")

        from eval_harness.backends import aws_packer as packer_mod

        subprocess_calls: list[str] = []

        def fake_stream_subprocess(command: list[str], **kwargs: Any) -> None:
            subprocess_calls.append(" ".join(command))

        def fake_preflight(**kwargs: Any) -> None:
            raise AwsPreflightError(
                "AWS SSO session expired or missing for profile 'myprof'. "
                "Run: aws sso login --profile myprof"
            )

        with patch.object(packer_mod, "preflight_aws", fake_preflight), \
             patch.object(packer_mod, "_stream_subprocess", fake_stream_subprocess):
            request = packer_mod.AwsPackerBuildRequest(
                target_image="debian-12",
                aws_region="us-east-1",
                subnet_id="subnet-abc",
                iam_instance_profile="EvalRole",
                packer_template_dir=tmp_path,
                distro_vars_file=tmp_path / "distro.pkrvars.hcl",
            )
            with pytest.raises(AwsPreflightError) as exc_info:
                packer_mod.build_golden_ami(request)

        # subprocess must never have been invoked
        assert subprocess_calls == [], (
            f"subprocess was called despite preflight failure: {subprocess_calls}"
        )
        assert "myprof" in str(exc_info.value)
        assert "aws sso login --profile myprof" in str(exc_info.value)
