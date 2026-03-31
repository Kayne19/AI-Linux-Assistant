import json
import re
from collections import Counter, defaultdict
from typing import Any, Dict, List, Tuple

_COMMAND_HINTS = (
    "sudo ", "apt ", "apt-get ", "dnf ", "yum ", "pacman ", "zypper ",
    "systemctl ", "journalctl ", "grub", "mkfs", "mount ", "umount ",
    "lsblk", "fdisk", "parted", "nmcli", "ip ", "ifconfig", "rfkill",
)


def is_code_like(text: str) -> bool:
    value = text.strip()
    if not value:
        return False

    if value.startswith("$ ") or value.startswith("# ") or value.startswith("> "):
        return True

    if any(operator in value for operator in (" | ", "&&", "||", ">>", "2>&1", " > ", " < ")):
        return True
    if re.search(r"(^|\s)--[a-zA-Z0-9][\w-]*", value):
        return True
    if re.search(r"(^|\s)-[a-zA-Z]{1,2}(\s|$)", value):
        return True
    if re.search(r"/(etc|var|usr|bin|sbin|home|boot|dev|proc|sys)/", value):
        return True

    lowered = value.lower()
    if any(hint in lowered for hint in _COMMAND_HINTS):
        return True

    return False


def normalize_for_dedupe(text: str, code_like: bool) -> str:
    if code_like:
        value = text.replace("\r\n", "\n").replace("\r", "\n")
        value = "\n".join(line.rstrip() for line in value.split("\n")).strip()
        return value

    value = text.replace("\u00ad", "")
    value = re.sub(r"\s+", " ", value).strip()
    return value


TYPE_PRIORITY = {
    "Title": 0,
    "NarrativeText": 1,
    "ListItem": 2,
    "Table": 3,
    "Header": 4,
    "FigureCaption": 5,
    "UncategorizedText": 6,
    "Image": 7,
    "Footer": 8,
}


def element_score(element: Dict[str, Any]) -> Tuple[int, float, int]:
    element_type = element.get("type", "UncategorizedText")
    metadata = element.get("metadata", {}) or {}
    prob = metadata.get("detection_class_prob", 0.0) or 0.0
    length = len(element.get("text", "") or "")
    return (TYPE_PRIORITY.get(element_type, 50), -float(prob), -length)


def is_tiny_noise(text: str) -> bool:
    value = text.strip()
    if len(value) >= 12:
        return False
    alnum = sum(ch.isalnum() for ch in value)
    return (alnum / max(1, len(value))) < 0.25


def clean_elements(
    elements: List[Dict[str, Any]],
    drop_boilerplate: bool = False,
    boilerplate_page_fraction: float = 0.25,
) -> List[Dict[str, Any]]:
    by_page: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
    for element in elements:
        text = element.get("text", "")
        if text is None:
            continue
        if not str(text).strip():
            continue
        if element.get("type") == "Image" and not str(text).strip():
            continue
        by_page[int(element.get("metadata", {}).get("page_number", 0) or 0)].append(element)

    cleaned_pages: Dict[int, List[Dict[str, Any]]] = {}
    for page, items in by_page.items():
        best_by_key: Dict[str, Dict[str, Any]] = {}
        for element in items:
            text = element.get("text", "") or ""
            code_like = is_code_like(text)
            if (not code_like) and is_tiny_noise(text):
                continue

            key = normalize_for_dedupe(text, code_like=code_like)
            if not key:
                continue

            if key not in best_by_key or element_score(element) < element_score(best_by_key[key]):
                best_by_key[key] = element

        cleaned_pages[page] = list(best_by_key.values())

    if drop_boilerplate:
        page_count = len(cleaned_pages)
        freq = Counter()

        for items in cleaned_pages.values():
            seen_on_page = set()
            for element in items:
                text = element.get("text", "") or ""
                if is_code_like(text):
                    continue
                key = normalize_for_dedupe(text, code_like=False)
                if 0 < len(key) <= 80:
                    seen_on_page.add(key)
            for key in seen_on_page:
                freq[key] += 1

        boilerplate = {
            key for key, count in freq.items() if count >= max(2, int(page_count * boilerplate_page_fraction))
        }

        for page, items in cleaned_pages.items():
            new_items = []
            for element in items:
                text = element.get("text", "") or ""
                if is_code_like(text):
                    new_items.append(element)
                    continue
                key = normalize_for_dedupe(text, code_like=False)
                element_type = element.get("type", "")
                if key in boilerplate and element_type in ("Header", "Footer", "UncategorizedText"):
                    continue
                new_items.append(element)
            cleaned_pages[page] = new_items

    output = []
    for page in sorted(cleaned_pages.keys()):
        output.extend(cleaned_pages[page])
    return output


if __name__ == "__main__":
    in_path = "extracted_raw.json"
    out_path = "extracted_clean.json"

    with open(in_path, "r", encoding="utf-8") as handle:
        data = json.load(handle)

    cleaned = clean_elements(data, drop_boilerplate=False)

    with open(out_path, "w", encoding="utf-8") as handle:
        json.dump(cleaned, handle, indent=2, ensure_ascii=False)

    print(f"✅ Cleaned: {len(data)} → {len(cleaned)} elements")
    print(f"💾 Wrote: {out_path}")
