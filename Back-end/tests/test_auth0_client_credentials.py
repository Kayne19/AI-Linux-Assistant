"""Tests proving Auth0 M2M (client_credentials) JWTs are accepted by the backend.

The ``sub`` claim for M2M tokens takes the form ``<client_id>@clients`` with
no email / name / profile claims.  ``find_or_create_auth_user`` provisions a
fresh user row transparently, keyed on ``(auth_provider, auth_subject)``.
"""

from __future__ import annotations

import base64
import json
import os
import tempfile
import time


os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from jwt import PyJWK

from auth.auth0 import Auth0AccessTokenVerifier
from persistence.database import Base
from persistence.postgres_app_store import PostgresAppStore
from persistence.postgres_models import User

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


# ---------------------------------------------------------------------------
# JWT helpers (mirrors test_auth0_auth.py approach)
# ---------------------------------------------------------------------------


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _json_b64url(payload) -> str:
    return _b64url(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    )


def _rsa_jwk(public_key, kid="m2m-key"):
    numbers = public_key.public_numbers()
    return {
        "kty": "RSA",
        "kid": kid,
        "use": "sig",
        "alg": "RS256",
        "n": _b64url(numbers.n.to_bytes((numbers.n.bit_length() + 7) // 8, "big")),
        "e": _b64url(numbers.e.to_bytes((numbers.e.bit_length() + 7) // 8, "big")),
    }


def _sign_token(private_key, payload, kid="m2m-key"):
    header = {"alg": "RS256", "typ": "JWT", "kid": kid}
    signing_input = f"{_json_b64url(header)}.{_json_b64url(payload)}".encode("ascii")
    signature = private_key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    return f"{signing_input.decode('ascii')}.{_b64url(signature)}"


class _Response:
    def __init__(self, payload):
        self._payload = payload
        self.headers = {"Cache-Control": "max-age=600"}

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def _build_stores():
    fd, db_path = tempfile.mkstemp(suffix=".sqlite3")
    os.close(fd)
    engine = create_engine(
        f"sqlite+pysqlite:///{db_path}",
        future=True,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    return PostgresAppStore(session_factory=session_factory), session_factory


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_verifier_accepts_client_credentials_jwt():
    """A JWT with sub='abcd1234@clients' and no profile claims must be accepted."""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = _rsa_jwk(private_key.public_key())
    now = int(time.time())

    token = _sign_token(
        private_key,
        {
            "iss": "https://tenant.example.com/",
            "sub": "abcd1234@clients",
            "aud": "https://api.example.com",
            "exp": now + 300,
            "iat": now,
            # No email, name, or picture claims — M2M tokens omit profile fields.
        },
    )

    verifier = Auth0AccessTokenVerifier(
        domain="tenant.example.com",
        issuer="https://tenant.example.com/",
        audience="https://api.example.com",
        signing_key=PyJWK(jwk),
    )

    claims = verifier.verify_access_token(token)

    assert claims["sub"] == "abcd1234@clients"
    assert claims["aud"] == "https://api.example.com"
    # Verifier must not require profile claims.
    assert "email" not in claims
    assert "name" not in claims


def test_protected_route_provisions_service_principal_user():
    """POST /projects with an M2M token must create a User row for the client sub."""
    from auth import AuthVerificationError, build_current_user_dependency

    app_store, session_factory = _build_stores()

    m2m_sub = "abcd1234@clients"
    m2m_token = "m2m-bearer-token"

    class _FakeVerifier:
        def verify_access_token(self, token):
            if token != m2m_token:
                raise AuthVerificationError("Invalid token.")
            # Simulate the claims an M2M token carries — no profile fields.
            return {
                "sub": m2m_sub,
                "aud": "https://api.example.com",
                "iss": "https://tenant.example.com/",
                "exp": int(time.time()) + 300,
            }

    verifier = _FakeVerifier()
    dependency = build_current_user_dependency(app_store, verifier)

    # Resolve the current_user from the Authorization header value.
    # build_current_user_dependency returns a FastAPI dependency that accepts
    # an ``authorization`` header string.
    current_user = dependency(authorization=f"Bearer {m2m_token}")

    assert current_user is not None
    assert current_user.auth_provider == "auth0"
    assert current_user.auth_subject == m2m_sub

    # Now create a project on behalf of this user (mimics POST /projects).
    project = app_store.create_project(current_user.id, "m2m-test-project")
    assert project.user_id == current_user.id

    # Verify the User row in the DB is keyed on (auth_provider, auth_subject).
    with session_factory() as session:
        stored = session.scalar(
            __import__("sqlalchemy")
            .select(User)
            .where(
                User.auth_provider == "auth0",
                User.auth_subject == m2m_sub,
            )
        )
    assert stored is not None
    assert stored.auth_subject == m2m_sub
    # M2M users have no email or display name.
    assert stored.email == "" or stored.email is None
