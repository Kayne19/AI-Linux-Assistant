import onnxruntime as ort
import numpy as np
import onnx
from onnx import helper, TensorProto

import onnxruntime as ort

# --- MONKEY PATCH: FORCE CUDA, IGNORE TENSORRT ---
# Unstructured tries to use TensorRT, fails, and falls back to CPU.
# We intercept the check and hide TensorRT so it defaults to CUDA.
original_providers = ort.get_available_providers
def patched_providers():
    return [p for p in original_providers() if p != 'TensorrtExecutionProvider']
ort.get_available_providers = patched_providers
# -------------------------------------------------

print("1. Checking Providers...")
# If 'CUDAExecutionProvider' is in this list, the drivers are found.
print(f"   Available: {ort.get_available_providers()}")

try:
    print("2. Creating Dummy Model (Opset 17, IR Version 8)...")
    input_info = helper.make_tensor_value_info("in", TensorProto.FLOAT, [1])
    output_info = helper.make_tensor_value_info("out", TensorProto.FLOAT, [1])
    node = helper.make_node("Identity", inputs=["in"], outputs=["out"])
    graph = helper.make_graph([node], "test", [input_info], [output_info])
    opset = helper.make_opsetid("", 17)
    
    # --- THIS WAS THE MISSING PIECE ---
    # We force the file format (IR) to version 8 so the runtime accepts it.
    model = helper.make_model(graph, opset_imports=[opset], ir_version=8) 
    # ----------------------------------
    
    onnx.save(model, "test_gpu.onnx")

    print("3. Attempting to load model on GPU...")
    sess = ort.InferenceSession("test_gpu.onnx", providers=['CUDAExecutionProvider'])
    
    print("4. Running Inference...")
    result = sess.run(None, {"in": np.array([1.0], dtype=np.float32)})
    
    print(f"✅ SUCCESS: ONNX ran on GPU! Result: {result[0]}")
    
except Exception as e:
    print(f"❌ FAILURE: {e}")