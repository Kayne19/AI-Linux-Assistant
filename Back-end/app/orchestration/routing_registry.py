import json
import re
from pathlib import Path


REGISTRY_PATH = Path(__file__).resolve().with_name("routing_domains.json")

DEFAULT_REGISTRY = {
    "domains": [
        {
            "label": "debian",
            "aliases": ["debian", "apt", "dpkg"],
            "description": "Debian install, apt, dpkg, Debian installer, Debian versions.",
            "skip_rag": False,
            "builtin": True,
        },
        {
            "label": "proxmox",
            "aliases": ["proxmox", "pve", "lxc", "vm"],
            "description": "Proxmox/PVE, VM/LXC management, nodes, clusters, storage, backups, Proxmox UI.",
            "skip_rag": False,
            "builtin": True,
        },
        {
            "label": "docker",
            "aliases": ["docker", "container", "compose", "dockerfile"],
            "description": "Docker, containers/images, Dockerfile, docker compose, docker CLI.",
            "skip_rag": False,
            "builtin": True,
        },
        {
            "label": "general",
            "aliases": ["general", "linux"],
            "description": "Generic Linux shell/filesystem questions without a clear distro/platform.",
            "skip_rag": True,
            "builtin": True,
        },
        {
            "label": "no_rag",
            "aliases": ["no_rag"],
            "description": "Greetings, thanks, small talk, and meta questions.",
            "skip_rag": True,
            "builtin": True,
        },
    ]
}


def _normalize_label(label):
    label = (label or "").strip().lower()
    label = re.sub(r"[^a-z0-9]+", "_", label)
    label = re.sub(r"_+", "_", label).strip("_")
    return label


def _normalize_aliases(aliases):
    normalized = []
    for alias in aliases or []:
        alias = (alias or "").strip().lower()
        if alias and alias not in normalized:
            normalized.append(alias)
    return normalized


def load_registry():
    if not REGISTRY_PATH.exists():
        save_registry(DEFAULT_REGISTRY)
    with REGISTRY_PATH.open("r", encoding="utf-8") as f:
        registry = json.load(f)
    return _normalize_registry(registry)


def save_registry(registry):
    REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    normalized = _normalize_registry(registry)
    with REGISTRY_PATH.open("w", encoding="utf-8") as f:
        json.dump(normalized, f, indent=2)


def _normalize_registry(registry):
    domains = []
    seen_labels = set()
    for domain in registry.get("domains", []):
        label = _normalize_label(domain.get("label"))
        if not label or label in seen_labels:
            continue
        aliases = _normalize_aliases(domain.get("aliases", []))
        if label not in aliases:
            aliases.insert(0, label)
        domains.append(
            {
                "label": label,
                "aliases": aliases,
                "description": (domain.get("description") or "").strip(),
                "skip_rag": bool(domain.get("skip_rag", False)),
                "builtin": bool(domain.get("builtin", False)),
            }
        )
        seen_labels.add(label)
    return {"domains": domains}


def get_domains():
    return load_registry()["domains"]


def get_domain_map():
    return {domain["label"]: domain for domain in get_domains()}


def get_allowed_labels():
    return [domain["label"] for domain in get_domains()]


def get_skip_rag_labels():
    return {domain["label"] for domain in get_domains() if domain.get("skip_rag")}


def get_searchable_labels(labels=None):
    skip_labels = get_skip_rag_labels()
    labels = labels if labels is not None else get_allowed_labels()
    return [label for label in labels if label not in skip_labels]


def get_aliases_for_label(label):
    domain = get_domain_map().get(label)
    if domain:
        return domain.get("aliases", [])
    return [label]


def merge_domain_suggestion(suggestion):
    label = _normalize_label(suggestion.get("label"))
    if not label:
        return False, "missing label"

    registry = load_registry()
    domains = registry["domains"]
    aliases = _normalize_aliases(suggestion.get("aliases", []))
    if label not in aliases:
        aliases.insert(0, label)

    for domain in domains:
        if domain["label"] == label:
            merged_aliases = domain["aliases"][:]
            for alias in aliases:
                if alias not in merged_aliases:
                    merged_aliases.append(alias)
            domain["aliases"] = merged_aliases
            if suggestion.get("description") and not domain.get("description"):
                domain["description"] = suggestion["description"].strip()
            save_registry(registry)
            return True, f"updated existing domain '{label}'"

    domains.append(
        {
            "label": label,
            "aliases": aliases,
            "description": (suggestion.get("description") or "").strip(),
            "skip_rag": bool(suggestion.get("skip_rag", False)),
            "builtin": False,
        }
    )
    save_registry(registry)
    return True, f"added new domain '{label}'"
