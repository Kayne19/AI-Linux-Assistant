# Progress

## Status

Done — all 4 bugs fixed, tests pass.

## Tasks

- [x] Bug 1: Alembic duplicate revision IDs — renamed `20260429_0010_add_ingest_identity_normalizer_settings.py` to `..._0011`, repointed `down_revision` to `20260429_0010`
- [x] Bug 2: OpenAI streaming text deltas — changed `"response.output.text.delta"` to `"response.output_text.delta"` in `openAI_caller.py:318`
- [x] Bug 3: Magi arbiter incremental JSON parser — added `elif ch == "\\"` break when backslash is last char in chunk, parking `pos` for next call
- [x] Bug 4: Auth0 verifier ignores injectables — dropped `http_get`/`time_fn`, added optional `signing_key: PyJWK` parameter for testability

## Files Changed

- `Back-end/alembic/versions/20260429_0011_add_ingest_identity_normalizer_settings.py` (renamed from `..._0010_...`)
- `Back-end/app/providers/openAI_caller.py`
- `Back-end/app/agents/magi/arbiter.py`
- `Back-end/app/auth/auth0.py`
- `Back-end/tests/test_auth0.py`
- `Back-end/tests/test_auth0_auth.py`
- `Back-end/tests/test_auth0_client_credentials.py`

## Validation

- `tests/test_auth0.py` — 3 passed
- `tests/test_auth0_auth.py` — 3 passed
- `tests/test_auth0_client_credentials.py` — 1 passed
- `tests/test_magi_system.py` — 47 passed
- **Total: 54 passed, 0 failed**
