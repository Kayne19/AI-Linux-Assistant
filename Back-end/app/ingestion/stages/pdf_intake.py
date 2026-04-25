import logging
import os
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import onnxruntime as ort
from pypdf import PdfReader, PdfWriter
from tqdm import tqdm
from unstructured.partition.pdf import partition_pdf

from ingestion.console import print_state, print_summary

logger = logging.getLogger(__name__)

os.environ.setdefault("OMP_THREAD_LIMIT", "1")
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("NUMEXPR_NUM_THREADS", "1")

_OriginalInferenceSession = ort.InferenceSession


class PatchedInferenceSession(_OriginalInferenceSession):
    def __init__(self, path_or_bytes, sess_options=None, providers=None, **kwargs):
        available = set(ort.get_available_providers())

        preferred = []
        if "CUDAExecutionProvider" in available:
            preferred.append("CUDAExecutionProvider")
        preferred.append("CPUExecutionProvider")
        preferred = [provider for provider in preferred if provider != "TensorrtExecutionProvider"]

        if providers:
            providers = [provider for provider in providers if provider != "TensorrtExecutionProvider"]
        else:
            providers = preferred

        if "providers" in kwargs and kwargs["providers"]:
            kwargs["providers"] = [provider for provider in kwargs["providers"] if provider != "TensorrtExecutionProvider"]

        super().__init__(path_or_bytes, sess_options=sess_options, providers=providers, **kwargs)


ort.InferenceSession = PatchedInferenceSession


@dataclass
class IntakeResult:
    """Structured result from process_pdf_parallel.

    Attributes:
        elements: List of extracted element dicts.
        total_pages: Total number of pages in the source PDF.
        processed_pages: Number of pages that were successfully processed.
        failed_batches: List of dicts with keys start_page, end_page, error for each failed batch.
        page_coverage_pct: Fraction of pages successfully processed (0.0–1.0). 1.0 when total_pages==0.
    """

    elements: List[Dict[str, Any]] = field(default_factory=list)
    total_pages: int = 0
    processed_pages: int = 0
    failed_batches: List[Dict[str, Any]] = field(default_factory=list)
    page_coverage_pct: float = 1.0


def _safe_get_page_number(el_dict: Dict[str, Any]) -> int:
    return int(el_dict.get("metadata", {}).get("page_number", 0) or 0)


def _safe_get_y(el_dict: Dict[str, Any]) -> float:
    md = el_dict.get("metadata", {})
    coords = md.get("coordinates", {})
    points = coords.get("points") or []
    if points and isinstance(points, list) and len(points) > 0:
        try:
            return float(points[0][1])
        except Exception:
            return 0.0
    return 0.0


def _sort_key(el_dict: Dict[str, Any]) -> Tuple[int, float]:
    return (_safe_get_page_number(el_dict), _safe_get_y(el_dict))


def classify_pages_by_text(pdf_path: str, min_chars: int = 50) -> Tuple[List[int], List[int]]:
    reader = PdfReader(pdf_path)
    texty, ocr = [], []
    for idx, page in enumerate(reader.pages):
        txt = (page.extract_text() or "").strip()
        if len(txt) >= min_chars:
            texty.append(idx)
        else:
            ocr.append(idx)
    return texty, ocr


def write_pdf_subset(src_reader: PdfReader, page_indices: List[int]) -> str:
    writer = PdfWriter()
    for index in page_indices:
        writer.add_page(src_reader.pages[index])

    tmp = tempfile.NamedTemporaryFile(prefix="subset_", suffix=".pdf", delete=False)
    tmp_path = tmp.name
    tmp.close()

    with open(tmp_path, "wb") as handle:
        writer.write(handle)

    return tmp_path


def adjust_elements_page_numbers(
    element_dicts: List[Dict[str, Any]],
    global_page_numbers: List[int],
    source_filename: str,
    source_path: str,
) -> List[Dict[str, Any]]:
    output = []
    for element in element_dicts:
        metadata = element.setdefault("metadata", {})
        local_page_number = metadata.get("page_number")
        if isinstance(local_page_number, int) and 1 <= local_page_number <= len(global_page_numbers):
            metadata["page_number"] = int(global_page_numbers[local_page_number - 1])

        metadata["filename"] = source_filename
        metadata["source_path"] = source_path
        output.append(element)
    return output


def process_single_chunk(args):
    temp_filename, chunk_start_idx, source_path, source_filename, hi_res_model_name, min_text_chars, ocr_dpi = args

    subset_files = []
    try:
        reader = PdfReader(temp_filename)
        texty_idx, ocr_idx = classify_pages_by_text(temp_filename, min_chars=min_text_chars)
        all_dicts: List[Dict[str, Any]] = []

        if texty_idx:
            fast_pdf = write_pdf_subset(reader, texty_idx)
            subset_files.append(fast_pdf)

            fast_elements = partition_pdf(
                filename=fast_pdf,
                strategy="fast",
                infer_table_structure=False,
                extract_images_in_pdf=False,
            )
            fast_dicts = [element.to_dict() for element in fast_elements]
            fast_global_pages = [chunk_start_idx + index + 1 for index in texty_idx]
            all_dicts.extend(
                adjust_elements_page_numbers(
                    fast_dicts,
                    fast_global_pages,
                    source_filename,
                    source_path,
                )
            )

        if ocr_idx:
            ocr_pdf = write_pdf_subset(reader, ocr_idx)
            subset_files.append(ocr_pdf)

            hi_elements = partition_pdf(
                filename=ocr_pdf,
                strategy="hi_res",
                hi_res_model_name=hi_res_model_name,
                infer_table_structure=True,
                extract_images_in_pdf=False,
            )
            hi_dicts = [element.to_dict() for element in hi_elements]
            hi_global_pages = [chunk_start_idx + index + 1 for index in ocr_idx]
            all_dicts.extend(
                adjust_elements_page_numbers(
                    hi_dicts,
                    hi_global_pages,
                    source_filename,
                    source_path,
                )
            )

        all_dicts.sort(key=_sort_key)
        return all_dicts

    except Exception as exc:
        return {"error": f"Error processing {temp_filename}: {exc}"}

    finally:
        for file_path in subset_files:
            try:
                if os.path.exists(file_path):
                    os.remove(file_path)
            except Exception:
                pass


