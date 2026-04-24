"""Tests for the context enrichment split (T9).

Covers:
- build_enrichment_requests filter + custom_id stability
- enrich_sync mutates elements and aggregates cache metrics
- enrich_batch_prepare produces valid /v1/responses envelopes
- enrich_batch_submit / enrich_batch_poll thin-wrapper delegation
- enrich_batch_merge resolves custom_ids, tolerates errors, captures usage
- enrich_elements legacy wrapper still writes _final.json and returns a result
"""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from ingestion.stages.context_enrichment import (
    EnrichmentRequest,
    EnrichmentResult,
    build_enrichment_requests,
    enrich_batch_merge,
    enrich_batch_poll,
    enrich_batch_prepare,
    enrich_batch_submit,
    enrich_elements,
    enrich_sync,
)


# ---------------------------------------------------------------------------
# build_enrichment_requests
# ---------------------------------------------------------------------------

def test_build_requests_filters_by_type_and_min_length():
    elements = [
        {"type": "Title", "text": "Heading, too short and wrong type"},
        {"type": "NarrativeText", "text": "x" * 40},                   # too short
        {"type": "NarrativeText", "text": "x" * 80},                   # keep
        {"type": "ListItem", "text": "y" * 60},                        # keep
        {"type": "UncategorizedText", "text": "z" * 60},               # keep
        {"type": "Table", "text": "w" * 500},                          # wrong type
    ]
    requests = build_enrichment_requests(elements, "doc context", model="m")
    indices = [r.element_index for r in requests]
    assert indices == [2, 3, 4]


def test_build_requests_custom_id_is_stable_and_unique():
    elements = [
        {"type": "NarrativeText", "text": "alpha " * 20},
        {"type": "NarrativeText", "text": "beta " * 20},
    ]
    a = build_enrichment_requests(elements, "ctx", model="m")
    b = build_enrichment_requests(elements, "ctx", model="m")
    assert [r.custom_id for r in a] == [r.custom_id for r in b]
    assert a[0].custom_id != a[1].custom_id
    assert all(r.custom_id.startswith("chunk-") for r in a)


def test_build_requests_shares_one_system_prompt_for_prompt_caching():
    elements = [
        {"type": "NarrativeText", "text": "a" * 80},
        {"type": "NarrativeText", "text": "b" * 80},
    ]
    requests = build_enrichment_requests(elements, "doc context", model="m")
    assert len(requests) == 2
    assert requests[0].system_prompt == requests[1].system_prompt


def test_build_requests_empty_elements_returns_empty_list():
    assert build_enrichment_requests([], "ctx", model="m") == []


def test_build_requests_respects_overrides():
    elements = [{"type": "NarrativeText", "text": "x" * 80}]
    requests = build_enrichment_requests(
        elements, "ctx", model="m", temperature=0.5, max_output_tokens=42
    )
    assert requests[0].temperature == 0.5
    assert requests[0].max_output_tokens == 42


# ---------------------------------------------------------------------------
# enrich_sync
# ---------------------------------------------------------------------------

class _StubWorker:
    """Minimal stand-in for a provider worker."""

    def __init__(self, responder=None, usage=None):
        self._responder = responder or (lambda req_idx: f"context-{req_idx}")
        self._calls = 0
        self._usage = usage or {"cached_tokens": 0, "input_tokens": 10, "output_tokens": 5}

    def generate_text(self, *, system_prompt, user_message, history, temperature,
                      max_output_tokens, event_listener, cache_config):
        idx = self._calls
        self._calls += 1
        if event_listener is not None:
            event_listener("prompt_cache_metrics", {
                "provider": "openai",
                "round": 0,
                **self._usage,
            })
        return self._responder(idx)


def _sample_elements():
    return [
        {"type": "NarrativeText", "text": "A" * 60},
        {"type": "NarrativeText", "text": "B" * 60},
    ]


def test_enrich_sync_mutates_elements_with_ai_context_and_embedding_text():
    elements = _sample_elements()
    requests = build_enrichment_requests(elements, "ctx", model="m")
    worker = _StubWorker(responder=lambda i: f"summary-{i}")

    result = enrich_sync(requests, elements, worker)

    assert result.completed_count == 2
    assert result.error_count == 0
    assert elements[0]["metadata"]["ai_context"] == "summary-0"
    assert elements[1]["metadata"]["ai_context"] == "summary-1"
    assert elements[0]["metadata"]["embedding_text"].startswith("CONTEXT: summary-0")
    assert "CONTENT: " + ("A" * 60) in elements[0]["metadata"]["embedding_text"]


def test_enrich_sync_aggregates_cache_metrics():
    elements = _sample_elements()
    requests = build_enrichment_requests(elements, "ctx", model="m")
    worker = _StubWorker(usage={"cached_tokens": 7, "input_tokens": 100, "output_tokens": 20})

    result = enrich_sync(requests, elements, worker)

    assert result.cache_metrics == {"cached_tokens": 14, "input_tokens": 200, "output_tokens": 40}


