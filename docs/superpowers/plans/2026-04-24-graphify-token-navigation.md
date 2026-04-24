# Graphify Token Navigation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a two-layer graphify navigation system that strongly reduces default repo-understanding token usage while preserving a dense graph for exact code tracing.

**Architecture:** Add a repo-local graphify navigation builder that stages curated corpora, emits a lean `architecture` graph plus wiki as the default navigation surface, and emits a separate `deep` graph for tracing. Architecture navigation is wiki-first, then graph-based, then source-file-based only when needed. Token targets are advisory guardrails that produce warnings and metrics, not hard build failures.

**Tech Stack:** Python, graphify Python package, NetworkX graphify exports, pytest, repo markdown docs, generated `graphify-out/architecture/` and `graphify-out/deep/` artifacts

---

## Operating Principles

- Default repo questions should start from `graphify-out/architecture/wiki/index.md`.
- The architecture graph should prioritize subsystem understanding over exact function tracing.
- The deep graph should preserve code-level density for exact path and call tracing.
- `eval-harness` must be represented in both layers.
- Token targets are measurement targets and warning thresholds. They should not block a build unless the artifact is missing, empty, or fails a required coverage check.
- Do not send private repo contents to external models during implementation. Use deterministic extraction and local graphify APIs unless the user explicitly authorizes a model-backed pass later.

## Token Targets And Guardrails

These are advisory targets:

- Architecture wiki question path: target `8k-18k` context tokens.
- Architecture graph fallback: target `18k-32k` context tokens.
- Deep graph tracing path: no strict target; it is opt-in and may be large.
- Architecture graph should generally be at least `40%` smaller than the deep graph by node count.
- Architecture graph should produce a benchmark better than the current broad graph baseline of about `2.9x` reduction when graphify benchmark is available.

Build behavior:

- If a target is missed, print a warning and include the metric in `graphify-out/navigation.json`.
- If `eval-harness` is absent from the architecture graph/wiki, fail the architecture build.
- If logs, `.env`, `node_modules`, cache directories, or existing graph outputs appear in staged corpora, fail staging.

## File Map

### New Builder And Tests

- Create: `scripts/graphify_navigation.py`
  Command-line builder for architecture and deep graph outputs. Owns corpus staging, deterministic markdown extraction, AST pruning, graphify export, wiki export, metrics, and verification.
- Create: `tests/test_graphify_navigation.py`
  Unit tests for scope selection, staging exclusions, token guardrail reporting, markdown extraction, and artifact verification.

### Documentation

- Modify: `AGENTS.md`
  Update graphify workflow to read `graphify-out/architecture/wiki/index.md` first, then `architecture/graph.json`, then `deep/graph.json` only for exact tracing.
- Modify: `CLAUDE.md`
  Mirror the `AGENTS.md` graphify workflow update.
- Modify: `docs/superpowers/specs/2026-04-18-graphify-navigation-design.md`
  Add the token guardrail clarification and wiki-first retrieval order, without making the advisory token targets hard requirements.

### Generated Outputs

- Generate: `graphify-out/architecture/graph.json`
- Generate: `graphify-out/architecture/graph.html`
- Generate: `graphify-out/architecture/GRAPH_REPORT.md`
- Generate: `graphify-out/architecture/wiki/index.md`
- Generate: `graphify-out/deep/graph.json`
- Generate: `graphify-out/deep/graph.html`
- Generate: `graphify-out/deep/GRAPH_REPORT.md`
- Generate: `graphify-out/navigation.json`

## Command Contract

The builder should expose these commands:

```bash
python scripts/graphify_navigation.py architecture
python scripts/graphify_navigation.py deep
python scripts/graphify_navigation.py all
python scripts/graphify_navigation.py verify
```

Expected behavior:

- `architecture`: rebuild only `graphify-out/architecture/`
- `deep`: rebuild only `graphify-out/deep/`
- `all`: rebuild both layers and `graphify-out/navigation.json`
- `verify`: inspect existing outputs and fail if required artifacts or coverage checks are missing

All commands should run from the repository root.

## Task 1: Add Scope Configuration And Staging

**Files:**
- Create: `scripts/graphify_navigation.py`
- Create: `tests/test_graphify_navigation.py`

- [ ] **Step 1: Write failing staging tests**

Add tests that prove architecture staging includes `eval-harness` and excludes junk.

```python
from pathlib import Path

from scripts.graphify_navigation import ARCHITECTURE_SCOPE, DEEP_SCOPE, stage_scope


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
    (repo / "eval-harness" / "src" / "eval_harness" / "orchestrator.py").write_text("class Orchestrator: pass\n")
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
```

- [ ] **Step 2: Run staging tests to verify they fail**

Run:

```bash
python -m pytest tests/test_graphify_navigation.py::test_architecture_scope_includes_eval_harness_and_docs tests/test_graphify_navigation.py::test_staging_excludes_logs_env_caches_and_node_modules -q
```

