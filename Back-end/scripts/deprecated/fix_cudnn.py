import os
import glob
import nvidia.cudnn

# 1. Find where the drivers are living
cudnn_dir = os.path.dirname(nvidia.cudnn.__file__)
lib_dir = os.path.join(cudnn_dir, "lib")

print(f"📍 Checking driver folder: {lib_dir}")

# 2. Look for the "New" file (Version 9)
source_files = glob.glob(os.path.join(lib_dir, "libcudnn.so.9*"))
if not source_files:
    print("❌ Error: Could not find libcudnn.so.9! Are you sure PyTorch is installed?")
    exit(1)

source = source_files[0]
target = os.path.join(lib_dir, "libcudnn.so.8")

# 3. Create the "Fake" Version 8 link
if os.path.exists(target):
    print(f"⚠️  Target {target} already exists. Skipping.")
else:
    try:
        os.symlink(source, target)
        print(f"✅ Created Bridge: {os.path.basename(target)} -> {os.path.basename(source)}")
    except PermissionError:
        print("❌ Permission Denied! Try running with sudo or check folder permissions.")