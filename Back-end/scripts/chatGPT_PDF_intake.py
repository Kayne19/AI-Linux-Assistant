import os
import re
import json
import time
import tempfile
from typing import List, Tuple, Dict, Any, Optional
from concurrent.futures import ProcessPoolExecutor, as_completed

import onnxruntime as ort
from tqdm import tqdm
from pypdf import PdfReader, PdfWriter
from unstructured.partition.pdf import partition_pdf


# ==============================================================================
# 🛡️ CPU THREAD LIMITS (avoid oversubscription when running many workers)
# ==============================================================================
os.environ.setdefault("OMP_THREAD_LIMIT", "1")
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("NUMEXPR_NUM_THREADS", "1")


# ==============================================================================
# 🧠 ONNX Runtime Provider Patch (prefer CUDA, ban TensorRT)
# ==============================================================================
_OriginalInferenceSession = ort.InferenceSession

class PatchedInferenceSession(_OriginalInferenceSession):
    def __init__(self, path_or_bytes, sess_options=None, providers=None, **kwargs):
        available = set(ort.get_available_providers())

        # Build a preferred provider list:
        preferred = []
        if "CUDAExecutionProvider" in available:
            preferred.append("CUDAExecutionProvider")
        preferred.append("CPUExecutionProvider")

        # Remove TensorRT if present anywhere
        preferred = [p for p in preferred if p != "TensorrtExecutionProvider"]

        # If caller passed providers, respect but sanitize
        if providers:
            providers = [p for p in providers if p != "TensorrtExecutionProvider"]
        else:
            providers = preferred

        if "providers" in kwargs and kwargs["providers"]:
            kwargs["providers"] = [p for p in kwargs["providers"] if p != "TensorrtExecutionProvider"]

        super().__init__(path_or_bytes, sess_options=sess_options, providers=providers, **kwargs)

ort.InferenceSession = PatchedInferenceSession


# ==============================================================================
# Helpers
# ==============================================================================
def _safe_get_page_number(el_dict: Dict[str, Any]) -> int:
    return int(el_dict.get("metadata", {}).get("page_number", 0) or 0)

def _safe_get_y(el_dict: Dict[str, Any]) -> float:
    """
    Use top-left y coordinate if present; otherwise 0.
    """
    md = el_dict.get("metadata", {})
    coords = md.get("coordinates", {})
    points = coords.get("points") or []
    if points and isinstance(points, list) and len(points) > 0:
        # points are [[x,y], ...]
        try:
            return float(points[0][1])
        except Exception:
            return 0.0
    return 0.0

def _sort_key(el_dict: Dict[str, Any]) -> Tuple[int, float]:
    return (_safe_get_page_number(el_dict), _safe_get_y(el_dict))

def classify_pages_by_text(pdf_path: str, min_chars: int = 50) -> Tuple[List[int], List[int]]:
    """
    Returns (texty_page_indices, ocr_page_indices) as 0-based indices within this pdf.
    A page is "texty" if pypdf can extract at least min_chars of text.
    """
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
    """
    Writes a subset PDF to a unique temp file and returns its path.
    Caller must delete the file.
    """
    writer = PdfWriter()
    for i in page_indices:
        writer.add_page(src_reader.pages[i])

    tmp = tempfile.NamedTemporaryFile(prefix="subset_", suffix=".pdf", delete=False)
    tmp_path = tmp.name
    tmp.close()

    with open(tmp_path, "wb") as f:
        writer.write(f)

    return tmp_path

def adjust_elements_page_numbers(
    element_dicts: List[Dict[str, Any]],
    global_page_numbers: List[int],
    source_filename: str,
    source_path: str,
) -> List[Dict[str, Any]]:
    """
    Map element_dict['metadata']['page_number'] (1..N within subset PDF)
    back to the global page number in the original document.
    """
    out = []
    for d in element_dicts:
        md = d.setdefault("metadata", {})
        local_pn = md.get("page_number")
        if isinstance(local_pn, int) and 1 <= local_pn <= len(global_page_numbers):
            md["page_number"] = int(global_page_numbers[local_pn - 1])

        # Normalize filename + source
        md["filename"] = source_filename
        md["source_path"] = source_path

        out.append(d)
    return out


