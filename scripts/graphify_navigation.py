from __future__ import annotations

import argparse
import hashlib
import json
import re
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

HEADING_RE = re.compile(r"^(#{1,4})\s+(.+?)\s*$")
INLINE_CODE_RE = re.compile(r"`([^`]+)`")

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

ARCHITECTURE_WIKI_TARGET = (8_000, 18_000)
ARCHITECTURE_GRAPH_TARGET = (18_000, 32_000)
ARCHITECTURE_REDUCTION_BASELINE = 2.9


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
        "eval-harness/Back-end/eval_harness",
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
        "eval-harness/Back-end/eval_harness",
        "eval-harness/Back-end/tests",
        "run_eval_harness.py",
    ),
    output_dir="graphify-out/deep",
    keep_tests=True,
    architecture_prune=False,
)


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
            if should_exclude(child) or any(
                part in EXCLUDED_NAMES for part in rel.parts
            ):
                continue
            if child.is_file():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(child, target)


def stage_scope(repo_root: Path, stage_root: Path, scope: ScopeConfig) -> Path:
    stage_dir = stage_root / scope.name
    if stage_dir.exists():
        shutil.rmtree(stage_dir)
    stage_dir.mkdir(parents=True)

    for include in scope.includes:
        src = repo_root / include
        if not src.exists():
            continue
        copy_path(src, stage_dir / include)

    return stage_dir


def validate_scope_sources(repo_root: Path, scope: ScopeConfig) -> None:
    missing = [
        include for include in scope.includes if not (repo_root / include).exists()
    ]
    if missing:
        raise FileNotFoundError(
            f"Missing required graphify scope paths: {', '.join(missing)}"
        )


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


def should_skip_reference(label: str) -> bool:
    ref_path = Path(label)
    return should_exclude(ref_path) or any(
        part in EXCLUDED_NAMES or part in SENSITIVE_NAMES for part in ref_path.parts
    )


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
                    if (
                        "/" not in code_ref
                        and "." not in code_ref
                        and len(code_ref) < 3
                    ):
                        continue
                    if should_skip_reference(code_ref):
                        continue
                    ref_node = node_for(
                        code_ref,
                        rel,
                        "code" if "." in code_ref or "/" in code_ref else "document",
                    )
                    if ref_node["id"] not in seen:
                        seen[ref_node["id"]] = ref_node
                        nodes.append(ref_node)
                    edges.append(edge_for(parent_id, ref_node["id"], "references", rel))

    return {
        "nodes": nodes,
        "edges": edges,
        "hyperedges": [],
        "input_tokens": 0,
        "output_tokens": 0,
    }


def collect_code_files(corpus_root: Path) -> list[Path]:
    return [
        path
        for path in corpus_root.rglob("*")
        if path.is_file() and path.suffix in CODE_SUFFIXES and not should_exclude(path)
    ]


def is_architecture_node(node: dict) -> bool:
    label = str(node.get("label", ""))
    source = str(node.get("source_file", ""))
    if (
        label.startswith("_")
        or label.startswith(".")
        or label in {"__init__.py", "__init__"}
    ):
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


def run_ast_extraction(code_files: list[Path]) -> dict:
    if not code_files:
        return {
            "nodes": [],
            "edges": [],
            "hyperedges": [],
            "input_tokens": 0,
            "output_tokens": 0,
        }

    from graphify.extract import extract

    result = extract(code_files)
    result.setdefault("hyperedges", [])
    result.setdefault("input_tokens", 0)
    result.setdefault("output_tokens", 0)
    return result


def normalize_source_path(value: object, corpus_root: Path) -> object:
    if not isinstance(value, str) or not value:
        return value
    path = Path(value)
    if not path.is_absolute():
        return value
    try:
        return path.relative_to(corpus_root).as_posix()
    except ValueError:
        return value


def normalize_extraction_paths(extraction: dict, corpus_root: Path) -> dict:
    for collection in ("nodes", "edges", "hyperedges"):
        for item in extraction.get(collection, []):
            if isinstance(item, dict) and "source_file" in item:
                item["source_file"] = normalize_source_path(
                    item["source_file"], corpus_root
                )
    return extraction


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


def label_communities(graph, communities: dict[int, list[str]]) -> dict[int, str]:
    labels: dict[int, str] = {}
    for cid, node_ids in communities.items():
        best = f"Community {cid}"
        for node_id in sorted(
            node_ids, key=lambda nid: graph.degree(nid), reverse=True
        ):
            label = str(graph.nodes[node_id].get("label", node_id)).strip()
            if (
                label
                and label not in {"__init__.py", "__init__"}
                and not label.startswith("_")
            ):
                best = label[:48]
                break
        labels[cid] = best
    return labels


def detect_corpus_summary(corpus_root: Path) -> dict:
    files = [
        path
        for path in corpus_root.rglob("*")
        if path.is_file() and not should_exclude(path)
    ]
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


def build_graph_outputs(
    extraction: dict,
    corpus_root: Path,
    output_dir: Path,
    *,
    wiki: bool,
    report_root: Path | None = None,
) -> dict:
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
    tokens = {
        "input": extraction.get("input_tokens", 0),
        "output": extraction.get("output_tokens", 0),
    }

    report = generate(
        graph,
        communities,
        cohesion,
        labels,
        gods,
        surprises,
        detection,
        tokens,
        str(report_root or corpus_root),
        suggested_questions=questions,
    )
    (output_dir / "GRAPH_REPORT.md").write_text(report)
    to_json(graph, communities, output_dir / "graph.json")
    to_html(graph, communities, output_dir / "graph.html", community_labels=labels)
    if wiki:
        to_wiki(
            graph,
            communities,
            output_dir / "wiki",
            community_labels=labels,
            cohesion=cohesion,
            god_nodes_data=gods,
        )

    return {
        "nodes": graph.number_of_nodes(),
        "edges": graph.number_of_edges(),
        "communities": len(communities),
        "god_nodes": gods,
        "questions": questions,
    }