Expected: FAIL with `ModuleNotFoundError` for `scripts.graphify_navigation`.

- [ ] **Step 3: Implement scope configuration**

Create `scripts/graphify_navigation.py` with these top-level types and scope constants:

```python
from __future__ import annotations

import argparse
import json
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


EXCLUDED_NAMES = {
    ".git",
    ".pytest_cache",
    "__pycache__",
    ".mypy_cache",
    ".ruff_cache",
    ".venv",
    "node_modules",
    "dist",
    ".test-dist",
    "graphify-out",
    "tui_logs",
    "lancedb_data",
    "lancedb_backups",
    "ingest_traces",
}

EXCLUDED_SUFFIXES = {
    ".pyc",
    ".pyo",
    ".log",
    ".sqlite",
    ".db",
}

SENSITIVE_NAMES = {".env", ".env.local", ".env.production"}


@dataclass(frozen=True)
class ScopeConfig:
    name: str
    includes: tuple[str, ...]
    output_dir: str
    keep_tests: bool
    architecture_prune: bool


ARCHITECTURE_SCOPE = ScopeConfig(
    name="architecture",
    includes=(
        "README.md",
        "AGENTS.md",
        "CLAUDE.md",
        "Back-end/app",
        "Back-end/infra/public-api",
        "Back-end/evals/README.md",
        "Front-end/FRONTEND.md",
        "eval-harness/README.md",
        "eval-harness/infra/aws/README.md",
        "eval-harness/src/eval_harness",
        "run_eval_harness.py",
    ),
    output_dir="graphify-out/architecture",
    keep_tests=False,
    architecture_prune=True,
)


DEEP_SCOPE = ScopeConfig(
    name="deep",
    includes=(
        "README.md",
        "AGENTS.md",
        "CLAUDE.md",
        "Back-end/app",
        "Back-end/tests",
        "Back-end/scripts",
        "Back-end/evals",
        "Back-end/infra/public-api",
        "Front-end/src",
        "Front-end/FRONTEND.md",
        "eval-harness/README.md",
        "eval-harness/infra/aws/README.md",
        "eval-harness/src",
        "eval-harness/tests",
        "run_eval_harness.py",
    ),
    output_dir="graphify-out/deep",
    keep_tests=True,
    architecture_prune=False,
)
```

- [ ] **Step 4: Implement safe staging**

Add these functions:

```python
def should_exclude(path: Path) -> bool:
    parts = set(path.parts)
    if parts & EXCLUDED_NAMES:
        return True
    if path.name in SENSITIVE_NAMES:
        return True
    return path.suffix in EXCLUDED_SUFFIXES


def copy_path(src: Path, dst: Path) -> None:
    if should_exclude(src):
        return
    if src.is_file():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        return
    if src.is_dir():
        for child in src.rglob("*"):
            rel = child.relative_to(src)
            target = dst / rel
            if should_exclude(child) or any(part in EXCLUDED_NAMES for part in rel.parts):
                continue
            if child.is_file():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(child, target)


def stage_scope(repo_root: Path, stage_root: Path, scope: ScopeConfig) -> Path:
    stage_dir = stage_root / scope.name
    if stage_dir.exists():
        shutil.rmtree(stage_dir)
    stage_dir.mkdir(parents=True)

    missing: list[str] = []
    for include in scope.includes:
        src = repo_root / include
        if not src.exists():
            missing.append(include)
            continue
        copy_path(src, stage_dir / include)

    if missing:
        raise FileNotFoundError(f"Missing required graphify scope paths: {', '.join(missing)}")
    return stage_dir
```

- [ ] **Step 5: Run staging tests to verify they pass**

Run:

```bash
python -m pytest tests/test_graphify_navigation.py::test_architecture_scope_includes_eval_harness_and_docs tests/test_graphify_navigation.py::test_staging_excludes_logs_env_caches_and_node_modules -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add scripts/graphify_navigation.py tests/test_graphify_navigation.py
git commit -m "Add graphify scope staging"
```

## Task 2: Add Deterministic Markdown Semantic Extraction

**Files:**
- Modify: `scripts/graphify_navigation.py`
- Modify: `tests/test_graphify_navigation.py`

The architecture layer should avoid model-backed semantic extraction by default. Instead, extract document nodes from markdown headings, key inline code references, and repo-relative file references. This keeps generation cheap and avoids external model risk.

- [ ] **Step 1: Write failing markdown extraction tests**

```python
from scripts.graphify_navigation import extract_markdown_graph


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
```

- [ ] **Step 2: Run markdown extraction tests to verify they fail**

Run:

```bash
python -m pytest tests/test_graphify_navigation.py::test_extract_markdown_graph_creates_heading_nodes_and_edges tests/test_graphify_navigation.py::test_extract_markdown_graph_marks_eval_harness -q
```

