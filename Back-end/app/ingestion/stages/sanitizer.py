"""PDF sanitizer stage.

Rewrites a problem PDF so that partition_pdf can handle it.  Uses pypdf to
re-emit the file page-by-page, stripping /Annots from each page.  This fixes
the majority of real problem PDFs (malformed annotation objects, etc.) without
requiring any external tool.

Encryption handling is out of scope for this pass.
"""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def sanitize_pdf(src_pdf: Path, dst_pdf: Path) -> bool:
    """Attempt to rewrite a problem PDF into a form partition_pdf handles.

    Uses pypdf to re-emit the file, stripping annotations and normalizing
    structure.  Returns True on success, False if sanitization fails (caller
    should quarantine).
    """
    try:
        from pypdf import PdfReader, PdfWriter

        reader = PdfReader(str(src_pdf))
        writer = PdfWriter()

        for page in reader.pages:
            # Strip /Annots to avoid annotation-related parse errors
            if "/Annots" in page:
                del page["/Annots"]
            writer.add_page(page)

        dst_pdf.parent.mkdir(parents=True, exist_ok=True)
        with dst_pdf.open("wb") as fh:
            writer.write(fh)

        return True

    except Exception as exc:
        logger.warning(
            "sanitize_pdf: failed to sanitize %s → %s: %s",
            src_pdf,
            dst_pdf,
            exc,
        )
        return False
