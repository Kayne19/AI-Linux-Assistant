# Ultrareview findings (recovered from crashed run r596zodeb)

The remote review crashed during the `synthesizing` stage but the underlying
verifier had already finished: 20 candidate findings → 8 confirmed, 12 refuted
(the refuted set was almost entirely duplicate reports of the same 8 issues).

Findings recovered from the streamed `<review-bug>` events in
`/tmp/claude-1000/-home-kayne19-projects-AI-Linux-Assistant/dd344f26-8464-4f4f-91e2-b4404e4edd32/tasks/r596zodeb.output`
and re-verified against the live tree on `ultrathink/fix-repo-audits`.

Descriptions in the stream are clipped at ~300 chars; the file/line + observed
code is reproduced below for each one.

---

## 1. Duplicate Alembic revision IDs — migrations will fail

**Files:**
- `Back-end/alembic/versions/20260429_0010_add_chat_session_id_to_memory_candidates.py`
- `Back-end/alembic/versions/20260429_0010_add_ingest_identity_normalizer_settings.py`

Both new migrations declare:
```
revision = "20260429_0010"
down_revision = "20260412_0009"
branch_labels = None
```

Alembic will refuse to build the version graph (`Multiple head revisions are
present` / `Revision ... is not unique`). One of them needs a fresh revision
ID and its `down_revision` repointed to the other.

---

## 2. OpenAI streaming text deltas never fire — wrong event type
**File:** `Back-end/app/providers/openAI_caller.py:318`

```python
if getattr(event, "type", None) == "response.output.text.delta":
```

The OpenAI Responses API emits `response.output_text.delta` (underscore between
`output` and `text`, not a dot). The current matcher never matches, so
`event_listener("text_delta", ...)` is never called from the stream loop. End
users get nothing visible on streaming responses through this provider.

---

## 3. Magi arbiter incremental JSON parser corrupts escape sequences on chunk boundary
**File:** `Back-end/app/agents/magi/arbiter.py:80-119` (`_extract_final_answer_incremental`)

The `while pos < len(accumulated_text)` loop handles a backslash by:

```python
if ch == "\\" and pos + 1 < len(accumulated_text):
    result_chars.append(ch)
    result_chars.append(accumulated_text[pos + 1])
    pos += 2
```

When a streaming chunk boundary lands exactly on `\`, `pos + 1` is past the end
of `accumulated_text`. The branch is skipped and the bare `\` falls through to
the `else` arm that appends `ch` and advances `pos += 1`. Next chunk starts
from the escaped character (e.g. `n`) and emits it literally. Result: a
streamed `\n` is emitted as the literal characters `\` then `n`, then the
final-pass `json.loads('"' + raw + '"')` decodes them as the two-character
sequence — but interim partial reads sent to the listener already contain the
corrupted output.

Fix: when the trailing character is `\` and there is no next char yet, leave
`pos` parked on the backslash and break (or stash it for the next call) rather
than emitting it.

---

## 4. Auth0 verifier silently ignores `http_get` and `time_fn` injection
**File:** `Back-end/app/auth/auth0.py:47-71`

`Auth0AccessTokenVerifier.__init__` still accepts `http_get` and `time_fn` and
stores them on `self._http_get` / `self._time_fn`, but the rewritten
implementation goes through `PyJWKClient` (own urllib fetch) and PyJWT's
internal time. The injectables exist purely as dead args — any tests that
relied on injecting a fake `http_get` / clock will run against the real
network/clock.

Either re-thread these through `_get_jwks_client` and the `jwt.decode(...,
leeway=...)` path, or drop the parameters and update callers/tests.

---

## 5. `auto_name` queue-failure error event is not deliverable to clients
**File:** `Back-end/app/chat_run_worker.py:480-510`

`_queue_auto_name_run` is invoked **after** `_complete_run()` has already
terminalized the parent run (`status="completed"`, lease cleared). When
`create_or_reuse_run` raises, the `except` arm calls:

```python
self._emit_event(run.id, claimed_worker_id, "error",
                 {"message": f"auto_name_queue_failed for run {run.id}"})
