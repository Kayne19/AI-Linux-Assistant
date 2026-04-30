import base64
import json

from jwt import PyJWK

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from auth import Auth0AccessTokenVerifier, AuthVerificationError


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _json_b64(value) -> str:
    return _b64url(
        json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8")
    )


def _public_jwk(private_key, kid="test-key"):
    public_numbers = private_key.public_key().public_numbers()
    modulus = public_numbers.n.to_bytes((public_numbers.n.bit_length() + 7) // 8, "big")
    exponent = public_numbers.e.to_bytes(
        (public_numbers.e.bit_length() + 7) // 8, "big"
    )
    return {
        "kty": "RSA",
        "kid": kid,
        "alg": "RS256",
        "use": "sig",
        "n": _b64url(modulus),
        "e": _b64url(exponent),
    }


def _sign_token(private_key, claims, kid="test-key"):
    header = {"alg": "RS256", "typ": "JWT", "kid": kid}
    signing_input = f"{_json_b64(header)}.{_json_b64(claims)}".encode("utf-8")
    signature = private_key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    return f"{signing_input.decode('utf-8')}.{_b64url(signature)}"


def test_auth0_verifier_accepts_valid_access_token():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    claims = {
        "iss": "https://tenant.example.com/",
        "sub": "auth0|user-1",
        "aud": "https://ai-linux-assistant-api",
        "exp": 2_000_000_000,
    }
    token = _sign_token(private_key, claims)
    verifier = Auth0AccessTokenVerifier(
        domain="tenant.example.com",
        issuer="https://tenant.example.com/",
        audience="https://ai-linux-assistant-api",
        signing_key=PyJWK(_public_jwk(private_key)),
    )

    verified_claims = verifier.verify_access_token(token)

    assert verified_claims["sub"] == "auth0|user-1"


def test_auth0_verifier_rejects_wrong_audience():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    claims = {
        "iss": "https://tenant.example.com/",
        "sub": "auth0|user-2",
        "aud": "spa-client-id",
        "exp": 2_000_000_000,
    }
    token = _sign_token(private_key, claims)
    verifier = Auth0AccessTokenVerifier(
        domain="tenant.example.com",
        issuer="https://tenant.example.com/",
        audience="https://ai-linux-assistant-api",
        signing_key=PyJWK(_public_jwk(private_key)),
    )

    try:
        verifier.verify_access_token(token)
        assert False, "expected audience mismatch"
    except AuthVerificationError as exc:
        assert "audience" in str(exc).lower()


def test_auth0_verifier_rejects_expired_or_bad_signature_tokens():
    import time

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    expired_token = _sign_token(
        private_key,
        {
            "iss": "https://tenant.example.com/",
            "sub": "auth0|user-3",
            "aud": "https://ai-linux-assistant-api",
            "exp": int(time.time()) - 3600,
        },
    )
    verifier = Auth0AccessTokenVerifier(
        domain="tenant.example.com",
        issuer="https://tenant.example.com/",
        audience="https://ai-linux-assistant-api",
        signing_key=PyJWK(_public_jwk(private_key)),
    )

    try:
        verifier.verify_access_token(expired_token)
        assert False, "expected expiration failure"
    except AuthVerificationError as exc:
        assert "expired" in str(exc).lower()

    valid_token = _sign_token(
        private_key,
        {
            "iss": "https://tenant.example.com/",
            "sub": "auth0|user-4",
            "aud": "https://ai-linux-assistant-api",
            "exp": 2_000_000_000,
        },
    )
    bad_signature_token = (
        valid_token.rsplit(".", 1)[0] + "." + _b64url(b"not-a-real-signature")
    )
    try:
        verifier.verify_access_token(bad_signature_token)
        assert False, "expected signature failure"
    except AuthVerificationError as exc:
        assert "signature" in str(exc).lower()
