import json
import sys
import time
from pathlib import Path

from pypdf import PdfReader

SCRIPT_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = SCRIPT_DIR.parent
BACKEND_DIR = SCRIPTS_DIR.parent
APP_DIR = BACKEND_DIR / "app"

for path in (BACKEND_DIR, APP_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from app.ingestion.pipeline import export_full_text
from app.ingestion.console import print_artifact, print_banner, print_summary
from app.ingestion.stages.pdf_intake import process_pdf_parallel


def main() -> int:
    input_pdf = "data/The_Linux_Command_Line.pdf"
    print_banner("PDF INTAKE SCRIPT", [f"Source: {input_pdf}"], char="=")

    start_time = time.time()
    intake_result = process_pdf_parallel(
        input_pdf,
        batch_size=40,
        max_workers=4,
        hi_res_model_name="yolox",
        min_text_chars=50,
        ocr_dpi=300,
    )
    data = intake_result.elements
    duration = time.time() - start_time

    print_summary(
        "PDF intake script complete",
        [
            ("elements", len(data)),
            ("duration_s", f"{duration:.2f}"),
            ("pages_per_s", f"{len(PdfReader(input_pdf).pages) / duration:.2f}"),
            ("coverage_pct", f"{intake_result.page_coverage_pct:.1%}"),
        ],
    )

    txt_output = Path("doc_context.txt")
    export_full_text(Path(input_pdf), txt_output)

    out = "extracted_raw.json"
    with open(out, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False)

    print_artifact("raw output", Path(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