def build_navigation_metrics(*, architecture: dict, deep: dict | None) -> dict:
    warnings: list[str] = []
    arch_query_tokens = int(architecture.get("average_query_tokens", 0) or 0)
    if arch_query_tokens and arch_query_tokens > ARCHITECTURE_GRAPH_TARGET[1]:
        warnings.append(
            f"Architecture graph fallback is above target: {arch_query_tokens} tokens > "
            f"{ARCHITECTURE_GRAPH_TARGET[1]}"
        )
    arch_reduction = float(architecture.get("reduction", 0) or 0)
    if arch_reduction and arch_reduction < ARCHITECTURE_REDUCTION_BASELINE:
        warnings.append(
            f"Architecture graph benchmark is below baseline: {arch_reduction:.2f}x < "
            f"{ARCHITECTURE_REDUCTION_BASELINE:.2f}x"
        )
    if deep and deep.get("nodes") and architecture.get("nodes"):
        ratio = architecture["nodes"] / deep["nodes"]
        if ratio > 0.60:
            warnings.append(
                f"Architecture graph is not at least 40% smaller than deep graph: ratio={ratio:.2f}"
            )

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


def build_scope(repo_root: Path, stage_root: Path, scope: ScopeConfig) -> dict:
    validate_scope_sources(repo_root, scope)
    staged = stage_scope(repo_root, stage_root, scope)
    markdown = [
        path
        for path in staged.rglob("*")
        if path.is_file() and path.suffix.lower() in {".md", ".txt"}
    ]
    docs = extract_markdown_graph(markdown, staged)
    ast = normalize_extraction_paths(
        run_ast_extraction(collect_code_files(staged)), staged
    )
    if scope.architecture_prune:
        ast = prune_architecture_extraction(ast)
    extraction = merge_extractions(ast, docs)
    output_dir = repo_root / scope.output_dir
    if output_dir.exists():
        shutil.rmtree(output_dir)
    return build_graph_outputs(
        extraction,
        staged,
        output_dir,
        wiki=scope.name == "architecture",
        report_root=repo_root,
    )


def estimate_query_tokens(output_dir: Path) -> dict:
    graph_json = output_dir / "graph.json"
    if not graph_json.exists():
        return {}
    try:
        from graphify.benchmark import run_benchmark
    except Exception:
        return {}
    report = output_dir / "GRAPH_REPORT.md"
    corpus_words = (
        len(report.read_text(errors="ignore").split()) if report.exists() else 0
    )
    if corpus_words <= 0:
        return {}
    result = run_benchmark(str(graph_json), corpus_words=corpus_words)
    return {
        "average_query_tokens": result.get("avg_graph_tokens", 0),
        "reduction": result.get("reduction_factor", 0),
    }


def verify_outputs(graphify_out: Path) -> None:
    required = [
        graphify_out / "architecture" / "graph.json",
        graphify_out / "architecture" / "GRAPH_REPORT.md",
        graphify_out / "architecture" / "wiki" / "index.md",
        graphify_out / "deep" / "graph.json",
    ]
    missing = [path for path in required if not path.exists()]
    if missing:
        raise RuntimeError(
            "Missing graphify navigation artifacts: "
            + ", ".join(str(path) for path in missing)
        )

    arch_text = (
        (graphify_out / "architecture" / "GRAPH_REPORT.md").read_text(errors="ignore")
        + "\n"
        + (graphify_out / "architecture" / "wiki" / "index.md").read_text(
            errors="ignore"
        )
    ).lower()
    if "eval harness" not in arch_text and "eval-harness" not in arch_text:
        raise RuntimeError("Architecture graph/wiki missing eval-harness coverage")

    forbidden = ["tui_logs", "node_modules", ".env"]
    for artifact in (graphify_out / "architecture").rglob("*"):
        if artifact.is_file() and artifact.suffix in {".md", ".json"}:
            text = artifact.read_text(errors="ignore")
            for token in forbidden:
                if token in text:
                    raise RuntimeError(
                        f"Forbidden source appeared in architecture artifact: {token}"
                    )


def run_all(repo_root: Path) -> int:
    stage_root = repo_root / ".graphify-staging"
    if stage_root.exists():
        shutil.rmtree(stage_root)
    stage_root.mkdir()
    try:
        architecture = build_scope(repo_root, stage_root, ARCHITECTURE_SCOPE)
        architecture.update(
            estimate_query_tokens(repo_root / ARCHITECTURE_SCOPE.output_dir)
        )
        deep = build_scope(repo_root, stage_root, DEEP_SCOPE)
        deep.update(estimate_query_tokens(repo_root / DEEP_SCOPE.output_dir))
        metrics = build_navigation_metrics(architecture=architecture, deep=deep)
        (repo_root / "graphify-out").mkdir(exist_ok=True)
        (repo_root / "graphify-out" / "navigation.json").write_text(
            json.dumps(metrics, indent=2)
        )
        verify_outputs(repo_root / "graphify-out")
        for warning in metrics["warnings"]:
            print(f"WARNING: {warning}")
        return 0
    finally:
        shutil.rmtree(stage_root, ignore_errors=True)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build graphify navigation layers")
    parser.add_argument("command", choices=("architecture", "deep", "all", "verify"))
    parser.add_argument(
        "--repo-root",
        default=".",
        help="Repository root; defaults to current directory",
    )
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
