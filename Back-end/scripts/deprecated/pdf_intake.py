import onnxruntime as ort
import os
import json
import time
from tqdm import tqdm
from pypdf import PdfReader, PdfWriter
from unstructured.partition.pdf import partition_pdf
from concurrent.futures import ProcessPoolExecutor, as_completed

# ==============================================================================
# 🛡️ GLOBAL PATCH (Must run in every worker process)
# ==============================================================================
# Force Tesseract to stay in its lane (1 thread per worker)
os.environ["OMP_THREAD_LIMIT"] = "1"

# Force ONNX to use CUDA only
_OriginalInferenceSession = ort.InferenceSession
class PatchedInferenceSession(_OriginalInferenceSession):
    def __init__(self, path_or_bytes, sess_options=None, providers=None, **kwargs):
        if providers:
            providers = [p for p in providers if p != 'TensorrtExecutionProvider']
        if 'providers' in kwargs:
            kwargs['providers'] = [p for p in kwargs['providers'] if p != 'TensorrtExecutionProvider']
        super().__init__(path_or_bytes, sess_options, providers, **kwargs)
ort.InferenceSession = PatchedInferenceSession
# ==============================================================================

def process_single_chunk(args):
    """
    Worker function. Each CPU core runs this independently.
    """
    temp_filename, offset_page, gpu_model_name = args
    
    try:
        # The worker loads its OWN copy of the model here
        elements = partition_pdf(
            filename=temp_filename,
            strategy="hi_res",
            hi_res_model_name=gpu_model_name,
            infer_table_structure=True,
            extract_images_in_pdf=False, # Set True if you need images extracted to disk
        )
        
        # Fix page numbers immediately
        for el in elements:
            if hasattr(el.metadata, 'page_number'):
                el.metadata.page_number += offset_page
                
        return elements
    except Exception as e:
        return f"Error processing {temp_filename}: {str(e)}"

def process_pdf_parallel(pdf_path, batch_size, max_workers):
    if not os.path.exists(pdf_path):
        return []

    # 1. Slice the PDF
    reader = PdfReader(pdf_path)
    total_pages = len(reader.pages)
    tasks = []
    
    print(f"\n🚀 TURBO MODE: Spawning {max_workers} Workers for {total_pages} pages...")
    
    # Prepare the batches
    for i in range(0, total_pages, batch_size):
        writer = PdfWriter()
        chunk_pages = reader.pages[i : i + batch_size]
        for page in chunk_pages:
            writer.add_page(page)
            
        temp_filename = f"temp_worker_chunk_{i}.pdf"
        writer.write(temp_filename)
        
        # Add task to queue: (Filename, PageOffset, ModelName)
        tasks.append((temp_filename, i, "yolox"))

    all_elements = []
    
    # 2. Ignite the Process Pool
    # This spins up 4 Python processes, each claiming 1 CPU Core + 1.3GB VRAM
    with ProcessPoolExecutor(max_workers=max_workers) as executor:
        # Submit all jobs
        futures = {executor.submit(process_single_chunk, task): task for task in tasks}
        
        # Track progress
        with tqdm(total=len(tasks), desc="Parallel Batches", unit="batch", colour="cyan") as pbar:
            for future in as_completed(futures):
                result = future.result()
                
                # Cleanup temp file
                task_args = futures[future]
                if os.path.exists(task_args[0]):
                    os.remove(task_args[0])
                
                if isinstance(result, list):
                    all_elements.extend(result)
                else:
                    print(f"\n❌ Worker Failed: {result}")
                
                pbar.update(1)

    # Sort elements by page number because they finish out of order
    all_elements.sort(key=lambda x: x.metadata.page_number if hasattr(x.metadata, 'page_number') else 0)
    
    # Sort by page
    all_elements.sort(key=lambda x: x.metadata.page_number)

    # --- ADD THIS LOOP ---
    real_name = os.path.basename(pdf_path)
    for el in all_elements:
        el.metadata.filename = real_name
    # ---------------------
    
    return all_elements

if __name__ == "__main__":
    # Point to the big manual
    input_pdf = "data/FLIGHT.pdf" 
    
    # TIMING START
    start_time = time.time()
    
    # Run with 4 Workers (Since you have 4 Cores)
    # Batch size 25 is a sweet spot for parallel (keeps RAM usage stable)
    final_data = process_pdf_parallel(input_pdf, batch_size=40, max_workers=6)
    
    duration = time.time() - start_time
    
    print(f"\n✅ DONE! Processed {len(final_data)} elements in {duration:.2f} seconds.")
    print(f"⚡ Speed: {350 / duration:.2f} pages/second (Approx)")

    # Save Results
    output_filename = "apollo_11_turbo_results.json"
    data_to_save = [el.to_dict() for el in final_data]
    with open(output_filename, "w") as f:
        json.dump(data_to_save, f, indent=2)