Expected: FAIL with missing `extract_markdown_graph`.

- [ ] **Step 3: Implement deterministic markdown extraction**

Add:

```python
import hashlib
import re


HEADING_RE = re.compile(r"^(#{1,4})\s+(.+?)\s*$")
INLINE_CODE_RE = re.compile(r"`([^`]+)`")


def stable_id(*parts: str) -> str:
    raw = "::".join(parts)
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:10]
    stem = re.sub(r"[^a-zA-Z0-9]+", "_", parts[-1].lower()).strip("_")
    return f"{stem}_{digest}" if stem else digest


def node_for(label: str, source_file: str, file_type: str = "document") -> dict:
    return {
        "id": stable_id(source_file, label),
        "label": label.strip(),
        "file_type": file_type,
        "source_file": source_file,
        "source_location": None,
        "source_url": None,
        "captured_at": None,
        "author": None,
        "contributor": None,
    }


def edge_for(source: str, target: str, relation: str, source_file: str) -> dict:
    return {
        "source": source,
        "target": target,
        "relation": relation,
        "confidence": "EXTRACTED",
        "confidence_score": 1.0,
        "source_file": source_file,
        "source_location": None,
        "weight": 1.0,
    }


def extract_markdown_graph(markdown_files: Iterable[Path], corpus_root: Path) -> dict:
    nodes: list[dict] = []
    edges: list[dict] = []
    seen: dict[str, dict] = {}

    for path in markdown_files:
        rel = path.relative_to(corpus_root).as_posix()
        parent_id: str | None = None
        for line in path.read_text(errors="ignore").splitlines():
            heading = HEADING_RE.match(line)
            if heading:
                label = heading.group(2).strip(" #")
                node = node_for(label, rel)
                if node["id"] not in seen:
                    seen[node["id"]] = node
                    nodes.append(node)
                if parent_id:
                    edges.append(edge_for(parent_id, node["id"], "references", rel))
                parent_id = node["id"]
                continue

            if parent_id:
                for code_ref in INLINE_CODE_RE.findall(line):
                    if "/" not in code_ref and "." not in code_ref and len(code_ref) < 3:
                        continue
                    ref_node = node_for(code_ref, rel, "code" if "." in code_ref or "/" in code_ref else "document")
                    if ref_node["id"] not in seen:
                        seen[ref_node["id"]] = ref_node
                        nodes.append(ref_node)
                    edges.append(edge_for(parent_id, ref_node["id"], "references", rel))

    return {"nodes": nodes, "edges": edges, "hyperedges": [], "input_tokens": 0, "output_tokens": 0}
```

- [ ] **Step 4: Run markdown extraction tests to verify they pass**

Run:

```bash
python -m pytest tests/test_graphify_navigation.py::test_extract_markdown_graph_creates_heading_nodes_and_edges tests/test_graphify_navigation.py::test_extract_markdown_graph_marks_eval_harness -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/graphify_navigation.py tests/test_graphify_navigation.py
git commit -m "Extract graphify markdown nodes"
```

## Task 3: Add AST Extraction And Architecture Pruning

**Files:**
- Modify: `scripts/graphify_navigation.py`
- Modify: `tests/test_graphify_navigation.py`

- [ ] **Step 1: Write pruning tests**

```python
from scripts.graphify_navigation import prune_architecture_extraction


def test_architecture_prune_keeps_major_classes_and_drops_private_helpers() -> None:
    extraction = {
        "nodes": [
            {"id": "router", "label": "ModelRouter", "source_file": "Back-end/app/model_router.py", "file_type": "code"},
            {"id": "helper", "label": "_clean_text()", "source_file": "Back-end/app/agents/magi/system.py", "file_type": "code"},
            {"id": "store", "label": "PostgresRunStore", "source_file": "Back-end/app/orchestration/run_store.py", "file_type": "code"},
        ],
        "edges": [
            {"source": "router", "target": "store", "relation": "references", "confidence": "EXTRACTED", "confidence_score": 1.0, "source_file": "Back-end/app/model_router.py", "weight": 1.0},
            {"source": "helper", "target": "router", "relation": "calls", "confidence": "INFERRED", "confidence_score": 0.7, "source_file": "Back-end/app/agents/magi/system.py", "weight": 1.0},
        ],
        "hyperedges": [],
    }

    pruned = prune_architecture_extraction(extraction)

    ids = {node["id"] for node in pruned["nodes"]}
    assert "router" in ids
    assert "store" in ids
    assert "helper" not in ids
    assert all(edge["source"] in ids and edge["target"] in ids for edge in pruned["edges"])
```

- [ ] **Step 2: Run pruning test to verify it fails**

Run:

```bash
python -m pytest tests/test_graphify_navigation.py::test_architecture_prune_keeps_major_classes_and_drops_private_helpers -q
```

Expected: FAIL with missing `prune_architecture_extraction`.

- [ ] **Step 3: Implement AST file collection and pruning**

Add:

```python
CODE_SUFFIXES = {".py", ".js", ".jsx", ".ts", ".tsx"}
ARCHITECTURE_LABEL_ALLOW = (
    "Router",
    "Store",
    "Provider",
    "Worker",
    "Service",
    "System",
    "Config",
    "Settings",
    "Client",
    "Verifier",
    "Orchestrator",
    "Scenario",
    "Harness",
    "Pipeline",
    "Indexer",
    "Controller",
    "Model",
)
ARCHITECTURE_SOURCE_ALLOW = (
    "api.py",
    "main.py",
    "run_eval_harness.py",
    "orchestrator.py",
    "models.py",
    "config.py",
    "settings.py",
    "providers",
    "orchestration",
    "persistence",
    "retrieval",
    "ingestion",
    "auth",
    "streaming",
    "eval_harness",
)


def collect_code_files(corpus_root: Path) -> list[Path]:
    return [
        path
        for path in corpus_root.rglob("*")
        if path.is_file() and path.suffix in CODE_SUFFIXES and not should_exclude(path)
    ]


def is_architecture_node(node: dict) -> bool:
    label = str(node.get("label", ""))
    source = str(node.get("source_file", ""))
    if label.startswith("_") or label.startswith(".") or label in {"__init__.py", "__init__"}:
        return False
    if any(token in label for token in ARCHITECTURE_LABEL_ALLOW):
        return True
    return any(token in source for token in ARCHITECTURE_SOURCE_ALLOW)


def prune_architecture_extraction(extraction: dict) -> dict:
    nodes = [node for node in extraction.get("nodes", []) if is_architecture_node(node)]
    kept_ids = {node["id"] for node in nodes}
    edges = [
        edge
        for edge in extraction.get("edges", [])
        if edge.get("source") in kept_ids and edge.get("target") in kept_ids
    ]
    hyperedges = [
        hyperedge
        for hyperedge in extraction.get("hyperedges", [])
        if all(node_id in kept_ids for node_id in hyperedge.get("nodes", []))
    ]
    return {
        "nodes": nodes,
        "edges": edges,
        "hyperedges": hyperedges,
        "input_tokens": extraction.get("input_tokens", 0),
        "output_tokens": extraction.get("output_tokens", 0),
    }
```

- [ ] **Step 4: Implement graphify AST extraction wrapper**

Add:

```python
def run_ast_extraction(code_files: list[Path]) -> dict:
    if not code_files:
        return {"nodes": [], "edges": [], "hyperedges": [], "input_tokens": 0, "output_tokens": 0}

    from graphify.extract import extract

    result = extract(code_files)
    result.setdefault("hyperedges", [])
    result.setdefault("input_tokens", 0)
    result.setdefault("output_tokens", 0)
    return result
```

- [ ] **Step 5: Run pruning tests**

Run:

```bash
python -m pytest tests/test_graphify_navigation.py::test_architecture_prune_keeps_major_classes_and_drops_private_helpers -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add scripts/graphify_navigation.py tests/test_graphify_navigation.py
git commit -m "Prune architecture graph nodes"
```

## Task 4: Add Graph Build, Wiki Export, HTML Export, And Metrics

**Files:**
- Modify: `scripts/graphify_navigation.py`
- Modify: `tests/test_graphify_navigation.py`

- [ ] **Step 1: Write metric tests**

```python
from scripts.graphify_navigation import build_navigation_metrics


def test_build_navigation_metrics_warns_without_failing_on_token_targets() -> None:
    metrics = build_navigation_metrics(
        architecture={"nodes": 900, "edges": 2500, "average_query_tokens": 36000, "reduction": 4.0},
        deep={"nodes": 1800, "edges": 6000, "average_query_tokens": 80000, "reduction": 2.5},
    )

    assert metrics["default"] == "architecture"
    assert metrics["architecture"]["average_query_tokens"] == 36000
    assert any("Architecture graph fallback is above target" in warning for warning in metrics["warnings"])
```

- [ ] **Step 2: Run metric test to verify it fails**

Run:

```bash
python -m pytest tests/test_graphify_navigation.py::test_build_navigation_metrics_warns_without_failing_on_token_targets -q
```

Expected: FAIL with missing `build_navigation_metrics`.

- [ ] **Step 3: Implement extraction merge and graph output builder**

Add:

```python
def merge_extractions(*extractions: dict) -> dict:
    nodes: list[dict] = []
    edges: list[dict] = []
    hyperedges: list[dict] = []
    seen: set[str] = set()
    input_tokens = 0
    output_tokens = 0

    for extraction in extractions:
        input_tokens += int(extraction.get("input_tokens", 0) or 0)
        output_tokens += int(extraction.get("output_tokens", 0) or 0)
        for node in extraction.get("nodes", []):
            node_id = node.get("id")
            if node_id and node_id not in seen:
                seen.add(node_id)
                nodes.append(node)
        edges.extend(extraction.get("edges", []))
        hyperedges.extend(extraction.get("hyperedges", []))

    return {
        "nodes": nodes,
        "edges": edges,
        "hyperedges": hyperedges,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
    }
```

Add:

```python
def build_graph_outputs(extraction: dict, corpus_root: Path, output_dir: Path, *, wiki: bool) -> dict:
    from graphify.analyze import god_nodes, surprising_connections, suggest_questions
    from graphify.build import build_from_json
    from graphify.cluster import cluster, score_all
    from graphify.export import to_html, to_json
    from graphify.report import generate
    from graphify.wiki import to_wiki

    output_dir.mkdir(parents=True, exist_ok=True)
    graph = build_from_json(extraction)
    communities = cluster(graph)
    cohesion = score_all(graph, communities)
    gods = god_nodes(graph)
    surprises = surprising_connections(graph, communities)
    labels = label_communities(graph, communities)
    questions = suggest_questions(graph, communities, labels)
    detection = detect_corpus_summary(corpus_root)
    tokens = {"input": extraction.get("input_tokens", 0), "output": extraction.get("output_tokens", 0)}

    report = generate(
        graph,
        communities,
        cohesion,
        labels,
        gods,
        surprises,
        detection,
        tokens,
        str(corpus_root),
        suggested_questions=questions,
    )
    (output_dir / "GRAPH_REPORT.md").write_text(report)
    to_json(graph, communities, output_dir / "graph.json")
    to_html(graph, communities, output_dir / "graph.html", community_labels=labels)
    if wiki:
        to_wiki(graph, communities, output_dir / "wiki", community_labels=labels, cohesion=cohesion, god_nodes_data=gods)

    return {
        "nodes": graph.number_of_nodes(),
        "edges": graph.number_of_edges(),
        "communities": len(communities),
        "god_nodes": gods,
        "questions": questions,
    }
```

- [ ] **Step 4: Implement community labels and corpus summary helpers**

Add:

```python
def label_communities(graph, communities: dict[int, list[str]]) -> dict[int, str]:
    labels: dict[int, str] = {}
    for cid, node_ids in communities.items():
        best = f"Community {cid}"
        for node_id in sorted(node_ids, key=lambda nid: graph.degree(nid), reverse=True):
            label = str(graph.nodes[node_id].get("label", node_id)).strip()
            if label and label not in {"__init__.py", "__init__"} and not label.startswith("_"):
                best = label[:48]
                break
        labels[cid] = best
    return labels


def detect_corpus_summary(corpus_root: Path) -> dict:
    files = [path for path in corpus_root.rglob("*") if path.is_file() and not should_exclude(path)]
    total_words = 0
    grouped = {"code": [], "document": [], "paper": [], "image": [], "video": []}
    for path in files:
        suffix = path.suffix.lower()
        rel = path.relative_to(corpus_root).as_posix()
        if suffix in CODE_SUFFIXES:
            grouped["code"].append(rel)
        elif suffix in {".md", ".txt"}:
            grouped["document"].append(rel)
        try:
            total_words += len(path.read_text(errors="ignore").split())
        except UnicodeDecodeError:
            pass
    return {
        "total_files": len(files),
        "total_words": total_words,
        "needs_graph": True,
        "warning": None,
        "files": grouped,
    }
```

- [ ] **Step 5: Implement token metric guardrails**

Add:

```python
ARCHITECTURE_WIKI_TARGET = (8_000, 18_000)
ARCHITECTURE_GRAPH_TARGET = (18_000, 32_000)


def build_navigation_metrics(*, architecture: dict, deep: dict | None) -> dict:
    warnings: list[str] = []
    arch_query_tokens = int(architecture.get("average_query_tokens", 0) or 0)
    if arch_query_tokens and arch_query_tokens > ARCHITECTURE_GRAPH_TARGET[1]:
        warnings.append(
            f"Architecture graph fallback is above target: {arch_query_tokens} tokens > {ARCHITECTURE_GRAPH_TARGET[1]}"
        )
    if deep and deep.get("nodes") and architecture.get("nodes"):
        ratio = architecture["nodes"] / deep["nodes"]
        if ratio > 0.60:
            warnings.append(f"Architecture graph is not at least 40% smaller than deep graph: ratio={ratio:.2f}")

    return {
        "default": "architecture",
        "navigation_order": [
            "graphify-out/architecture/wiki/index.md",
            "graphify-out/architecture/graph.json",
            "graphify-out/deep/graph.json",
        ],
        "targets": {
            "architecture_wiki_context_tokens": list(ARCHITECTURE_WIKI_TARGET),
            "architecture_graph_context_tokens": list(ARCHITECTURE_GRAPH_TARGET),
            "hard_limits": False,
        },
        "architecture": architecture,
        "deep": deep,
        "warnings": warnings,
    }
```

- [ ] **Step 6: Run metric tests**

Run:

```bash
python -m pytest tests/test_graphify_navigation.py::test_build_navigation_metrics_warns_without_failing_on_token_targets -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add scripts/graphify_navigation.py tests/test_graphify_navigation.py
git commit -m "Build graphify navigation outputs"
```

## Task 5: Add CLI Commands And Verification

**Files:**
- Modify: `scripts/graphify_navigation.py`
- Modify: `tests/test_graphify_navigation.py`

- [ ] **Step 1: Write verification tests**

```python
from scripts.graphify_navigation import verify_outputs


def test_verify_outputs_requires_architecture_wiki_and_eval_harness(tmp_path: Path) -> None:
    out = tmp_path / "graphify-out"
    (out / "architecture" / "wiki").mkdir(parents=True)
    (out / "architecture" / "wiki" / "index.md").write_text("# Knowledge Graph Index\n- [[Eval Harness]]\n")
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
```

- [ ] **Step 2: Run verification tests to verify they fail**

Run:

```bash
python -m pytest tests/test_graphify_navigation.py::test_verify_outputs_requires_architecture_wiki_and_eval_harness tests/test_graphify_navigation.py::test_verify_outputs_fails_without_eval_harness -q
```

Expected: FAIL with missing `verify_outputs`.

- [ ] **Step 3: Implement build commands**

Add:

```python
def build_scope(repo_root: Path, stage_root: Path, scope: ScopeConfig) -> dict:
    staged = stage_scope(repo_root, stage_root, scope)
    markdown = [path for path in staged.rglob("*") if path.is_file() and path.suffix.lower() in {".md", ".txt"}]
    docs = extract_markdown_graph(markdown, staged)
    ast = run_ast_extraction(collect_code_files(staged))
    if scope.architecture_prune:
        ast = prune_architecture_extraction(ast)
    extraction = merge_extractions(ast, docs)
    output_dir = repo_root / scope.output_dir
    if output_dir.exists():
        shutil.rmtree(output_dir)
    return build_graph_outputs(extraction, staged, output_dir, wiki=scope.name == "architecture")


def estimate_query_tokens(output_dir: Path) -> dict:
    graph_json = output_dir / "graph.json"
    if not graph_json.exists():
        return {}
    try:
        from graphify.benchmark import run_benchmark
    except Exception:
        return {}
    report = output_dir / "GRAPH_REPORT.md"
    corpus_words = len(report.read_text(errors="ignore").split()) if report.exists() else 0
    if corpus_words <= 0:
        return {}
    result = run_benchmark(str(graph_json), corpus_words=corpus_words)
    return {
        "average_query_tokens": result.get("avg_graph_tokens", 0),
        "reduction": result.get("reduction_factor", 0),
    }
```

Add:

```python
def run_all(repo_root: Path) -> int:
    stage_root = repo_root / ".graphify-staging"
    if stage_root.exists():
        shutil.rmtree(stage_root)
    stage_root.mkdir()
    try:
        architecture = build_scope(repo_root, stage_root, ARCHITECTURE_SCOPE)
        architecture.update(estimate_query_tokens(repo_root / ARCHITECTURE_SCOPE.output_dir))
        deep = build_scope(repo_root, stage_root, DEEP_SCOPE)
        deep.update(estimate_query_tokens(repo_root / DEEP_SCOPE.output_dir))
        metrics = build_navigation_metrics(architecture=architecture, deep=deep)
        (repo_root / "graphify-out").mkdir(exist_ok=True)
        (repo_root / "graphify-out" / "navigation.json").write_text(json.dumps(metrics, indent=2))
        verify_outputs(repo_root / "graphify-out")
        for warning in metrics["warnings"]:
            print(f"WARNING: {warning}")
        return 0
    finally:
        shutil.rmtree(stage_root, ignore_errors=True)
```

- [ ] **Step 4: Implement output verification**

Add:

```python
def verify_outputs(graphify_out: Path) -> None:
    required = [
        graphify_out / "architecture" / "graph.json",
        graphify_out / "architecture" / "GRAPH_REPORT.md",
        graphify_out / "architecture" / "wiki" / "index.md",
        graphify_out / "deep" / "graph.json",
    ]
    missing = [path for path in required if not path.exists()]
    if missing:
        raise RuntimeError("Missing graphify navigation artifacts: " + ", ".join(str(path) for path in missing))

    arch_text = (
        (graphify_out / "architecture" / "GRAPH_REPORT.md").read_text(errors="ignore")
        + "\n"
        + (graphify_out / "architecture" / "wiki" / "index.md").read_text(errors="ignore")
    ).lower()
    if "eval harness" not in arch_text and "eval-harness" not in arch_text:
        raise RuntimeError("Architecture graph/wiki missing eval-harness coverage")

    forbidden = ["tui_logs", "node_modules", ".env"]
    for artifact in (graphify_out / "architecture").rglob("*"):
        if artifact.is_file() and artifact.suffix in {".md", ".json"}:
            text = artifact.read_text(errors="ignore")
            for token in forbidden:
                if token in text:
                    raise RuntimeError(f"Forbidden source appeared in architecture artifact: {token}")
```

- [ ] **Step 5: Implement CLI**

Add:

```python
def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build graphify navigation layers")
    parser.add_argument("command", choices=("architecture", "deep", "all", "verify"))
    parser.add_argument("--repo-root", default=".", help="Repository root; defaults to current directory")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    repo_root = Path(args.repo_root).resolve()
    stage_root = repo_root / ".graphify-staging"
    if args.command == "verify":
        verify_outputs(repo_root / "graphify-out")
        return 0
    if args.command == "all":
        return run_all(repo_root)
    scope = ARCHITECTURE_SCOPE if args.command == "architecture" else DEEP_SCOPE
    if stage_root.exists():
        shutil.rmtree(stage_root)
    stage_root.mkdir()
    try:
        build_scope(repo_root, stage_root, scope)
        return 0
    finally:
        shutil.rmtree(stage_root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 6: Run verification tests**

Run:

```bash
python -m pytest tests/test_graphify_navigation.py::test_verify_outputs_requires_architecture_wiki_and_eval_harness tests/test_graphify_navigation.py::test_verify_outputs_fails_without_eval_harness -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add scripts/graphify_navigation.py tests/test_graphify_navigation.py
git commit -m "Verify graphify navigation outputs"
```

## Task 6: Update Repo Workflow Documentation

**Files:**
- Modify: `AGENTS.md`
- Modify: `CLAUDE.md`
- Modify: `docs/superpowers/specs/2026-04-18-graphify-navigation-design.md`

- [ ] **Step 1: Update the graphify section in `AGENTS.md`**

Replace the existing graphify section with this wording:

```markdown
## graphify

This project uses a two-layer graphify navigation model:

- `graphify-out/architecture/` is the default navigation surface.
- `graphify-out/architecture/wiki/index.md` is the first file to read for repo orientation.
- `graphify-out/deep/` is reserved for exact code tracing and implementation-level path questions.

Rules:
- Before answering architecture or codebase questions, read `graphify-out/architecture/wiki/index.md` if it exists.
- Use `graphify-out/architecture/graph.json` for subsystem path tracing.
- Use `graphify-out/deep/graph.json` only when the question needs exact code-level tracing.
- If architecture outputs are missing or stale, run `python scripts/graphify_navigation.py architecture`.
- If code tracing outputs are needed or stale, run `python scripts/graphify_navigation.py deep`.
- After modifying code files in this session, run `python scripts/graphify_navigation.py architecture` to refresh default navigation. Run the deep build only when the change affects tracing needs.
- Token targets in `graphify-out/navigation.json` are advisory guardrails. Treat warnings as tuning signals, not automatic failures.
```

- [ ] **Step 2: Mirror the same section in `CLAUDE.md`**

Use the exact same markdown block as `AGENTS.md` so agent instructions stay synchronized.

- [ ] **Step 3: Update the design spec token section**

Add this paragraph under `Default Navigation Behavior` in `docs/superpowers/specs/2026-04-18-graphify-navigation-design.md`:

```markdown
Token targets are advisory. The architecture wiki should usually answer navigation questions with substantially less context than the architecture graph, and the architecture graph should usually be smaller than the deep graph. Missed targets should produce warnings and measurements, not hard failures, unless required artifacts are missing or required subsystem coverage is absent.
```

- [ ] **Step 4: Verify docs mention architecture and deep outputs**

Run:

```bash
rg -n "graphify-out/(architecture|deep)|graphify_navigation.py|Token targets" AGENTS.md CLAUDE.md docs/superpowers/specs/2026-04-18-graphify-navigation-design.md
```

Expected: output includes all three files.

- [ ] **Step 5: Commit**

```bash
git add AGENTS.md CLAUDE.md docs/superpowers/specs/2026-04-18-graphify-navigation-design.md
git commit -m "Document graphify navigation workflow"
```

## Task 7: Generate Both Graph Layers

**Files:**
- Generate: `graphify-out/architecture/graph.json`
- Generate: `graphify-out/architecture/graph.html`
- Generate: `graphify-out/architecture/GRAPH_REPORT.md`
- Generate: `graphify-out/architecture/wiki/index.md`
- Generate: `graphify-out/deep/graph.json`
- Generate: `graphify-out/deep/graph.html`
- Generate: `graphify-out/deep/GRAPH_REPORT.md`
- Generate: `graphify-out/navigation.json`

- [ ] **Step 1: Run the full builder**

Run:

```bash
python scripts/graphify_navigation.py all
```

Expected:

- `graphify-out/architecture/wiki/index.md` exists
- `graphify-out/architecture/graph.json` exists
- `graphify-out/deep/graph.json` exists
- terminal output may include advisory warnings

- [ ] **Step 2: Verify artifacts**

Run:

```bash
python scripts/graphify_navigation.py verify
```

Expected: exit code `0`.

- [ ] **Step 3: Inspect metrics**

Run:

```bash
python -m json.tool graphify-out/navigation.json
```

Expected:

- `default` is `architecture`
- `navigation_order[0]` is `graphify-out/architecture/wiki/index.md`
- `targets.hard_limits` is `false`
- warnings are present only if token targets need tuning

- [ ] **Step 4: Compare graph sizes**

Run:

```bash
python - <<'PY'
import json
from pathlib import Path

for name in ("architecture", "deep"):
    data = json.loads(Path(f"graphify-out/{name}/graph.json").read_text())
    print(name, len(data.get("nodes", [])), len(data.get("links", [])))
PY
```

Expected:

- `architecture` has fewer nodes than `deep`
- `architecture` includes eval-harness coverage in the report/wiki

- [ ] **Step 5: Commit generated navigation artifacts if the repo tracks graphify outputs**

Check:

```bash
git status --short graphify-out
```

If graphify outputs are tracked or intentionally committed in this repo, commit them:

```bash
git add graphify-out/architecture graphify-out/deep graphify-out/navigation.json
git commit -m "Generate graphify navigation layers"
```

If generated outputs are intentionally untracked, do not commit them. Report the paths instead.

## Task 8: Run Final Verification

**Files:**
- Read: all files touched above

- [ ] **Step 1: Run unit tests for the builder**

Run:

```bash
python -m pytest tests/test_graphify_navigation.py -q
```

Expected: PASS.

- [ ] **Step 2: Run graphify verification**

Run:

```bash
python scripts/graphify_navigation.py verify
```

Expected: PASS.

- [ ] **Step 3: Run syntax check**

Run:

```bash
python -m py_compile scripts/graphify_navigation.py tests/test_graphify_navigation.py
```

Expected: no output and exit code `0`.

- [ ] **Step 4: Check repo docs stay synchronized**

Run:

```bash
python - <<'PY'
from pathlib import Path

agents = Path("AGENTS.md").read_text()
claude = Path("CLAUDE.md").read_text()
required = [
    "graphify-out/architecture/wiki/index.md",
    "graphify-out/deep/graph.json",
    "scripts/graphify_navigation.py",
]
for item in required:
    assert item in agents, f"AGENTS.md missing {item}"
    assert item in claude, f"CLAUDE.md missing {item}"
print("graphify docs synchronized")
PY
```

Expected: `graphify docs synchronized`.

- [ ] **Step 5: Inspect final git status**

Run:

```bash
git status --short
```

Expected:

- Only intended plan, script, test, docs, and optional generated graph outputs are changed.
- Pre-existing unrelated changes remain untouched.

- [ ] **Step 6: Final commit**

If there are uncommitted intended implementation changes:

```bash
git add scripts/graphify_navigation.py tests/test_graphify_navigation.py AGENTS.md CLAUDE.md docs/superpowers/specs/2026-04-18-graphify-navigation-design.md
git commit -m "Implement graphify navigation"
```

Use a separate generated-artifacts commit only if graphify outputs are intentionally tracked.

## Execution Notes

- Use `conda run -n AI-Linux-Assistant python ...` if the local shell cannot import the same Python packages available in the project environment.
- If `graphify` is missing, install or activate the environment that already produced `graphify-out/.graphify_python`; do not switch to an external semantic model just to rebuild the architecture layer.
- The first implementation pass should avoid model-backed semantic extraction. Deterministic markdown extraction plus AST extraction is enough to create the token-saving navigation layer.
- A future optional improvement can add a user-approved model-backed semantic refresh for the architecture wiki, but that should be a separate decision because it spends tokens and may expose private repo content to model providers.

## Self-Review

Spec coverage:

- Two graph layers: Tasks 4, 5, and 7.
- Architecture wiki: Tasks 4, 5, and 7.
- Deep tracing graph: Tasks 4, 5, and 7.
- `eval-harness` coverage: Tasks 1, 2, 5, and 7.
- Wiki-first default navigation: Task 6 and `navigation.json` in Task 4.
- Advisory token guardrails: Task 4 and Task 6.
- Junk exclusion: Task 1 and Task 5.

No placeholders are intentionally left in this plan. Token targets are intentionally advisory because the user was unsure about hard limits.

