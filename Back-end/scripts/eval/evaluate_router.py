"""Router evaluation harness.

Purpose:
- run a repeatable set of real router prompts against the active backend
- compare provider/model mixes without editing application code
- capture outputs, traces, tool usage, and simple pass/fail checks

Important limitation:
- this is a regression/evaluation harness, not a semantic grader
- the checks are intentionally shallow (routing, RAG use, citations, errors)
- humans still need to review answer quality for final prompt/model decisions
"""

import argparse
import json
import sys
import time
from dataclasses import replace
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = SCRIPT_DIR.parent.parent
APP_DIR = BACKEND_DIR / "app"

if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import os

from orchestration.model_router import ModelRouter
from config.settings import SETTINGS
from persistence.in_memory_memory_store import InMemoryMemoryStore


def _read_cases(path: Path):
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _append_seed_history(router, seed_history):
    for item in seed_history or []:
        role = item.get("role", "user")
        content = item.get("content", "")
        router.update_history(role, content)


def _reset_router_history(router):
    router.conversation_history = []


def _trace_has(turn, marker):
    return marker in (turn.state_trace or [])


def _collect_tool_names(turn):
    names = []
    for event in turn.tool_events or []:
        if event.get("type") == "tool_start":
            payload = event.get("payload", {})
            name = payload.get("name")
            if name:
                names.append(name)
    return names


def _has_citations(text):
    return "Source:" in text or "(Source:" in text


def _score_case(case, response, turn):
    actual_labels = turn.routing_labels or []
    expected_labels = case.get("expected_labels")
    expected_rag = case.get("expect_rag")
    expect_citations = case.get("expect_citations")

    checks = {
        "no_router_error": not response.startswith("Router error:"),
        "nonempty_response": bool(response.strip()),
    }
    if expected_labels is not None:
        checks["routing_match"] = actual_labels == expected_labels
    if expected_rag is not None:
        checks["rag_match"] = _trace_has(turn, "RETRIEVE_CONTEXT") == expected_rag
    if expect_citations is not None:
        checks["citation_match"] = _has_citations(response) == expect_citations

    overall = all(checks.values())
    return overall, checks


def _build_overridden_settings(args):
    settings = SETTINGS
    provider_defaults = dict(settings.provider_defaults)
    if args.openai_default_model:
        provider_defaults["openai"] = args.openai_default_model
    if args.local_default_model:
        provider_defaults["local"] = args.local_default_model
    settings = replace(settings, provider_defaults=provider_defaults)

    role_fields = [
        "classifier",
        "contextualizer",
        "responder",
        "history_summarizer",
        "context_summarizer",
    ]
    for role_name in role_fields:
        provider = getattr(args, f"{role_name}_provider")
        model = getattr(args, f"{role_name}_model")
        current_role = getattr(settings, role_name)
        if provider is not None or model is not None:
            settings = replace(
                settings,
                **{
                    role_name: replace(
                        current_role,
                        provider=provider or current_role.provider,
                        model=model or current_role.model,
                    )
                },
            )

    if args.response_tool_rounds is not None:
        settings = replace(settings, response_tool_rounds=args.response_tool_rounds)
    return settings


def _apply_vectordb_device_overrides(args):
    if args.vectordb_embed_device is not None:
        os.environ["VECTORDB_EMBED_DEVICE"] = args.vectordb_embed_device
    if args.vectordb_rerank_device is not None:
        os.environ["VECTORDB_RERANK_DEVICE"] = args.vectordb_rerank_device


def run_case(router, case):
    # Reuse one router instance for the full eval run so retrieval/model
    # initialization matches real runtime behavior. We still reset conversation
    # history between cases unless a case intentionally seeds follow-up turns.
    _reset_router_history(router)
    _append_seed_history(router, case.get("seed_history"))
    started = time.time()
    response = router.ask_question(case["query"])
    duration_ms = (time.time() - started) * 1000.0
    turn = router.last_turn
    overall, checks = _score_case(case, response, turn)

    return {
        "id": case["id"],
        "category": case.get("category", ""),
        "query": case["query"],
        "notes": case.get("notes", ""),
        "response": response,
        "duration_ms": round(duration_ms, 1),
        "passed": overall,
        "checks": checks,
        "routing_labels": turn.routing_labels,
        "retrieval_query": turn.retrieval_query,
        "tool_names": _collect_tool_names(turn),
        "state_trace": turn.state_trace,
        "retrieved_docs_chars": len(turn.retrieved_docs or ""),
        "summarized_retrieved_docs_chars": len(turn.summarized_retrieved_docs or ""),
    }


def print_summary(results):
    passed = sum(1 for result in results if result["passed"])
    total = len(results)
    print(f"Passed {passed}/{total} cases")
    for result in results:
        status = "PASS" if result["passed"] else "FAIL"
        print(f"[{status}] {result['id']} ({result['category']})")
        failed_checks = [name for name, ok in result["checks"].items() if not ok]
        if failed_checks:
            print(f"  Failed checks: {', '.join(failed_checks)}")
        print(f"  Labels: {result['routing_labels']}")
        print(f"  Tools: {result['tool_names'] or ['-']}")
        print(f"  Time: {result['duration_ms']}ms")


def build_parser():
    parser = argparse.ArgumentParser(description="Run a repeatable evaluation battery against the router.")
    parser.add_argument(
        "--cases",
        default=str(BACKEND_DIR / "evals" / "router_eval_cases.json"),
        help="Path to the evaluation case JSON file.",
    )
    parser.add_argument(
        "--output",
        default=str(BACKEND_DIR / "evals" / "last_router_eval_results.json"),
        help="Where to write the JSON results.",
    )
    parser.add_argument("--openai-default-model")
    parser.add_argument("--local-default-model")
    parser.add_argument("--classifier-provider")
    parser.add_argument("--classifier-model")
    parser.add_argument("--contextualizer-provider")
    parser.add_argument("--contextualizer-model")
    parser.add_argument("--responder-provider")
    parser.add_argument("--responder-model")
    parser.add_argument("--history-summarizer-provider")
    parser.add_argument("--history-summarizer-model")
    parser.add_argument("--context-summarizer-provider")
    parser.add_argument("--context-summarizer-model")
    parser.add_argument("--response-tool-rounds", type=int)
    parser.add_argument(
        "--vectordb-embed-device",
        help="Override the embedder device for eval runs. By default this follows the normal app runtime.",
    )
    parser.add_argument(
        "--vectordb-rerank-device",
        help="Override the reranker device for eval runs. By default this follows the normal app runtime.",
    )
    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    cases_path = Path(args.cases)
    output_path = Path(args.output)

    _apply_vectordb_device_overrides(args)
    cases = _read_cases(cases_path)
    settings = _build_overridden_settings(args)
    router = ModelRouter(settings=settings, memory_store=InMemoryMemoryStore(project_id="router-eval"))
    results = [run_case(router, case) for case in cases]

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print_summary(results)
    print(f"Saved detailed results to {output_path}")


if __name__ == "__main__":
    main()
