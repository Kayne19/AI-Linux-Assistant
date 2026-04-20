"""Merge loader outputs into a validated DocumentIdentity by layer precedence.

Precedence (strict): sidecar > heuristics > pdf_meta > llm
"""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from ingestion.identity.schema import DocumentIdentity
from ingestion.identity.vocabularies import (
    ALL_ENUMS,
    IngestSourceType,
    VendorOrProject,
    coerce_enum,
    coerce_enum_list,
)

if TYPE_CHECKING:
    from ingestion.audit import AuditLog


# Vendor string -> VendorOrProject value mapping for deterministic heuristic resolution
_VENDOR_STRING_MAP: dict[str, str] = {
    "proxmox": VendorOrProject.proxmox_server_solutions.value,
    "debian": VendorOrProject.debian_project.value,
    "ubuntu": VendorOrProject.ubuntu_canonical.value,
    "canonical": VendorOrProject.ubuntu_canonical.value,
    "docker": VendorOrProject.docker_inc.value,
    "red hat": VendorOrProject.red_hat.value,
    "rhel": VendorOrProject.red_hat.value,
    "centos": VendorOrProject.red_hat.value,
    "linux foundation": VendorOrProject.linux_foundation.value,
    "apache": VendorOrProject.apache_foundation.value,
    "nginx": VendorOrProject.nginx_inc.value,
    "fedora": VendorOrProject.fedora_project.value,
    "arch linux": VendorOrProject.arch_linux.value,
    "alpine": VendorOrProject.alpine_linux.value,
    "suse": VendorOrProject.suse.value,
    "opensuse": VendorOrProject.suse.value,
}

# List enum fields
_LIST_ENUM_FIELDS = {"init_systems", "package_managers", "major_subsystems"}

# Scalar enum fields
_SCALAR_ENUM_FIELDS = {
    "source_family",
    "vendor_or_project",
    "doc_kind",
    "trust_tier",
    "freshness_status",
    "os_family",
    "ingest_source_type",
}


@dataclass(slots=True)
class LayerContribution:
    layer: str       # "sidecar" | "heuristics" | "pdf_meta" | "llm"
    field: str
    value: object
    accepted: bool
    normalized: object


def build_canonical_source_id(canonical_title: str, version: str | None = None) -> str:
    """Build a URL-safe slug from title and optional version."""
    slug = re.sub(r"[^a-z0-9]+", "_", (canonical_title or "").lower()).strip("_") or "unnamed"
    if version:
        v_slug = re.sub(r"[^a-z0-9]+", "_", version.lower()).strip("_")
        if v_slug:
            slug = f"{slug}__{v_slug}"
    return slug


def _is_empty(value) -> bool:
    """Return True when a value is absent/unknown/empty for precedence purposes."""
    if value is None:
        return True
    if isinstance(value, str) and value.strip() in ("", "unknown"):
        return True
    if isinstance(value, list):
        return not value or all(
            (v is None or (isinstance(v, str) and v.strip() in ("", "unknown")))
            for v in value
        )
    return False


def _heuristic_fields(heuristic_signals: dict, pdf_info: dict) -> dict:
    """Extract the fields that heuristics layer can contribute."""
    out: dict = {}

    # canonical_title: prefer metadata.title, fall back to first heading candidate
    meta_title = (heuristic_signals.get("metadata") or {}).get("title", "")
    if meta_title and meta_title.strip():
        out["canonical_title"] = meta_title.strip()
    elif heuristic_signals.get("heading_candidates"):
        out["canonical_title"] = heuristic_signals["heading_candidates"][0]

    # title_aliases: filename stem + second heading candidate
    aliases = []
    stem = heuristic_signals.get("stem", "")
    if stem:
        aliases.append(stem)
    headings = heuristic_signals.get("heading_candidates", [])
    if len(headings) > 1:
        aliases.append(headings[1])
    if aliases:
        out["title_aliases"] = aliases

    # version
    version_detected = heuristic_signals.get("version_detected")
    if version_detected:
        out["version"] = version_detected

    # vendor_or_project + source_family: derive from first detected vendor string
    vendors = heuristic_signals.get("vendors_detected", [])
    if vendors:
        raw = vendors[0].lower().strip()

        mapped = _VENDOR_STRING_MAP.get(raw)
        if mapped:
            coerced = coerce_enum("vendor_or_project", mapped)
            if coerced != "unknown":
                out["vendor_or_project"] = coerced

        sf_coerced = coerce_enum("source_family", raw)
        if sf_coerced != "unknown":
            out["source_family"] = sf_coerced

    return out