def test_enrich_sync_records_errors_and_continues():
    elements = _sample_elements()
    requests = build_enrichment_requests(elements, "ctx", model="m")

    class _FailFirst(_StubWorker):
        def generate_text(self, **kwargs):
            if self._calls == 0:
                self._calls += 1
                raise RuntimeError("boom")
            return super().generate_text(**kwargs)

    worker = _FailFirst()
    result = enrich_sync(requests, elements, worker)

    assert result.error_count == 1
    assert result.completed_count == 1
    assert result.errors[0]["error"] == "boom"
    assert "ai_context" not in elements[0].get("metadata", {})
    assert elements[1]["metadata"]["ai_context"].startswith("context-")


def test_enrich_sync_forwards_events_to_caller_listener():
    elements = _sample_elements()
    requests = build_enrichment_requests(elements, "ctx", model="m")
    worker = _StubWorker()
    seen = []
    enrich_sync(requests, elements, worker, event_listener=lambda t, p: seen.append(t))
    assert "prompt_cache_metrics" in seen


def test_enrich_sync_partial_save_hook_fires_at_100():
    # 101 eligible chunks so the loop crosses index=100 exactly once.
    elements = [{"type": "NarrativeText", "text": "x" * 60} for _ in range(101)]
    requests = build_enrichment_requests(elements, "ctx", model="m")
    worker = _StubWorker()
    hits: list[int] = []
    enrich_sync(requests, elements, worker, on_partial_save=lambda i, els: hits.append(i))
    assert hits == [100]


def test_enrich_sync_applies_document_cache_key_suffix():
    elements = _sample_elements()
    requests = build_enrichment_requests(elements, "SAME CONTEXT", model="m")
    seen_configs = []

    class _Capture(_StubWorker):
        def generate_text(self, **kwargs):
            seen_configs.append(kwargs["cache_config"])
            return super().generate_text(**kwargs)

    enrich_sync(requests, elements, _Capture(), full_doc_context="SAME CONTEXT")
    assert len(seen_configs) == 2
    assert seen_configs[0]["key_suffix"] == seen_configs[1]["key_suffix"]
    assert seen_configs[0]["scope"] == "ingest_enrichment"


# ---------------------------------------------------------------------------
# enrich_batch_prepare
# ---------------------------------------------------------------------------

def test_enrich_batch_prepare_writes_valid_jsonl(tmp_path):
    elements = _sample_elements()
    requests = build_enrichment_requests(elements, "ctx", model="gpt-x")
    out = tmp_path / "batch" / "input.jsonl"

    returned = enrich_batch_prepare(requests, out)
    assert returned == out
    assert out.exists()

    lines = [json.loads(line) for line in out.read_text().splitlines() if line.strip()]
    assert len(lines) == 2
    for line, request in zip(lines, requests):
        assert line["custom_id"] == request.custom_id
        assert line["method"] == "POST"
        assert line["url"] == "/v1/responses"
        body = line["body"]
        assert body["model"] == "gpt-x"
        assert body["instructions"] == request.system_prompt
        assert body["input"] == [{"role": "user", "content": request.user_message}]
        assert body["temperature"] == request.temperature
        assert body["max_output_tokens"] == request.max_output_tokens


def test_enrich_batch_prepare_empty_requests_writes_empty_file(tmp_path):
    out = tmp_path / "empty.jsonl"
    enrich_batch_prepare([], out)
    assert out.exists()
    assert out.read_text() == ""


# ---------------------------------------------------------------------------
# enrich_batch_submit / enrich_batch_poll
# ---------------------------------------------------------------------------

def test_enrich_batch_submit_delegates_to_client(tmp_path):
    jsonl = tmp_path / "in.jsonl"
    jsonl.write_text("")
    client = MagicMock()
    client.upload_jsonl.return_value = "file_abc"
    client.submit_batch.return_value = "submission-obj"

    result = enrich_batch_submit(jsonl, client=client, metadata={"doc": "X"})

    client.upload_jsonl.assert_called_once_with(Path(jsonl))
    client.submit_batch.assert_called_once_with("file_abc", metadata={"doc": "X"})
    assert result == "submission-obj"


def test_enrich_batch_poll_delegates_to_client():
    client = MagicMock()
    client.get_status.return_value = "status-obj"
    assert enrich_batch_poll("batch_xyz", client=client) == "status-obj"
    client.get_status.assert_called_once_with("batch_xyz")


# ---------------------------------------------------------------------------
# enrich_batch_merge
# ---------------------------------------------------------------------------

def _result_line(custom_id, text, *, cached=2, input_tokens=50, output_tokens=10):
    return {
        "custom_id": custom_id,
        "response": {
            "status_code": 200,
            "body": {
                "output_text": text,
                "usage": {
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "input_tokens_details": {"cached_tokens": cached},
                },
            },
        },
    }


