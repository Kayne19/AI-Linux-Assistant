"""Auth0 Machine-to-Machine (client credentials) token provider.

Holds client credentials for one subject, fetches a token on demand via the
``client_credentials`` grant, and caches it until ``exp - refresh_skew_seconds``.
Thread-safe: the adapter's ``_wait_for_terminal_run`` polling loop calls
``get_access_token()`` on every request via ``_headers()``.
"""
from __future__ import annotations

import base64
import json
import threading
import time
from dataclasses import dataclass, field
from typing import Any

import requests


def _decode_unverified_jwt_exp(token: str) -> int | None:
    """Return the ``exp`` claim from an unverified JWT, or None on failure."""
    parts = token.split(".")
    if len(parts) != 3 or not parts[1]:
        return None
    payload = parts[1]
    padding = "=" * (-len(payload) % 4)
    try:
        decoded = base64.urlsafe_b64decode(payload + padding)
        parsed = json.loads(decoded.decode("utf-8"))
    except (ValueError, json.JSONDecodeError):
        return None
    exp = parsed.get("exp")
    if isinstance(exp, int):
        return exp
    if isinstance(exp, float):
        return int(exp)
    return None


@dataclass(frozen=True)
class ClientCreds:
    client_id: str
    client_secret: str


@dataclass(frozen=True)
class Auth0M2MConfig:
    token_url: str
    audience: str
    clients_by_subject: dict[str, ClientCreds] = field(default_factory=dict)
    refresh_skew_seconds: int = 60
    scope: str | None = None
    organization: str | None = None


class Auth0M2MTokenProvider:
    """Fetches and caches an Auth0 client-credentials access token for one subject.

    Constructor arguments:
        token_url: Auth0 ``/oauth/token`` endpoint, e.g.
            ``https://dev-abc123.us.auth0.com/oauth/token``.
        audience: API audience registered in Auth0.
        client_id: M2M application client ID.
        client_secret: M2M application client secret.
        scope: Optional space-separated scope string.
        organization: Optional Auth0 organization ID.
        refresh_skew_seconds: How many seconds before ``exp`` to treat a
            cached token as stale (default 60).
    """

    def __init__(
        self,
        *,
        token_url: str,
        audience: str,
        client_id: str,
        client_secret: str,
        scope: str | None = None,
        organization: str | None = None,
        refresh_skew_seconds: int = 60,
    ) -> None:
        self._token_url = token_url
        self._audience = audience
        self._client_id = client_id
        self._client_secret = client_secret
        self._scope = scope
        self._organization = organization
        self._refresh_skew_seconds = refresh_skew_seconds
        self._lock = threading.Lock()
        self._cached_token: str | None = None
        self._cached_exp: int = 0

    def get_access_token(self) -> str:
        """Return a valid access token, fetching a new one if needed.

        The cached token is reused while ``now + refresh_skew < exp``.
        On error, raises ``RuntimeError`` with the Auth0 ``error`` and
        ``error_description`` fields verbatim so operators can act on them.
        """
        with self._lock:
            now = int(time.time())
            if self._cached_token and now + self._refresh_skew_seconds < self._cached_exp:
                return self._cached_token
            token = self._fetch_token()
            exp = _decode_unverified_jwt_exp(token)
            self._cached_token = token
            self._cached_exp = exp if exp is not None else now + 3600
            return self._cached_token

    def _fetch_token(self) -> str:
        data: dict[str, Any] = {
            "grant_type": "client_credentials",
            "client_id": self._client_id,
            "client_secret": self._client_secret,
            "audience": self._audience,
        }
        if self._scope:
            data["scope"] = self._scope
        if self._organization:
            data["organization"] = self._organization

        try:
            response = requests.post(
                self._token_url,
                data=data,
                timeout=15,
            )
        except requests.RequestException as exc:
            raise RuntimeError(f"Auth0 token request failed: {exc}") from exc

        if not response.ok:
            try:
                body = response.json()
                error = body.get("error", "unknown_error")
                description = body.get("error_description", response.text)
            except ValueError:
                error = "http_error"
                description = response.text
            raise RuntimeError(
                f"Auth0 token request failed ({response.status_code}): "
                f"error={error!r} error_description={description!r}"
            )

        try:
            body = response.json()
        except ValueError as exc:
            raise RuntimeError(
                f"Auth0 returned non-JSON body: {response.text[:200]!r}"
            ) from exc

        token = body.get("access_token")
        if not token or not isinstance(token, str):
            raise RuntimeError(
                f"Auth0 response did not contain access_token: {body!r}"
            )
        return token