```

Per `RUNS.md`, an emit against a terminalized run cannot append to the event
log (the lease/append is gated on the active state). Either move the
auto-name enqueue *before* `_complete_run()` so the error can attach to the
parent run, or emit on the new auto-name run (or skip and just log).

---

## 6. `commit_resolution(chat_session_id=...)` breaks `InMemoryMemoryStore`
**Files:**
- `Back-end/app/orchestration/model_router.py:1699-1704` (caller, always passes the kwarg)
- `Back-end/app/persistence/in_memory_memory_store.py:449` (signature lacks the kwarg)
- `Back-end/app/persistence/postgres_memory_store.py:625` (signature has it)

```python
# in_memory_memory_store.py
def commit_resolution(self, resolution, user_question="", assistant_response=""):
```

vs.

```python
# model_router.py
self.memory_store.commit_resolution(
    turn.memory_resolution,
    user_question=turn.user_question,
    assistant_response=turn.response,
    chat_session_id=self.chat_session_id or "",
)
```

Any test/dev path that uses `InMemoryMemoryStore` will raise
`TypeError: commit_resolution() got an unexpected keyword argument 'chat_session_id'`
when memory is actually committed. Add the kwarg (and ignore or store it) on
the in-memory store.

---

## 7. `conflict_staleness_days` does not actually check staleness
**File:** `Back-end/app/agents/memory_resolver.py:228-249`

Per `MEMORY.md`, the new flag should auto-resolve a conflicted mutable fact
*only after it has remained unresolved for N days*. The implementation:

```python
if (
    self._fact_is_mutable(key)
    and self.conflict_staleness_days is not None
    and confidence >= self.fact_commit_confidence
):
    # marks the existing value superseded immediately
    ...
```

It never compares the existing fact's age (or last-seen / first-conflict
timestamp) against `conflict_staleness_days` — the value being non-null is
treated as the trigger. Any conflict on a mutable fact gets auto-resolved on
the next turn regardless of how recently it was committed. Needs to compare a
real timestamp against `now - timedelta(days=conflict_staleness_days)`.

---

## 8. LanceDB LIKE-prefix helpers don't escape SQL wildcards (`_`, `%`)
**File:** `Back-end/app/retrieval/store.py:148-156`

```python
def delete_by_id_prefix(self, prefix: str) -> int:
    escaped = prefix.replace("'", "''")
    return self.delete_by_predicate(f"id LIKE '{escaped}%'")

def count_rows_by_id_prefix(self, prefix: str) -> int:
    escaped = prefix.replace("'", "''")
    return self.count_rows_matching(f"id LIKE '{escaped}%'")
```

Only single quotes are escaped. The indexer builds prefixes like
`vec_{source_key}_` and `source_key` contains underscores routinely — `_`
matches any single character in SQL `LIKE`. So `count_rows_by_id_prefix("vec_a_b_")`
also matches `vec_aXbY...` rows owned by other sources. Risk:
`delete_by_id_prefix` deletes another source's chunks; the idempotency
counter over-counts and wrongly skips a re-index.

Fix: also escape `_` and `%` and use `LIKE '...' ESCAPE '\\'` (or switch to a
prefix-anchored equality like `starts_with(id, ...)` if LanceDB exposes one).

---

## Refuted findings (deduped)

The verifier marked 12 reports as `refuted`, but cross-referencing the
finding text shows they were duplicate descriptions of the 8 above (e.g.
`bug_002`, `bug_003`, `bug_014`, `bug_017` are all the same Alembic issue;
`bug_009` / `bug_018` are both the arbiter parser; `bug_013` is the same
OpenAI event-type issue; etc.). No genuinely-falsified findings were lost
in the crash, with one caveat:

The **synthesis** step (the part that crashed) is what would have ranked
severity, dropped near-duplicates, and added cross-file context. The eight
items above are the raw verified set — re-running `/ultrareview` on this
branch should give a polished report.
