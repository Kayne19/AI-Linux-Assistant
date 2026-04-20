"""Provider-agnostic LLM-based document identity normalizer.

Returns a raw dict of field suggestions; enum coercion is the resolver's job.
"""

import inspect
import json

from ingestion.identity.vocabularies import (
    ALL_ENUMS,
    DocKind,
    FreshnessStatus,
    InitSystem,
    MajorSubsystem,
    OsFamily,
    PackageManager,
    SourceFamily,
    TrustTier,
    VendorOrProject,
)


LLM_NORMALIZER_SYSTEM_PROMPT = (
    "You are a document-identity normalizer. "
    "Given PDF signals, choose values from the supplied enum vocabularies for each field. "
    "If uncertain, use 'unknown'. "
    "Return a single JSON object matching the schema. "
    "Do not invent fields."
)


def _enum_values(enum_cls) -> list[str]:
    return [m.value for m in enum_cls]


def _build_schema() -> dict:
    """Build JSON Schema for the LLM output dynamically from vocabulary enums."""
    scalar_enum_fields = {
        "source_family": SourceFamily,
        "vendor_or_project": VendorOrProject,
        "doc_kind": DocKind,
        "trust_tier": TrustTier,
        "freshness_status": FreshnessStatus,
        "os_family": OsFamily,
    }
    list_enum_fields = {
        "init_systems": InitSystem,
        "package_managers": PackageManager,
        "major_subsystems": MajorSubsystem,
    }

    properties: dict = {
        "canonical_title": {"type": "string"},
        "title_aliases": {"type": "array", "items": {"type": "string"}},
        "product": {"type": "string"},
        "version": {"type": "string"},
        "release_date": {"type": "string"},
        "applies_to": {"type": "array", "items": {"type": "string"}},
        "source_url": {"type": "string"},
        "rationale": {"type": "string"},
    }

    for field_name, enum_cls in scalar_enum_fields.items():
        properties[field_name] = {
            "type": "string",
            "enum": _enum_values(enum_cls),
        }

    for field_name, enum_cls in list_enum_fields.items():
        properties[field_name] = {
            "type": "array",
            "items": {
                "type": "string",
                "enum": _enum_values(enum_cls),
            },
        }

    required = [
        "canonical_title",
        "title_aliases",
        "source_family",
        "product",
        "vendor_or_project",
        "version",
        "release_date",
        "doc_kind",
        "trust_tier",
        "freshness_status",
        "os_family",
        "init_systems",
        "package_managers",
        "major_subsystems",
        "applies_to",
        "source_url",
        "rationale",
    ]

    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


LLM_NORMALIZER_OUTPUT_SCHEMA: dict = _build_schema()


def build_llm_context(
    *,
    filename: str,
    pdf_info: dict,
    heuristic_signals: dict,
    sidecar: dict | None,
) -> str:
    """Assemble the user-message payload from PDF signals for the LLM normalizer."""
    # Trim potentially large heuristic fields
    signals = dict(heuristic_signals)
    if "front_matter_samples" in signals:
        signals["front_matter_samples"] = [
            s[:600] for s in (signals["front_matter_samples"] or [])
        ]
    if "heading_candidates" in signals:
        signals["heading_candidates"] = (signals["heading_candidates"] or [])[:15]

    sidecar_text = json.dumps(sidecar, ensure_ascii=False) if sidecar is not None else "none"

    parts = [
        f"<filename>\n{filename}\n</filename>",
        f"<pdf_info>\n{json.dumps(pdf_info, ensure_ascii=False)}\n</pdf_info>",
        f"<heuristic_signals>\n{json.dumps(signals, ensure_ascii=False)}\n</heuristic_signals>",
        f"<operator_sidecar>\n{sidecar_text}\n</operator_sidecar>",
        "<instructions>\nFill every field. Use \"unknown\" when unsure. Use the provided enum values verbatim.\n</instructions>",
    ]
    return "\n\n".join(parts)


def normalize_with_llm(
    *,
    worker,
    filename: str,
    pdf_info: dict,
    heuristic_signals: dict,
    sidecar: dict | None,
    cache_suffix: str | None = None,
) -> dict:
    """Return a raw dict of identity fields suggested by the LLM. Empty dict on any failure."""
    try:
        user_message = build_llm_context(
            filename=filename,
            pdf_info=pdf_info,
            heuristic_signals=heuristic_signals,
            sidecar=sidecar,
        )

        base_kwargs = dict(
            system_prompt=LLM_NORMALIZER_SYSTEM_PROMPT,
            user_message=user_message,
            history=[],
            temperature=0.1,
            structured_output=True,
            output_schema=LLM_NORMALIZER_OUTPUT_SCHEMA,
        )

        # Attempt to pass cache_config if worker supports it
        if cache_suffix is not None:
            sig = inspect.signature(worker.generate_text)
            if "cache_config" in sig.parameters:
                base_kwargs["cache_config"] = {
                    "enabled": True,
                    "scope": "identity_normalizer",
                    "key_suffix": cache_suffix,
                }

        try:
            raw = worker.generate_text(**base_kwargs)
        except TypeError:
            # Worker doesn't accept cache_config; retry without it
            base_kwargs.pop("cache_config", None)
            raw = worker.generate_text(**base_kwargs)

        if not raw:
            return {}

        return json.loads(raw)

    except Exception:
        return {}