def _pdf_meta_fields(pdf_info: dict, stem: str) -> dict:
    """Extract fields that the PDF meta layer can contribute."""
    out: dict = {}

    title = (pdf_info.get("title") or "").strip()
    if title:
        out["canonical_title"] = title

    aliases = []
    if title:
        aliases.append(title)
    subject = (pdf_info.get("subject") or "").strip()
    if subject and subject != title:
        aliases.append(subject)
    if stem and stem not in aliases:
        aliases.append(stem)
    if aliases:
        out["title_aliases"] = aliases

    return out


def _merge_layers(
    sidecar: dict | None,
    heuristic_fields: dict,
    pdf_meta_fields: dict,
    llm_fields: dict,
) -> tuple[dict, list[LayerContribution]]:
    """For each field, pick the first non-empty layer value."""
    # All possible field names from all layers
    all_fields: set[str] = set()
    for d in (sidecar or {}, heuristic_fields, pdf_meta_fields, llm_fields):
        all_fields.update(d.keys())

    # Remove internal-only fields that resolver sets programmatically
    all_fields -= {
        "canonical_source_id",
        "operator_override_present",
        "ingest_source_type",
        "ingested_at",
        "pipeline_version",
    }

    layers_ordered = [
        ("sidecar", sidecar or {}),
        ("heuristics", heuristic_fields),
        ("pdf_meta", pdf_meta_fields),
        ("llm", llm_fields),
    ]

    merged: dict = {}
    contributions: list[LayerContribution] = []

    for field in sorted(all_fields):
        winner_layer: str | None = None
        winner_value = None

        layer_values: dict[str, object] = {}
        for layer_name, layer_data in layers_ordered:
            layer_values[layer_name] = layer_data.get(field)

        for layer_name, layer_data in layers_ordered:
            val = layer_data.get(field)
            if not _is_empty(val):
                if winner_layer is None:
                    winner_layer = layer_name
                    winner_value = val

        # Record contributions from all layers
        for layer_name, _ in layers_ordered:
            val = layer_values.get(layer_name)
            if val is not None:
                contributions.append(LayerContribution(
                    layer=layer_name,
                    field=field,
                    value=val,
                    accepted=(layer_name == winner_layer),
                    normalized=val,  # will be updated after coercion for accepted
                ))

        if winner_layer is not None:
            merged[field] = winner_value

    return merged, contributions


def _coerce_merged(merged: dict, contributions: list[LayerContribution]) -> dict:
    """Apply enum coercion to each field in the merged dict."""
    coerced: dict = {}
    for field, value in merged.items():
        if field in _LIST_ENUM_FIELDS:
            if isinstance(value, list):
                coerced[field] = coerce_enum_list(field, value)
            else:
                coerced[field] = coerce_enum_list(field, [value] if value else [])
        elif field in _SCALAR_ENUM_FIELDS:
            coerced[field] = coerce_enum(field, str(value) if value is not None else None)
        else:
            coerced[field] = value

    # Update normalized value on accepted contributions
    for c in contributions:
        if c.accepted and c.field in coerced:
            object.__setattr__(c, "normalized", coerced[c.field])

    return coerced


def _audit_field(
    audit: "AuditLog | None",
    doc: str,
    field: str,
    layer_values: dict,
    chosen_layer: str | None,
    chosen_value,
) -> None:
    if audit is None:
        return
    if chosen_layer is None:
        action = "accept_fallback_unknown"
    else:
        action = "accept"
    audit.record(
        doc=doc,
        phase="identity_resolution",
        action=action,
        inputs=layer_values,
        chosen={"field": field, "value": chosen_value, "source_layer": chosen_layer},
        confidence=None,
        rationale=None,
    )


def _audit_coercion_fallback(
    audit: "AuditLog | None",
    doc: str,
    field: str,
    raw_value: str,
) -> None:
    if audit is None:
        return
    audit.record(
        doc=doc,
        phase="identity_resolution",
        action="enum_coercion_fallback",
        inputs={"raw": raw_value},
        chosen={"field": field, "value": "unknown"},
        confidence=None,
        rationale="coercion returned unknown for raw value",
    )


