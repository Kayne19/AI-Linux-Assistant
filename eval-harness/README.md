# Eval Harness (Scaffold)

`eval-harness` is a standalone, generic-first scaffold for environment-based evaluation workflows.

## Scope

- Core contracts for scenarios, verification checks, run artifacts, and grader outputs.
- Scenario validation that enforces runnable scenarios include both:
- broken-state checks
- resolution checks
- A plugin boundary for optional grading logic.

## Out of Scope

- Provider-specific adapters or product-specific assumptions.
- Built-in canonical scoring for all users.

## Plugin Boundary

The core harness records artifacts. Grading is optional and lives behind `GraderPlugin`.
`ExampleGrader` is intentionally simple and demonstrates how to compute metrics from stored
artifacts without changing the artifact data.

## Quick Start

```bash
cd eval-harness
python -m pytest
```
