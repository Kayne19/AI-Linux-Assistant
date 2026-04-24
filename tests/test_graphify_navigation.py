from pathlib import Path

from scripts.graphify_navigation import (
    ARCHITECTURE_SCOPE,
    DEEP_SCOPE,
    build_navigation_metrics,
    extract_markdown_graph,
    normalize_extraction_paths,
    prune_architecture_extraction,
    stage_scope,
    validate_scope_sources,
    verify_outputs,
)


def test_architecture_scope_includes_eval_harness_and_docs(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "Back-end" / "app").mkdir(parents=True)
    (repo / "Back-end" / "app" / "api.py").write_text("class API: pass\n")
    (repo / "Back-end" / "infra" / "public-api").mkdir(parents=True)
    (repo / "Back-end" / "infra" / "public-api" / "README.md").write_text("# Public API\n")
    (repo / "Back-end" / "evals").mkdir(parents=True)
    (repo / "Back-end" / "evals" / "README.md").write_text("# Router Evals\n")
    (repo / "Front-end").mkdir()
    (repo / "Front-end" / "FRONTEND.md").write_text("# Frontend\n")
    (repo / "eval-harness" / "src" / "eval_harness").mkdir(parents=True)
    (repo / "eval-harness" / "src" / "eval_harness" / "orchestrator.py").write_text(
        "class Orchestrator: pass\n"
    )
    (repo / "eval-harness" / "README.md").write_text("# Eval Harness\n")
    (repo / "README.md").write_text("# Root\n")
    (repo / "AGENTS.md").write_text("# Agents\n")
    (repo / "CLAUDE.md").write_text("# Claude\n")

    staged = stage_scope(repo, tmp_path / "staged", ARCHITECTURE_SCOPE)

    assert (staged / "eval-harness" / "README.md").exists()
    assert (staged / "eval-harness" / "src" / "eval_harness" / "orchestrator.py").exists()
    assert (staged / "Back-end" / "app" / "api.py").exists()
    assert (staged / "Front-end" / "FRONTEND.md").exists()


def test_staging_excludes_logs_env_caches_and_node_modules(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "Back-end" / "app").mkdir(parents=True)
    (repo / "Back-end" / "app" / "api.py").write_text("class API: pass\n")
    (repo / "Back-end" / "tui_logs").mkdir(parents=True)
    (repo / "Back-end" / "tui_logs" / "run.log").write_text("noise\n")
    (repo / "Front-end" / "node_modules" / "pkg").mkdir(parents=True)
    (repo / "Front-end" / "node_modules" / "pkg" / "index.js").write_text("noise\n")
    (repo / "eval-harness").mkdir()
    (repo / "eval-harness" / ".env").write_text("SECRET=value\n")
    (repo / "README.md").write_text("# Root\n")
    (repo / "AGENTS.md").write_text("# Agents\n")
    (repo / "CLAUDE.md").write_text("# Claude\n")

    staged = stage_scope(repo, tmp_path / "staged", DEEP_SCOPE)

    assert not (staged / "Back-end" / "tui_logs").exists()
    assert not (staged / "Front-end" / "node_modules").exists()
    assert not (staged / "eval-harness" / ".env").exists()