def process_pdf_parallel(
    pdf_path: str,
    batch_size: int = 25,
    max_workers: Optional[int] = None,
    hi_res_model_name: str = "yolox",
    min_text_chars: int = 50,
    ocr_dpi: int = 300,
) -> IntakeResult:
    if not os.path.exists(pdf_path):
        raise FileNotFoundError(pdf_path)

    reader = PdfReader(pdf_path)
    total_pages = len(reader.pages)

    if max_workers is None:
        max_workers = max(1, min(os.cpu_count() or 2, 4))

    source_filename = os.path.basename(pdf_path)
    print_state("📚 PDF_INTAKE", source_filename)
    print_summary(
        "PDF intake configuration",
        [
            ("source", source_filename),
            ("pages", total_pages),
            ("batch_size", batch_size),
            ("workers", max_workers),
        ],
    )

    # Build per-batch task list, recording (start, end) page ranges for failure tracking
    tasks = []
    batch_ranges: List[Tuple[int, int]] = []
    for start in range(0, total_pages, batch_size):
        end = min(start + batch_size - 1, total_pages - 1)
        writer = PdfWriter()
        for page in reader.pages[start : start + batch_size]:
            writer.add_page(page)

        tmp = tempfile.NamedTemporaryFile(prefix="worker_chunk_", suffix=".pdf", delete=False)
        tmp_path = tmp.name
        tmp.close()

        with open(tmp_path, "wb") as handle:
            writer.write(handle)

        tasks.append((tmp_path, start, pdf_path, source_filename, hi_res_model_name, min_text_chars, ocr_dpi))
        batch_ranges.append((start + 1, end + 1))  # 1-based page numbers for reporting

    all_elements: List[Dict[str, Any]] = []
    failed_batches: List[Dict[str, Any]] = []

    with ProcessPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(process_single_chunk, task): i for i, task in enumerate(tasks)}
        with tqdm(total=len(tasks), desc="Partitioning PDF batches", unit="batch", colour="cyan") as progress:
            for future in as_completed(futures):
                task_idx = futures[future]
                task_args = tasks[task_idx]
                chunk_path = task_args[0]
                start_page, end_page = batch_ranges[task_idx]
                try:
                    if os.path.exists(chunk_path):
                        os.remove(chunk_path)
                except Exception:
                    pass

                result = future.result()
                if isinstance(result, dict) and "error" in result:
                    error_str = result["error"]
                    failed_batches.append({
                        "start_page": start_page,
                        "end_page": end_page,
                        "error": error_str,
                    })
                    logger.warning(
                        "pdf_intake: batch failed pages=%d-%d error=%s",
                        start_page,
                        end_page,
                        error_str,
                    )
                else:
                    all_elements.extend(result)
                progress.update(1)

    all_elements.sort(key=_sort_key)

    # Compute processed_pages as total minus pages in failed batches
    failed_page_count = sum(b["end_page"] - b["start_page"] + 1 for b in failed_batches)
    processed_pages = total_pages - failed_page_count

    if total_pages == 0:
        page_coverage_pct = 1.0
    else:
        page_coverage_pct = processed_pages / total_pages

    print_summary(
        "PDF intake complete",
        [
            ("batches", len(tasks)),
            ("failed_batches", len(failed_batches)),
            ("elements", len(all_elements)),
            ("processed_pages", processed_pages),
            ("total_pages", total_pages),
            ("coverage_pct", f"{page_coverage_pct:.1%}"),
        ],
    )

    return IntakeResult(
        elements=all_elements,
        total_pages=total_pages,
        processed_pages=processed_pages,
        failed_batches=failed_batches,
        page_coverage_pct=page_coverage_pct,
    )


if __name__ == "__main__":
    input_pdf = "data/The_Linux_Command_Line.pdf"

    start_time = time.time()
    intake_result = process_pdf_parallel(
        input_pdf,
        batch_size=40,
        max_workers=4,
        hi_res_model_name="yolox",
        min_text_chars=50,
        ocr_dpi=300,
    )
    duration = time.time() - start_time

    print(f"\n✅ DONE! Extracted {len(intake_result.elements)} elements in {duration:.2f}s")
    print(f"⚡ Speed: {len(PdfReader(input_pdf).pages) / duration:.2f} pages/sec (approx)")
    print(f"📊 Coverage: {intake_result.page_coverage_pct:.1%} ({intake_result.processed_pages}/{intake_result.total_pages} pages)")
