# Graphify Navigation Design

## Goal

Split repository navigation into two generated graphify layers:

- A lean default architecture graph and wiki for everyday repo navigation
- A denser deep graph for code tracing and implementation-level questions

The default layer must represent the real system boundaries of this repository, including `Back-end/app`, `Front-end`, `Back-end/infra/public-api`, `Back-end/evals`, and `eval-harness`.

## Problem

The current single graph mixes architectural signal with low-level AST detail and junk surfaces such as logs. That creates two failures:

- Default navigation is noisy and expensive
- The graph still needs to preserve exact implementation seams for deep tracing

One artifact is doing two conflicting jobs.

## Recommended Approach

Use two graphify outputs under `graphify-out/`:

- `graphify-out/architecture/`
- `graphify-out/deep/`

`architecture/` is the default navigation surface. It contains:

- `graph.json`
- `graph.html`
- `GRAPH_REPORT.md`
- `wiki/`

`deep/` is the tracing surface. It contains:

- `graph.json`
- `graph.html`
- `GRAPH_REPORT.md`

The existing top-level `graphify-out/` may continue to hold shared metadata, but user-facing navigation should point to `architecture/` first.

## Scope

### Architecture Graph

The architecture graph is built from a curated corpus that preserves subsystem boundaries and design intent while reducing low-value detail.

Include:

- `README.md`
- `AGENTS.md`
- `CLAUDE.md`
- `Back-end/app`
- `Front-end/FRONTEND.md`
- `Back-end/infra/public-api`
- `Back-end/evals/README.md`
- `eval-harness` architecture and code surfaces that define its role, orchestration, schemas, entry points, and integration boundaries

Exclude or prune:

- Logs
- Generated artifacts
- Large data dumps
- Duplicate or near-duplicate workflow docs where they do not add navigation value
- Most test-only detail unless a test defines a meaningful system seam
- Low-level helper-heavy AST nodes that do not improve architectural understanding

The architecture layer should keep modules, routers, stores, workers, providers, entry points, harness orchestrators, and cross-subsystem seams.

### Deep Graph

The deep graph is built from the broader cleaned repository scope and is optimized for implementation tracing.

Include:

- Architecture graph scope
- `Front-end` code surfaces
- `Back-end/tests`
- `Back-end/scripts`
- `Back-end/evals`
- `eval-harness` code and tests where useful for traceability

Exclude:

- Logs
- Junk or machine-generated output
- Sensitive or irrelevant transient files

The deep graph keeps denser AST detail and richer implementation nodes for exact path queries.

## Wiki Design

Generate a wiki from the architecture graph at `graphify-out/architecture/wiki/`.

The wiki is the default navigation entry point. It must provide stable subsystem pages rather than forcing users to start from raw graph structure.

Expected pages include:

- Backend Architecture
- Auth
- Runs
- Memory
- Retrieval
- Frontend
- Public API
- Eval Harness
- Router Evals

Each page should summarize:

- What the subsystem is
- Why it exists
- Key concepts and load-bearing abstractions
- Connected subsystems
- Primary source files

The wiki does not replace the graph. It is a human-readable navigation layer on top of it.

## Data Flow

### Architecture Build

1. Stage a curated corpus that excludes junk surfaces and includes `eval-harness`
2. Run graphify detect on the curated corpus
3. Run AST extraction
4. Run semantic extraction only on architecture-relevant docs and surfaces
5. Merge extraction results
6. Build graph, cluster, label communities, and generate report
7. Export HTML and wiki into `graphify-out/architecture/`

### Deep Build

1. Stage a broader cleaned corpus that excludes logs and junk
2. Run full detect and AST extraction
3. Run semantic extraction on the broader documentation surface
4. Merge extraction results
5. Build graph, cluster, label communities, and generate report
6. Export HTML into `graphify-out/deep/`

## Default Navigation Behavior

When using graphify for repo understanding:

- Open the architecture wiki first
- Use the architecture graph for subsystem connections and high-level path tracing
- Drop into the deep graph only when the question requires exact code tracing

This preserves low token cost for routine questions while keeping implementation depth available on demand.

## Error Handling

- If a required subsystem path is missing, fail with a clear message naming the missing path
- If the architecture graph accidentally grows too large or noisy, surface the dominant sources and prune them rather than silently accepting degraded output
- If wiki generation fails, still emit the graph and report, but mark the architecture build incomplete
- If `eval-harness` is not represented in the architecture graph, treat that as a build failure because the repo-level navigation surface would be incomplete

## Testing And Verification

Verification should confirm:

- `graphify-out/architecture/graph.json` exists
- `graphify-out/architecture/wiki/index.md` exists
- `graphify-out/deep/graph.json` exists
- The architecture report includes communities or god nodes tied to `eval-harness`
- The architecture graph is materially smaller or cheaper than the deep graph
- The default navigation surface excludes known junk sources such as logs

Useful checks:

- Count nodes and edges in both graphs and compare
- Inspect architecture wiki index for expected subsystem pages
- Grep architecture outputs for `eval-harness`
- Confirm the deep graph still contains fine-grained code nodes that are absent from the architecture graph

## Tradeoffs

### Approach 1: Two Generated Graph Layers

Recommended.

Pros:

- Best match for navigation versus tracing workloads
- Lower default token usage
- Cleaner subsystem understanding
- Keeps exact tracing available

Cons:

- Two build targets to maintain
- Requires explicit rules for what belongs in each layer

### Approach 2: Single Dense Graph With Filtering

Rejected.

Pros:

- Less build orchestration

Cons:

- Default experience stays noisy
- Filtering is weaker than building the right graph in the first place

### Approach 3: Wiki Only

Rejected.

Pros:

- Very readable for humans

Cons:

- Loses graph-first path tracing as the default substrate
- Pushes users back into raw code too early

## Decision

Implement Approach 1.

The repository should have:

- A lean architecture graph plus wiki for default navigation
- A denser deep graph for code tracing
- First-class representation of `eval-harness` in the architecture layer