def test_validate_scope_sources_fails_for_real_missing_includes(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    try:
        validate_scope_sources(repo, ARCHITECTURE_SCOPE)
    except FileNotFoundError as exc:
        assert "README.md" in str(exc)
    else:
        raise AssertionError("validate_scope_sources should fail when configured inputs are missing")


def test_extract_markdown_graph_creates_heading_nodes_and_edges(tmp_path: Path) -> None:
    doc = tmp_path / "README.md"
    doc.write_text(
        "# AI Linux Assistant\n\n"
        "## Retrieval\n\n"
        "Uses `Back-end/app/retrieval/RETRIEVAL.md` and `VectorDB`.\n"
    )

    graph = extract_markdown_graph([doc], tmp_path)

    labels = {node["label"] for node in graph["nodes"]}
    assert "AI Linux Assistant" in labels
    assert "Retrieval" in labels
    assert any(edge["relation"] == "references" for edge in graph["edges"])


def test_extract_markdown_graph_marks_eval_harness(tmp_path: Path) -> None:
    doc = tmp_path / "eval-harness" / "README.md"
    doc.parent.mkdir(parents=True)
    doc.write_text("# Eval Harness\n\nRuns benchmark scenarios through orchestrators.\n")

    graph = extract_markdown_graph([doc], tmp_path)

    assert any("Eval Harness" == node["label"] for node in graph["nodes"])
    assert any(node["source_file"] == "eval-harness/README.md" for node in graph["nodes"])


def test_extract_markdown_graph_skips_sensitive_inline_references(tmp_path: Path) -> None:
    doc = tmp_path / "README.md"
    doc.write_text("# Config\n\nDo not index `Back-end/.env` as a graph node.\n")

    graph = extract_markdown_graph([doc], tmp_path)

    assert all(node["label"] != "Back-end/.env" for node in graph["nodes"])


def test_architecture_prune_keeps_major_classes_and_drops_private_helpers() -> None:
    extraction = {
        "nodes": [
            {
                "id": "router",
                "label": "ModelRouter",
                "source_file": "Back-end/app/model_router.py",
                "file_type": "code",
            },
            {
                "id": "helper",
                "label": "_clean_text()",
                "source_file": "Back-end/app/agents/magi/system.py",
                "file_type": "code",
            },
            {
                "id": "store",
                "label": "PostgresRunStore",
                "source_file": "Back-end/app/orchestration/run_store.py",
                "file_type": "code",
            },
        ],
        "edges": [
            {
                "source": "router",
                "target": "store",
                "relation": "references",
                "confidence": "EXTRACTED",
                "confidence_score": 1.0,
                "source_file": "Back-end/app/model_router.py",
                "weight": 1.0,
            },
            {
                "source": "helper",
                "target": "router",
                "relation": "calls",
                "confidence": "INFERRED",
                "confidence_score": 0.7,
                "source_file": "Back-end/app/agents/magi/system.py",
                "weight": 1.0,
            },
        ],
        "hyperedges": [],
    }

    pruned = prune_architecture_extraction(extraction)

    ids = {node["id"] for node in pruned["nodes"]}
    assert "router" in ids
    assert "store" in ids
    assert "helper" not in ids
    assert all(edge["source"] in ids and edge["target"] in ids for edge in pruned["edges"])


def test_normalize_extraction_paths_rewrites_staging_source_files(tmp_path: Path) -> None:
    staged = tmp_path / ".graphify-staging" / "architecture"
    staged.mkdir(parents=True)
    source = staged / "Back-end" / "app" / "api.py"
    source.parent.mkdir(parents=True)
    source.write_text("class API: pass\n")
    extraction = {
        "nodes": [{"id": "api", "source_file": str(source), "label": "api.py"}],
        "edges": [{"source": "api", "target": "router", "source_file": str(source)}],
        "hyperedges": [],
    }

    normalized = normalize_extraction_paths(extraction, staged)

    assert normalized["nodes"][0]["source_file"] == "Back-end/app/api.py"
    assert normalized["edges"][0]["source_file"] == "Back-end/app/api.py"


def test_build_navigation_metrics_warns_without_failing_on_token_targets() -> None:
    metrics = build_navigation_metrics(
        architecture={"nodes": 900, "edges": 2500, "average_query_tokens": 36000, "reduction": 2.5},
        deep={"nodes": 1800, "edges": 6000, "average_query_tokens": 80000, "reduction": 2.5},
    )

    assert metrics["default"] == "architecture"
    assert metrics["architecture"]["average_query_tokens"] == 36000
    assert any(
        "Architecture graph fallback is above target" in warning for warning in metrics["warnings"]
    )
    assert any(
        "Architecture graph benchmark is below baseline" in warning for warning in metrics["warnings"]
    )


def test_verify_outputs_requires_architecture_wiki_and_eval_harness(tmp_path: Path) -> None:
    out = tmp_path / "graphify-out"
    (out / "architecture" / "wiki").mkdir(parents=True)
    (out / "architecture" / "wiki" / "index.md").write_text(
        "# Knowledge Graph Index\n- [[Eval Harness]]\n"
    )
    (out / "architecture" / "GRAPH_REPORT.md").write_text("Eval Harness\n")
    (out / "architecture" / "graph.json").write_text('{"nodes": [], "links": []}')
    (out / "deep").mkdir()
    (out / "deep" / "graph.json").write_text('{"nodes": [], "links": []}')

    verify_outputs(out)


def test_verify_outputs_fails_without_eval_harness(tmp_path: Path) -> None:
    out = tmp_path / "graphify-out"
    (out / "architecture" / "wiki").mkdir(parents=True)
    (out / "architecture" / "wiki" / "index.md").write_text("# Knowledge Graph Index\n")
    (out / "architecture" / "GRAPH_REPORT.md").write_text("Backend\n")
    (out / "architecture" / "graph.json").write_text('{"nodes": [], "links": []}')
    (out / "deep").mkdir()
    (out / "deep" / "graph.json").write_text('{"nodes": [], "links": []}')

    try:
        verify_outputs(out)
    except RuntimeError as exc:
        assert "eval-harness" in str(exc)
    else:
        raise AssertionError("verify_outputs should fail when eval-harness is absent")
