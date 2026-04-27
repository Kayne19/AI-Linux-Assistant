"""Tests for Auth0M2MTokenProvider."""
from __future__ import annotations

import base64
import json
import time
from unittest.mock import MagicMock, patch, call

import pytest

from eval_harness.adapters.auth0_m2m import Auth0M2MTokenProvider


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_jwt(exp: int, client_id: str = "client-a") -> str:
    """Return a minimal unsigned JWT-shaped string with the given exp."""
    header = base64.urlsafe_b64encode(
        json.dumps({"alg": "RS256", "typ": "JWT"}).encode()
    ).rstrip(b"=").decode()
    payload = base64.urlsafe_b64encode(
        json.dumps({"sub": f"{client_id}@clients", "exp": exp}).encode()
    ).rstrip(b"=").decode()
    return f"{header}.{payload}.fakesig"


def _mock_response(token: str, status: int = 200, error_body: dict | None = None):
    resp = MagicMock()
    resp.ok = status < 400
    resp.status_code = status
    if error_body is not None:
        resp.json.return_value = error_body
        resp.text = json.dumps(error_body)
    else:
        resp.json.return_value = {"access_token": token, "token_type": "Bearer", "expires_in": 86400}
        resp.text = json.dumps(resp.json.return_value)
    return resp


def _provider(**kwargs) -> Auth0M2MTokenProvider:
    defaults = dict(
        token_url="https://dev-test.us.auth0.com/oauth/token",
        audience="https://api.example.com",
        client_id="client-a",
        client_secret="secret-a",
    )
    defaults.update(kwargs)
    return Auth0M2MTokenProvider(**defaults)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_token_fetch_and_cache():
    """First call hits the token endpoint; second call within skew uses the cache."""
    now = int(time.time())
    exp = now + 3600
    token = _make_jwt(exp)

    with patch("eval_harness.adapters.auth0_m2m.requests.post", return_value=_mock_response(token)) as mock_post:
        provider = _provider(refresh_skew_seconds=60)

        t1 = provider.get_access_token()
        t2 = provider.get_access_token()

    assert t1 == token
    assert t2 == token
    assert mock_post.call_count == 1, "second call should use the cache"


def test_refresh_at_skew_boundary():
    """A second call after the skew boundary triggers a fresh fetch."""
    now = int(time.time())
    exp = now + 3600
    token_a = _make_jwt(exp, client_id="client-a")

    # Second token has exp well into the future
    exp_b = now + 7200
    token_b = _make_jwt(exp_b, client_id="client-a")

    responses = [_mock_response(token_a), _mock_response(token_b)]

    with patch("eval_harness.adapters.auth0_m2m.requests.post", side_effect=responses) as mock_post:
        provider = _provider(refresh_skew_seconds=60)

        t1 = provider.get_access_token()
        assert t1 == token_a

        # Manually wind the clock so now + skew >= exp
        provider._cached_exp = now + 30  # exp is within skew

        t2 = provider.get_access_token()

    assert t2 == token_b
    assert mock_post.call_count == 2


def test_long_polling_refresh():
    """Simulate ~10 polling calls crossing one token expiry boundary.

    Expect exactly one token refresh (two total fetches) and no exceptions.
    """
    now = int(time.time())
    # Token A expires in 5 simulated steps
    exp_a = now + 5
    token_a = _make_jwt(exp_a)

    exp_b = now + 3600
    token_b = _make_jwt(exp_b)

    responses = [_mock_response(token_a), _mock_response(token_b)]

    with patch("eval_harness.adapters.auth0_m2m.requests.post", side_effect=responses) as mock_post:
        provider = _provider(refresh_skew_seconds=0)  # no skew for precision

        results = []
        for i in range(10):
            if i == 5:
                # Simulate time advancing past expiry
                provider._cached_exp = now - 1
            results.append(provider.get_access_token())

    # First 5 calls → token_a, remaining → token_b
    assert results[:5] == [token_a] * 5
    assert results[5:] == [token_b] * 5
    assert mock_post.call_count == 2, "exactly one refresh should occur"


def test_audience_mismatch_surfaces_auth0_error():
    """A 403 from Auth0 with error/error_description fields is re-raised verbatim."""
    error_body = {
        "error": "access_denied",
        "error_description": "Client is not authorized to access the API. Ensure this client is permitted.",
    }
    resp = _mock_response("unused", status=403, error_body=error_body)

    with patch("eval_harness.adapters.auth0_m2m.requests.post", return_value=resp):
        provider = _provider()
        with pytest.raises(RuntimeError) as exc_info:
            provider.get_access_token()

    msg = str(exc_info.value)
    assert "access_denied" in msg
    assert "Client is not authorized to access the API. Ensure this client is permitted." in msg


def test_per_subject_client_isolation():
    """Three providers with distinct client_ids produce three distinct cached tokens."""
    now = int(time.time())
    exp = now + 3600

    client_ids = ["client-x", "client-y", "client-z"]

    def _make_side_effect(cid):
        token = _make_jwt(exp, client_id=cid)

        def _post(url, data=None, **kwargs):
            assert data.get("client_id") == cid
            return _mock_response(token)

        return _post, token

    providers = []
    expected_tokens = []
    for cid in client_ids:
        side_effect, expected = _make_side_effect(cid)
        expected_tokens.append(expected)
        with patch("eval_harness.adapters.auth0_m2m.requests.post", side_effect=side_effect):
            p = _provider(client_id=cid, client_secret=f"secret-{cid}")
            t = p.get_access_token()
            assert t == expected, f"Token mismatch for {cid}"
            providers.append((p, expected))

    # Verify all three are distinct
    tokens = [tok for _, tok in providers]
    assert len(set(tokens)) == 3, "Each subject should produce a distinct cached token"
