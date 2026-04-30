import re
import time
from typing import Any, Callable

import jwt
from jwt import PyJWKClient, PyJWKClientError

import requests

from config.settings import SETTINGS

try:
    from fastapi import Header, HTTPException
except ImportError:  # pragma: no cover - optional until FastAPI is installed
    Header = None
    HTTPException = Exception


AUTH_PROVIDER_AUTH0 = "auth0"


class AuthConfigurationError(RuntimeError):
    pass


class AuthVerificationError(RuntimeError):
    pass


def _claim_string(claims: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = claims.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


class Auth0AccessTokenVerifier:
    """Verify Auth0 RS256 access tokens using pyjwt + PyJWKClient.

    Replaces the hand-rolled cryptography-based verification with the
    standard PyJWT library, which handles key fetching, signature
    verification, and standard claims validation (iss, aud, exp, nbf)
    in a single jwt.decode() call.
    """

    def __init__(
        self,
        *,
        domain: str | None = None,
        issuer: str | None = None,
        audience: str | None = None,
        timeout_seconds: float = 5.0,
        http_get: Callable[..., Any] | None = None,
        time_fn: Callable[[], float] | None = None,
    ):
        self.domain = (domain or SETTINGS.auth0_domain or "").strip()
        self.audience = (audience or SETTINGS.auth0_audience or "").strip()
        self.timeout_seconds = max(0.5, float(timeout_seconds))
        self._http_get = http_get or requests.get
        self._time_fn = time_fn or time.time

        # Issuer normalization: Auth0 issuers end with a trailing slash.
        raw_issuer = (issuer or SETTINGS.auth0_issuer or "").strip()
        if not raw_issuer and self.domain:
            raw_issuer = f"https://{self.domain.rstrip('/')}/"
        self.issuer = raw_issuer.rstrip("/") + "/"

        # JWKS URL
        self._jwks_url = f"https://{self.domain.rstrip('/')}/.well-known/jwks.json"
        self._jwks_client: PyJWKClient | None = None

    def _get_jwks_client(self) -> PyJWKClient:
        if self._jwks_client is None:
            self._jwks_client = PyJWKClient(
                self._jwks_url,
                cache_jwk_set=True,
                lifespan=max(60, int(self.timeout_seconds) * 60),
            )
        return self._jwks_client

    def validate_configuration(self):
        missing = []
        if not self.domain:
            missing.append("AUTH0_DOMAIN")
        if not self.issuer:
            missing.append("AUTH0_ISSUER")
        if not self.audience:
            missing.append("AUTH0_AUDIENCE")
        if missing:
            raise AuthConfigurationError(
                f"Auth0 access-token auth is enabled but required settings are missing: {', '.join(missing)}."
            )

    def verify_access_token(self, token: str) -> dict[str, Any]:
        self.validate_configuration()

        if not token or token.count(".") != 2:
            raise AuthVerificationError("Malformed bearer token.")

        try:
            jwks_client = self._get_jwks_client()
            signing_key = jwks_client.get_signing_key_from_jwt(token)
        except PyJWKClientError as exc:
            raise AuthVerificationError(
                "Signing key was not found in Auth0 JWKS."
            ) from exc
        except Exception as exc:
            raise AuthVerificationError("Unable to fetch Auth0 JWKS.") from exc

        try:
            claims = jwt.decode(
                token,
                key=signing_key,
                algorithms=["RS256"],
                issuer=self.issuer,
                audience=self.audience,
                options={
                    "require": ["exp", "iss", "sub", "aud"],
                    "verify_exp": True,
                    "verify_iss": True,
                    "verify_sub": True,
                    "verify_aud": True,
                    "verify_nbf": True,
                },
            )
        except jwt.ExpiredSignatureError as exc:
            raise AuthVerificationError("Bearer token has expired.") from exc
        except jwt.ImmatureSignatureError as exc:
            raise AuthVerificationError("Bearer token is not active yet.") from exc
        except jwt.InvalidIssuerError as exc:
            raise AuthVerificationError("Bearer token issuer did not match.") from exc
        except jwt.InvalidAudienceError as exc:
            raise AuthVerificationError(
                "Bearer token audience did not match the API audience."
            ) from exc
        except jwt.MissingRequiredClaimError as exc:
            raise AuthVerificationError(str(exc)) from exc
        except jwt.InvalidSignatureError as exc:
            raise AuthVerificationError("Bearer token signature is invalid.") from exc
        except jwt.DecodeError as exc:
            raise AuthVerificationError("Bearer token could not be decoded.") from exc

        # PyJWT does not validate the 'typ' claim, so we add that check.
        token_type = str(claims.get("typ") or "").lower()
        if token_type and token_type not in {"at+jwt", "jwt"}:
            raise AuthVerificationError("Bearer token type was not an access token.")

        return claims


def build_current_user_dependency(
    app_store, verifier: Auth0AccessTokenVerifier, *, scopes: list[str] | None = None
):
    """Build a FastAPI dependency that verifies the bearer token and returns the user.

    When *scopes* is provided the dependency also verifies that the token
    includes every listed scope (checked against the 'scope' claim, a
    space-delimited string per RFC 8693).
    """
    if Header is None:
        raise ImportError("FastAPI is required for auth dependencies.")

    required_scopes = list(scopes or [])

    def _http_401(detail: str):
        return HTTPException(status_code=401, detail=detail)

    def dependency(authorization: str | None = Header(default=None)):
        header = (authorization or "").strip()
        if not header:
            raise _http_401("Missing bearer token.")
        scheme, _, token = header.partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            raise _http_401("Invalid authorization header.")
        try:
            claims = verifier.verify_access_token(token.strip())
        except AuthVerificationError as exc:
            raise _http_401(str(exc)) from exc
        except requests.RequestException as exc:
            raise HTTPException(
                status_code=503, detail="Unable to fetch Auth0 JWKS."
            ) from exc

        # Scope check
        if required_scopes:
            token_scopes = set(
                re.split(r"\s+", str(claims.get("scope", "") or "").strip())
            )
            missing = set(required_scopes) - token_scopes
            if missing:
                raise HTTPException(
                    status_code=403,
                    detail=f"Missing required scopes: {', '.join(sorted(missing))}",
                )

        subject = str(claims.get("sub") or "").strip()
        display_name = _claim_string(claims, "name", "nickname")
        email = _claim_string(claims, "email")
        avatar_url = _claim_string(claims, "picture")
        email_verified = bool(claims.get("email_verified"))
        user = app_store.find_or_create_auth_user(
            auth_provider=AUTH_PROVIDER_AUTH0,
            auth_subject=subject,
            email=email,
            email_verified=email_verified,
            display_name=display_name,
            avatar_url=avatar_url,
        )
        return user

    return dependency