def test_enrich_batch_merge_applies_ai_context_via_custom_id(tmp_path):
    elements = _sample_elements()
    requests = build_enrichment_requests(elements, "ctx", model="m")
    results = tmp_path / "out.jsonl"
    results.write_text(
        "\n".join(
            [
                json.dumps(_result_line(requests[0].custom_id, "first summary")),
                json.dumps(_result_line(requests[1].custom_id, "second summary")),
            ]
        )
    )

    result = enrich_batch_merge(requests, results, elements)

    assert result.completed_count == 2
    assert result.error_count == 0
    assert elements[0]["metadata"]["ai_context"] == "first summary"
    assert elements[1]["metadata"]["ai_context"] == "second summary"
    assert result.cache_metrics == {"cached_tokens": 4, "input_tokens": 100, "output_tokens": 20}


def test_enrich_batch_merge_skips_unknown_custom_ids(tmp_path):
    elements = _sample_elements()
    requests = build_enrichment_requests(elements, "ctx", model="m")
    results = tmp_path / "out.jsonl"
    results.write_text(json.dumps(_result_line("chunk-unknown", "ignored")))

    result = enrich_batch_merge(requests, results, elements)
    assert result.completed_count == 0
    assert all("ai_context" not in e.get("metadata", {}) for e in elements)


def test_enrich_batch_merge_counts_errors_and_empty_output(tmp_path):
    elements = _sample_elements()
    requests = build_enrichment_requests(elements, "ctx", model="m")
    results = tmp_path / "out.jsonl"
    err_line = {
        "custom_id": requests[0].custom_id,
        "error": {"code": "rate_limited", "message": "retry later"},
    }
    empty_line = _result_line(requests[1].custom_id, "")
    results.write_text(json.dumps(err_line) + "\n" + json.dumps(empty_line))

    result = enrich_batch_merge(requests, results, elements)
    assert result.completed_count == 0
    assert result.error_count == 2
    assert result.errors[0]["error"]["code"] == "rate_limited"
    assert result.errors[1]["error"] == "empty output"


def test_enrich_batch_merge_reads_output_list_when_output_text_absent(tmp_path):
    elements = _sample_elements()
    requests = build_enrichment_requests(elements, "ctx", model="m")
    results = tmp_path / "out.jsonl"
    record = {
        "custom_id": requests[0].custom_id,
        "response": {
            "status_code": 200,
            "body": {
                "output": [
                    {"content": [{"text": "first "}, {"text": "summary"}]}
                ],
                "usage": {"input_tokens": 1, "output_tokens": 1,
                          "input_tokens_details": {"cached_tokens": 0}},
            },
        },
    }
    results.write_text(json.dumps(record))
    enrich_batch_merge(requests, results, elements)
    assert elements[0]["metadata"]["ai_context"] == "first summary"


def test_enrich_batch_merge_missing_file_returns_empty_result(tmp_path):
    requests = []
    result = enrich_batch_merge(requests, tmp_path / "missing.jsonl", [])
    assert result.completed_count == 0
    assert result.error_count == 0


def test_enrich_batch_merge_tolerates_malformed_jsonl(tmp_path):
    elements = _sample_elements()
    requests = build_enrichment_requests(elements, "ctx", model="m")
    results = tmp_path / "out.jsonl"
    good = _result_line(requests[0].custom_id, "ok summary")
    results.write_text("not-json\n\n" + json.dumps(good) + "\n")

    result = enrich_batch_merge(requests, results, elements)
    assert result.completed_count == 1
    assert result.error_count == 1  # the malformed line


# ---------------------------------------------------------------------------
# enrich_elements (legacy wrapper)
# ---------------------------------------------------------------------------

def test_enrich_elements_wrapper_writes_final_json(tmp_path):
    elements_path = tmp_path / "extracted_clean.json"
    context_path = tmp_path / "doc_context.txt"

    elements = [
        {"type": "NarrativeText", "text": "x" * 80},
        {"type": "Title", "text": "ignored header"},
    ]
    elements_path.write_text(json.dumps(elements))
    context_path.write_text("some doc context")

    worker = _StubWorker(responder=lambda i: "the summary")
    result = enrich_elements(str(elements_path), str(context_path), worker, model="m")

    final = tmp_path / "extracted_clean_final.json"
    assert final.exists()
    written = json.loads(final.read_text())
    assert written[0]["metadata"]["ai_context"] == "the summary"
    assert result.completed_count == 1


def test_enrichmentrequest_is_frozen():
    req = EnrichmentRequest(
        custom_id="c", element_index=0, system_prompt="s", user_message="u", model="m"
    )
    with pytest.raises((AttributeError, Exception)):
        req.custom_id = "d"  # type: ignore[misc]


def test_enrichmentresult_default_cache_metrics_present():
    result = EnrichmentResult()
    assert set(result.cache_metrics) == {"cached_tokens", "input_tokens", "output_tokens"}
    assert all(v == 0 for v in result.cache_metrics.values())
