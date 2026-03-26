"""Deep router evaluation harness.

Purpose:
- run multi-turn, scenario-style evaluations against the real router
- stress branch switching, restart persistence, memory continuity, and long-session behavior
- save full per-step outputs so humans can review answer quality and evidence paths

This script is intentionally more qualitative than `evaluate_router.py`.
It still performs a few shallow checks, but its main job is to capture
repeatable scenario transcripts, traces, tool use, memory events, and
final memory state for later review.
"""

import argparse
import json
import os
import sys
import tempfile
import time
from dataclasses import replace
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = SCRIPT_DIR.parent
APP_DIR = BACKEND_DIR / "app"

if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from memory_store import MemoryStore
from model_router import ModelRouter
from settings import SETTINGS


def _read_scenarios(path: Path):
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _append_seed_history(router, seed_history):
    for item in seed_history or []:
        router.update_history(item.get("role", "user"), item.get("content", ""))


def _collect_tool_names(turn):
    names = []
    for event in turn.tool_events or []:
        if event.get("type") == "tool_start":
            payload = event.get("payload", {})
            name = payload.get("name")
            if name:
                names.append(name)
    return names


def _collect_memory_events(turn):
    return [event for event in (turn.tool_events or []) if event.get("type", "").startswith("memory_")]


def _trace_has(turn, marker):
    return marker in (turn.state_trace or [])


def _score_step(step, response, turn):
    checks = {
        "no_router_error": not response.startswith("Router error:"),
        "nonempty_response": bool(response.strip()),
    }
    expected_labels = step.get("expected_labels")
    if expected_labels is not None:
        checks["routing_match"] = (turn.routing_labels or []) == expected_labels
    expected_rag = step.get("expect_rag")
    if expected_rag is not None:
        checks["rag_match"] = _trace_has(turn, "RETRIEVE_CONTEXT") == expected_rag
    return all(checks.values()), checks


def _build_overridden_settings(args):
    settings = SETTINGS
    provider_defaults = dict(settings.provider_defaults)
    if args.openai_default_model:
        provider_defaults["openai"] = args.openai_default_model
    if args.local_default_model:
        provider_defaults["local"] = args.local_default_model
    if args.gemini_default_model:
        provider_defaults["gemini"] = args.gemini_default_model

    settings = replace(settings, provider_defaults=provider_defaults)

    role_fields = [
        "classifier",
        "contextualizer",
        "responder",
        "history_summarizer",
        "context_summarizer",
        "memory_extractor",
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
    return settings


def _apply_vectordb_device_overrides(args):
    if args.vectordb_embed_device is not None:
        os.environ["VECTORDB_EMBED_DEVICE"] = args.vectordb_embed_device
    if args.vectordb_rerank_device is not None:
        os.environ["VECTORDB_RERANK_DEVICE"] = args.vectordb_rerank_device


def _make_router(settings, db_path):
    store = MemoryStore(db_path=db_path)
    return ModelRouter(settings=settings, memory_store=store), store


def run_scenario(settings, scenario):
    with tempfile.TemporaryDirectory(prefix="router-deep-eval-") as tmp_dir:
        db_path = Path(tmp_dir) / "assistant_memory.db"
        router, store = _make_router(settings, db_path)
        _append_seed_history(router, scenario.get("seed_history"))

        steps_out = []
        for index, step in enumerate(scenario.get("steps", []), start=1):
            if step.get("restart_before"):
                router, store = _make_router(settings, db_path)

            started = time.time()
            response = router.ask_question(step["user"])
            duration_ms = round((time.time() - started) * 1000.0, 1)
            turn = router.last_turn
            passed, checks = _score_step(step, response, turn)

            steps_out.append(
                {
                    "index": index,
                    "user": step["user"],
                    "notes": step.get("notes", ""),
                    "response": response,
                    "duration_ms": duration_ms,
                    "passed": passed,
                    "checks": checks,
                    "routing_labels": turn.routing_labels,
                    "retrieval_query": turn.retrieval_query,
                    "tool_names": _collect_tool_names(turn),
                    "state_trace": turn.state_trace,
                    "memory_snapshot_chars": len(turn.memory_snapshot_text or ""),
                    "retrieved_docs_chars": len(turn.retrieved_docs or ""),
                    "memory_events": _collect_memory_events(turn),
                }
            )

        snapshot = store.load_snapshot()
        candidates = store.list_candidates()
        debug_dump = store.format_debug_dump(
            query=scenario.get("memory_debug_query", "system profile issues attempts preferences")
        )

        return {
            "id": scenario["id"],
            "category": scenario.get("category", ""),
            "notes": scenario.get("notes", ""),
            "review_focus": scenario.get("review_focus", ""),
            "steps": steps_out,
            "final_snapshot": snapshot,
            "final_candidates": candidates,
            "final_debug_dump": debug_dump,
        }


def print_summary(results):
    print(f"Ran {len(results)} deep scenarios")
    for scenario in results:
        step_passed = sum(1 for step in scenario["steps"] if step["passed"])
        total_steps = len(scenario["steps"])
        print(f"[{step_passed}/{total_steps}] {scenario['id']} ({scenario['category']})")
        if scenario.get("review_focus"):
            print(f"  Review: {scenario['review_focus']}")
        for step in scenario["steps"]:
            failed_checks = [name for name, ok in step["checks"].items() if not ok]
            marker = "PASS" if step["passed"] else "FAIL"
            print(
                f"  [{marker}] Step {step['index']} labels={step['routing_labels']} "
                f"tools={step['tool_names'] or ['-']} time={step['duration_ms']}ms"
            )
            if failed_checks:
                print(f"    Failed checks: {', '.join(failed_checks)}")


def build_parser():
    parser = argparse.ArgumentParser(description="Run deeper multi-turn router evaluations.")
    parser.add_argument(
        "--scenarios",
        default=str(BACKEND_DIR / "evals" / "router_deep_eval_scenarios.json"),
        help="Path to the deep-eval scenario JSON file.",
    )
    parser.add_argument(
        "--output",
        default=str(BACKEND_DIR / "evals" / "last_router_deep_eval_results.json"),
        help="Where to write the JSON results.",
    )
    parser.add_argument("--openai-default-model")
    parser.add_argument("--local-default-model")
    parser.add_argument("--gemini-default-model")
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
    parser.add_argument("--memory-extractor-provider")
    parser.add_argument("--memory-extractor-model")
    parser.add_argument("--vectordb-embed-device")
    parser.add_argument("--vectordb-rerank-device")
    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    scenarios_path = Path(args.scenarios)
    output_path = Path(args.output)

    os.chdir(BACKEND_DIR)
    _apply_vectordb_device_overrides(args)
    settings = _build_overridden_settings(args)
    scenarios = _read_scenarios(scenarios_path)
    results = [run_scenario(settings, scenario) for scenario in scenarios]

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print_summary(results)
    print(f"Saved detailed results to {output_path}")


if __name__ == "__main__":
    main()