# ==============================================================================
# Worker
# ==============================================================================
def process_single_chunk(args):
    """
    Worker: takes a temp chunk PDF, runs FAST on texty pages and HI_RES on OCR pages,
    returns a list of element dicts.
    """
    temp_filename, chunk_start_idx, source_path, source_filename, hi_res_model_name, min_text_chars, ocr_dpi = args

    subset_files = []
    try:
        reader = PdfReader(temp_filename)
        texty_idx, ocr_idx = classify_pages_by_text(temp_filename, min_chars=min_text_chars)

        all_dicts: List[Dict[str, Any]] = []

        # --- FAST path (best for command fidelity) ---
        if texty_idx:
            fast_pdf = write_pdf_subset(reader, texty_idx)
            subset_files.append(fast_pdf)

            fast_elements = partition_pdf(
                filename=fast_pdf,
                strategy="fast",
                infer_table_structure=False,  # usually cleaner for fast text extraction
                extract_images_in_pdf=False,
            )
            fast_dicts = [el.to_dict() for el in fast_elements]

            fast_global_pages = [chunk_start_idx + i + 1 for i in texty_idx]  # 1-indexed global
            all_dicts.extend(
                adjust_elements_page_numbers(
                    fast_dicts, fast_global_pages, source_filename, source_path
                )
            )

        # --- HI_RES/OCR path (only where needed) ---
        if ocr_idx:
            ocr_pdf = write_pdf_subset(reader, ocr_idx)
            subset_files.append(ocr_pdf)

            # NOTE: pdf_image_dpi may exist in your unstructured version; if it errors, remove it.
            hi_elements = partition_pdf(
                filename=ocr_pdf,
                strategy="hi_res",
                hi_res_model_name=hi_res_model_name,
                infer_table_structure=True,
                extract_images_in_pdf=False,
                # pdf_image_dpi=ocr_dpi,
            )
            hi_dicts = [el.to_dict() for el in hi_elements]

            hi_global_pages = [chunk_start_idx + i + 1 for i in ocr_idx]  # 1-indexed global
            all_dicts.extend(
                adjust_elements_page_numbers(
                    hi_dicts, hi_global_pages, source_filename, source_path
                )
            )

        # Sort deterministically
        all_dicts.sort(key=_sort_key)
        return all_dicts

    except Exception as e:
        return {"error": f"Error processing {temp_filename}: {e}"}

    finally:
        for fp in subset_files:
            try:
                if os.path.exists(fp):
                    os.remove(fp)
            except Exception:
                pass


# ==============================================================================
# Orchestrator
# ==============================================================================
def process_pdf_parallel(
    pdf_path: str,
    batch_size: int = 25,
    max_workers: Optional[int] = None,
    hi_res_model_name: str = "yolox",
    min_text_chars: int = 50,
    ocr_dpi: int = 300,
) -> List[Dict[str, Any]]:
    if not os.path.exists(pdf_path):
        raise FileNotFoundError(pdf_path)

    reader = PdfReader(pdf_path)
    total_pages = len(reader.pages)

    if max_workers is None:
        max_workers = max(1, min(os.cpu_count() or 2, 4))  # conservative default

    source_filename = os.path.basename(pdf_path)

    print(f"\n🚀 Hybrid Mode: {total_pages} pages | batch_size={batch_size} | workers={max_workers}")

    tasks = []
    temp_files = []

    # Slice PDF into batches
    for start in range(0, total_pages, batch_size):
        writer = PdfWriter()
        for page in reader.pages[start : start + batch_size]:
            writer.add_page(page)

        tmp = tempfile.NamedTemporaryFile(prefix="worker_chunk_", suffix=".pdf", delete=False)
        tmp_path = tmp.name
        tmp.close()

        with open(tmp_path, "wb") as f:
            writer.write(f)

        temp_files.append(tmp_path)
        tasks.append((tmp_path, start, pdf_path, source_filename, hi_res_model_name, min_text_chars, ocr_dpi))

    all_elements: List[Dict[str, Any]] = []

    with ProcessPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(process_single_chunk, t): t for t in tasks}

        with tqdm(total=len(tasks), desc="Parallel Batches", unit="batch", colour="cyan") as pbar:
            for future in as_completed(futures):
                result = future.result()

                # Clean up the chunk file for this task
                task_args = futures[future]
                chunk_path = task_args[0]
                try:
                    if os.path.exists(chunk_path):
                        os.remove(chunk_path)
                except Exception:
                    pass

                if isinstance(result, dict) and "error" in result:
                    print(f"\n❌ Worker Failed: {result['error']}")
                else:
                    all_elements.extend(result)

                pbar.update(1)

    # Final deterministic sort
    all_elements.sort(key=_sort_key)

    return all_elements


if __name__ == "__main__":
    input_pdf = "data/Debian_Install_Guide.pdf"

    start_time = time.time()
    data = process_pdf_parallel(
        input_pdf,
        batch_size=40,
        max_workers=4,          # adjust based on CPU + GPU VRAM
        hi_res_model_name="yolox",
        min_text_chars=50,      # raise if you want to OCR more pages
        ocr_dpi=300,
    )
    duration = time.time() - start_time

    print(f"\n✅ DONE! Extracted {len(data)} elements in {duration:.2f}s")
    print(f"⚡ Speed: {len(PdfReader(input_pdf).pages) / duration:.2f} pages/sec (approx)")

    out = "extracted_raw.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    print(f"💾 Wrote: {out}")