def resolve_identity(
    *,
    pdf_path: Path,
    sidecar: dict | None,
    pdf_info: dict,
    heuristic_signals: dict,
    llm_fields: dict,
    pipeline_version: str,
    audit: "AuditLog | None" = None,
    run_id: str | None = None,
) -> tuple[DocumentIdentity, list[LayerContribution]]:
    """Merge layers by precedence, validate enums, and build DocumentIdentity."""
    stem = heuristic_signals.get("stem", pdf_path.stem)
    doc_label = pdf_path.name

    heuristic_flds = _heuristic_fields(heuristic_signals, pdf_info)
    pdf_meta_flds = _pdf_meta_fields(pdf_info, stem)

    merged, contributions = _merge_layers(sidecar, heuristic_flds, pdf_meta_flds, llm_fields)

    # Track which layers provided values before coercion, for audit
    all_layers = [
        ("sidecar", sidecar or {}),
        ("heuristics", heuristic_flds),
        ("pdf_meta", pdf_meta_flds),
        ("llm", llm_fields),
    ]

    # Derive winner layer per field directly from contributions (accepted entries)
    field_winner: dict[str, str | None] = {c.field: c.layer for c in contributions if c.accepted}
    # Ensure every merged field has an entry (None if no winner found)
    for field in merged:
        field_winner.setdefault(field, None)

    # Detect enum coercion fallbacks before coercing
    pre_coerce = dict(merged)
    coerced = _coerce_merged(merged, contributions)

    # Audit coercion fallbacks for scalar enum fields
    for field in _SCALAR_ENUM_FIELDS:
        if field in pre_coerce and field in coerced:
            raw = str(pre_coerce[field]) if pre_coerce[field] is not None else ""
            if coerced[field] == "unknown" and raw not in ("", "unknown"):
                _audit_coercion_fallback(audit, doc_label, field, raw)

    # Audit each field
    for field, value in coerced.items():
        layer_inputs = {
            layer_name: layer_data.get(field)
            for layer_name, layer_data in all_layers
        }
        chosen_layer = field_winner.get(field)
        _audit_field(audit, doc_label, field, layer_inputs, chosen_layer, value)

    # Determine canonical_title fallback
    canonical_title = coerced.get("canonical_title") or stem or "unnamed"

    # Determine if sidecar produced any accepted contribution
    operator_override_present = False
    if sidecar is not None:
        sidecar_fields = set(sidecar.keys()) & set(coerced.keys())
        for c in contributions:
            if c.layer == "sidecar" and c.accepted and c.field in sidecar_fields:
                operator_override_present = True
                break

    identity = DocumentIdentity(
        canonical_source_id=build_canonical_source_id(
            canonical_title, coerced.get("version") or None
        ),
        canonical_title=canonical_title,
        title_aliases=coerced.get("title_aliases") or [],
        source_family=coerced.get("source_family", "other"),
        product=coerced.get("product") or None,
        vendor_or_project=coerced.get("vendor_or_project", "unknown"),
        version=coerced.get("version") or None,
        release_date=coerced.get("release_date") or None,
        doc_kind=coerced.get("doc_kind", "other"),
        trust_tier=coerced.get("trust_tier", "unknown"),
        freshness_status=coerced.get("freshness_status", "unknown"),
        os_family=coerced.get("os_family", "unknown"),
        init_systems=coerced.get("init_systems") or ["unknown"],
        package_managers=coerced.get("package_managers") or ["unknown"],
        major_subsystems=coerced.get("major_subsystems") or [],
        applies_to=coerced.get("applies_to") or [],
        source_url=coerced.get("source_url") or None,
        ingest_source_type=IngestSourceType.pdf_operator.value,
        operator_override_present=operator_override_present,
        ingested_at=None,
        pipeline_version=pipeline_version,
    )

    if audit is not None:
        audit.record(
            doc=doc_label,
            phase="identity_resolution",
            action="resolve_complete",
            inputs=None,
            chosen={
                "canonical_source_id": identity.canonical_source_id,
                "canonical_title": identity.canonical_title,
            },
            confidence=None,
            rationale=None,
        )

    return identity, contributions
