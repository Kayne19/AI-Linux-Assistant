import base64
import json
import time
from typing import Any, Callable

import requests

from config.settings import SETTINGS

try:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding, rsa
except ImportError:  # pragma: no cover - optional until cryptography is installed
    InvalidSignature = Exception
    hashes = None
    padding = None
    rsa = None

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


def _require_cryptography():
    if hashes is None or padding is None or rsa is None:
        raise AuthConfigurationError("cryptography is required for Auth0 JWT verification.")


def _b64url_decode(value: str) -> bytes:
    padded = value + "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii"))


def _json_b64url(value: str) -> dict[str, Any]:
    try:
        return json.loads(_b64url_decode(value).decode("utf-8"))
    except Exception as exc:  # pragma: no cover - defensive parse guard
        raise AuthVerificationError("Malformed JWT segment.") from exc


def _claim_string(claims: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = claims.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


class Auth0AccessTokenVerifier:
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
        _require_cryptography()
        self.domain = (domain or SETTINGS.auth0_domain or "").strip()
        self.issuer = (issuer or SETTINGS.auth0_issuer or "").strip().rstrip("/")
        self.audience = (audience or SETTINGS.auth0_audience or "").strip()
        self.timeout_seconds = max(0.5, float(timeout_seconds))
        self._http_get = http_get or requests.get
        self._time_fn = time_fn or time.time
        self._jwks_cache: dict[str, Any] | None = None
        self._jwks_cache_expires_at = 0.0
        if not self.issuer and self.domain:
            self.issuer = f"https://{self.domain.rstrip('/')}/"
        elif self.issuer:
            self.issuer = f"{self.issuer}/"

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
        parts = (token or "").split(".")
        if len(parts) != 3:
            raise AuthVerificationError("Malformed bearer token.")

        signing_input = f"{parts[0]}.{parts[1]}".encode("ascii")
        header = _json_b64url(parts[0])
        claims = _json_b64url(parts[1])
        signature = _b64url_decode(parts[2])

        if str(header.get("alg") or "") != "RS256":
            raise AuthVerificationError("Only RS256 access tokens are accepted.")
        kid = str(header.get("kid") or "").strip()
        if not kid:
            raise AuthVerificationError("Bearer token is missing a key id.")

        self._verify_signature(kid, signing_input, signature)
        self._verify_claims(claims)
        return claims

    def _verify_signature(self, kid: str, signing_input: bytes, signature: bytes):
        jwk = self._get_signing_key(kid)
        try:
            modulus = int.from_bytes(_b64url_decode(str(jwk.get("n") or "")), "big")
            exponent = int.from_bytes(_b64url_decode(str(jwk.get("e") or "")), "big")
        except Exception as exc:
            raise AuthVerificationError("Signing key is malformed.") from exc
        public_key = rsa.RSAPublicNumbers(exponent, modulus).public_key()
        try:
            public_key.verify(signature, signing_input, padding.PKCS1v15(), hashes.SHA256())
        except InvalidSignature as exc:
            raise AuthVerificationError("Bearer token signature is invalid.") from exc

    def _verify_claims(self, claims: dict[str, Any]):
        issuer = str(claims.get("iss") or "")
        if issuer != self.issuer:
            raise AuthVerificationError("Bearer token issuer did not match.")

        audience = claims.get("aud")
        if isinstance(audience, str):
            audiences = [audience]
        elif isinstance(audience, list):
            audiences = [str(item) for item in audience]
        else:
            audiences = []
        if self.audience not in audiences:
            raise AuthVerificationError("Bearer token audience did not match the API audience.")

        token_type = str(claims.get("typ") or "").lower()
        if token_type and token_type not in {"at+jwt", "jwt"}:
            raise AuthVerificationError("Bearer token type was not an access token.")

        subject = str(claims.get("sub") or "").strip()
        if not subject:
            raise AuthVerificationError("Bearer token is missing subject.")

        now = int(self._time_fn())
        exp = claims.get("exp")
        if not isinstance(exp, (int, float)) or int(exp) <= now:
            raise AuthVerificationError("Bearer token has expired.")
        nbf = claims.get("nbf")
        if isinstance(nbf, (int, float)) and int(nbf) > now:
            raise AuthVerificationError("Bearer token is not active yet.")

    def _get_signing_key(self, kid: str) -> dict[str, Any]:
        jwks = self._get_jwks()
        for key in list(jwks.get("keys") or []):
            if str(key.get("kid") or "") == kid:
                return key
        raise AuthVerificationError("Signing key was not found in Auth0 JWKS.")

    def _get_jwks(self) -> dict[str, Any]:
        now = self._time_fn()
        if self._jwks_cache is not None and now < self._jwks_cache_expires_at:
            return self._jwks_cache

        url = f"https://{self.domain.rstrip('/')}/.well-known/jwks.json"
        response = self._http_get(url, timeout=self.timeout_seconds)
        if hasattr(response, "raise_for_status"):
            response.raise_for_status()
        payload = response.json()
        cache_control = ""
        headers = getattr(response, "headers", {}) or {}
        for key, value in headers.items():
            if str(key).lower() == "cache-control":
                cache_control = str(value or "")
                break
        ttl_seconds = 300
        for part in cache_control.split(","):
            part = part.strip().lower()
            if part.startswith("max-age="):
                try:
                    ttl_seconds = max(60, int(part.split("=", 1)[1]))
                except ValueError:
                    ttl_seconds = 300
                break
        self._jwks_cache = payload
        self._jwks_cache_expires_at = now + ttl_seconds
        return payload


def build_current_user_dependency(app_store, verifier: Auth0AccessTokenVerifier):
    if Header is None:
        raise ImportError("FastAPI is required for auth dependencies.")

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
            raise HTTPException(status_code=503, detail="Unable to fetch Auth0 JWKS.") from exc

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
