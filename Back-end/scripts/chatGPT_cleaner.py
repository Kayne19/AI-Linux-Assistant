import json
import re
from collections import defaultdict, Counter
from typing import Dict, Any, List, Tuple, Optional


# ------------------------------------------------------------------
# Heuristic: detect command/code-like text (protect from normalization)
# ------------------------------------------------------------------
_COMMAND_HINTS = (
    "sudo ", "apt ", "apt-get ", "dnf ", "yum ", "pacman ", "zypper ",
    "systemctl ", "journalctl ", "grub", "mkfs", "mount ", "umount ",
    "lsblk", "fdisk", "parted", "nmcli", "ip ", "ifconfig", "rfkill",
)

def is_code_like(text: str) -> bool:
    t = text.strip()
    if not t:
        return False

    # shell prompts
    if t.startswith("$ ") or t.startswith("# ") or t.startswith("> "):
        return True

    # obvious operators / filesystem paths / flags
    if any(op in t for op in (" | ", "&&", "||", ">>", "2>&1", " > ", " < ")):
        return True
    if re.search(r"(^|\s)--[a-zA-Z0-9][\w-]*", t):
        return True
    if re.search(r"(^|\s)-[a-zA-Z]{1,2}(\s|$)", t):
        return True
    if re.search(r"/(etc|var|usr|bin|sbin|home|boot|dev|proc|sys)/", t):
        return True

    tl = t.lower()
    if any(h in tl for h in _COMMAND_HINTS):
        return True

    return False


# ------------------------------------------------------------------
# Normalization for dedupe keys (different for code vs prose)
# ------------------------------------------------------------------
def normalize_for_dedupe(text: str, code_like: bool) -> str:
    if code_like:
        # Preserve newlines and punctuation; only normalize line endings + trim trailing spaces
        t = text.replace("\r\n", "\n").replace("\r", "\n")
        t = "\n".join(line.rstrip() for line in t.split("\n")).strip()
        return t
    else:
        # Collapse whitespace for prose-ish
        t = text.replace("\u00ad", "")  # soft hyphen
        t = re.sub(r"\s+", " ", t).strip()
        return t


# ------------------------------------------------------------------
# Type scoring: choose "best" element when duplicates exist
# (Prefer structured/non-OCR text over UncategorizedText/Image OCR)
# ------------------------------------------------------------------
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

def element_score(el: Dict[str, Any]) -> Tuple[int, float, int]:
    t = el.get("type", "UncategorizedText")
    md = el.get("metadata", {}) or {}
    prob = md.get("detection_class_prob", 0.0) or 0.0
    length = len(el.get("text", "") or "")
    return (TYPE_PRIORITY.get(t, 50), -float(prob), -length)


# ------------------------------------------------------------------
# Noise heuristic (conservative): drop tiny mostly-symbol tokens if NOT code-like
# ------------------------------------------------------------------
def is_tiny_noise(text: str) -> bool:
    t = text.strip()
    if len(t) >= 12:
        return False
    # ratio of alnum to all chars
    alnum = sum(ch.isalnum() for ch in t)
    return (alnum / max(1, len(t))) < 0.25


# ------------------------------------------------------------------
# Cleaning
# ------------------------------------------------------------------
def clean_elements(
    elements: List[Dict[str, Any]],
    drop_boilerplate: bool = False,
    boilerplate_page_fraction: float = 0.25,  # only used if drop_boilerplate=True
) -> List[Dict[str, Any]]:
    # Group by page
    by_page: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
    for el in elements:
        text = el.get("text", "")
        if text is None:
            continue
        if not str(text).strip():
            continue

        # Drop empty OCR images (Image type with no text)
        if el.get("type") == "Image" and not str(text).strip():
            continue

        by_page[int(el.get("metadata", {}).get("page_number", 0) or 0)].append(el)

    # Page-local dedupe
    cleaned_pages: Dict[int, List[Dict[str, Any]]] = {}
    for page, items in by_page.items():
        best_by_key: Dict[str, Dict[str, Any]] = {}
        for el in items:
            txt = el.get("text", "") or ""
            code_like = is_code_like(txt)

            # Optional: drop tiny symbol noise (but NEVER for code-like)
            if (not code_like) and is_tiny_noise(txt):
                continue

            key = normalize_for_dedupe(txt, code_like=code_like)
            if not key:
                continue

            if key not in best_by_key:
                best_by_key[key] = el
            else:
                # Keep the best-scored element
                if element_score(el) < element_score(best_by_key[key]):
                    best_by_key[key] = el

        cleaned_pages[page] = list(best_by_key.values())

    # Optional: boilerplate removal (OFF by default)
    if drop_boilerplate:
        # Count repeated short lines across pages
        page_count = len(cleaned_pages)
        freq = Counter()

        for page, items in cleaned_pages.items():
            seen_on_page = set()
            for el in items:
                txt = el.get("text", "") or ""
                if is_code_like(txt):
                    continue
                key = normalize_for_dedupe(txt, code_like=False)
                if 0 < len(key) <= 80:
                    seen_on_page.add(key)
            for k in seen_on_page:
                freq[k] += 1

        boilerplate = {
            k for k, c in freq.items()
            if c >= max(2, int(page_count * boilerplate_page_fraction))
        }

        # Drop boilerplate only if it looks like header/footer-ish text
        for page, items in cleaned_pages.items():
            new_items = []
            for el in items:
                txt = el.get("text", "") or ""
                if is_code_like(txt):
                    new_items.append(el)
                    continue
                key = normalize_for_dedupe(txt, code_like=False)
                el_type = el.get("type", "")

                if key in boilerplate and el_type in ("Header", "Footer", "UncategorizedText"):
                    continue

                new_items.append(el)
            cleaned_pages[page] = new_items

    # Flatten and sort
    out = []
    for page in sorted(cleaned_pages.keys()):
        out.extend(cleaned_pages[page])
    return out


if __name__ == "__main__":
    in_path = "extracted_raw.json"
    out_path = "extracted_clean.json"

    with open(in_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    cleaned = clean_elements(
        data,
        drop_boilerplate=False,  # keep conservative; enable later if desired
    )

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(cleaned, f, indent=2, ensure_ascii=False)

    print(f"✅ Cleaned: {len(data)} → {len(cleaned)} elements")
    print(f"💾 Wrote: {out_path}")
