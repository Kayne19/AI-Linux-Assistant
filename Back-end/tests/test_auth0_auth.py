import base64
import json
import time

from jwt import PyJWK

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from auth.auth0 import Auth0AccessTokenVerifier, AuthVerificationError


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _json_b64url(payload) -> str:
    return _b64url(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    )


def _rsa_jwk(public_key, kid="test-key"):
    numbers = public_key.public_numbers()
    return {
        "kty": "RSA",
        "kid": kid,
        "use": "sig",
        "alg": "RS256",
        "n": _b64url(numbers.n.to_bytes((numbers.n.bit_length() + 7) // 8, "big")),
        "e": _b64url(numbers.e.to_bytes((numbers.e.bit_length() + 7) // 8, "big")),
    }


def _sign_token(private_key, payload, kid="test-key"):
    header = {"alg": "RS256", "typ": "JWT", "kid": kid}
    signing_input = f"{_json_b64url(header)}.{_json_b64url(payload)}".encode("ascii")
    signature = private_key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    return f"{signing_input.decode('ascii')}.{_b64url(signature)}"


def test_auth0_verifier_accepts_valid_access_token():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = int(time.time())
    token = _sign_token(
        private_key,
        {
            "iss": "https://tenant.example.com/",
            "sub": "auth0|user-1",
            "aud": "https://api.example.com",
            "exp": now + 300,
            "iat": now,
            "scope": "openid profile email",
        },
    )
    verifier = Auth0AccessTokenVerifier(
        domain="tenant.example.com",
        issuer="https://tenant.example.com/",
        audience="https://api.example.com",
        signing_key=PyJWK(_rsa_jwk(private_key.public_key())),
    )

    claims = verifier.verify_access_token(token)

    assert claims["sub"] == "auth0|user-1"
    assert claims["aud"] == "https://api.example.com"


def test_auth0_verifier_rejects_wrong_audience_and_expired_tokens():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = int(time.time())
    verifier = Auth0AccessTokenVerifier(
        domain="tenant.example.com",
        issuer="https://tenant.example.com/",
        audience="https://api.example.com",
        signing_key=PyJWK(_rsa_jwk(private_key.public_key())),
    )

    wrong_audience = _sign_token(
        private_key,
        {
            "iss": "https://tenant.example.com/",
            "sub": "auth0|user-1",
            "aud": "frontend-client-id",
            "exp": now + 300,
            "iat": now,
        },
    )
    expired = _sign_token(
        private_key,
        {
            "iss": "https://tenant.example.com/",
            "sub": "auth0|user-1",
            "aud": "https://api.example.com",
            "exp": now - 1,
            "iat": now - 600,
        },
    )

    try:
        verifier.verify_access_token(wrong_audience)
        assert False, "expected wrong audience rejection"
    except AuthVerificationError as exc:
        assert "audience" in str(exc).lower()

    try:
        verifier.verify_access_token(expired)
        assert False, "expected expiry rejection"
    except AuthVerificationError as exc:
        assert "expired" in str(exc).lower()


def test_auth0_verifier_rejects_bad_signature():
    first_private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    second_private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = int(time.time())
    verifier = Auth0AccessTokenVerifier(
        domain="tenant.example.com",
        issuer="https://tenant.example.com/",
        audience="https://api.example.com",
        signing_key=PyJWK(_rsa_jwk(first_private_key.public_key())),
    )
    token = _sign_token(
        second_private_key,
        {
            "iss": "https://tenant.example.com/",
            "sub": "auth0|user-1",
            "aud": "https://api.example.com",
            "exp": now + 300,
            "iat": now,
        },
    )

    try:
        verifier.verify_access_token(token)
        assert False, "expected signature rejection"
    except AuthVerificationError as exc:
        assert "signature" in str(exc).lower()
