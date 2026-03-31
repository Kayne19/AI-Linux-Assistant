import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = SCRIPT_DIR.parent
BACKEND_DIR = SCRIPTS_DIR.parent
APP_DIR = BACKEND_DIR / "app"

for path in (BACKEND_DIR, APP_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from app.ingestion.console import print_artifact, print_banner, print_summary
from app.ingestion.stages.cleaner import clean_elements


def main() -> int:
    in_path = "extracted_raw.json"
    out_path = "extracted_clean.json"
    print_banner("CLEANER SCRIPT", [f"Input: {in_path}"], char="=")

    with open(in_path, "r", encoding="utf-8") as handle:
        data = json.load(handle)

    cleaned = clean_elements(data, drop_boilerplate=False)

    with open(out_path, "w", encoding="utf-8") as handle:
        json.dump(cleaned, handle, indent=2, ensure_ascii=False)

    print_summary(
        "Cleaner complete",
        [
            ("raw", len(data)),
            ("clean", len(cleaned)),
        ],
    )
    print_artifact("clean output", Path(out_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
