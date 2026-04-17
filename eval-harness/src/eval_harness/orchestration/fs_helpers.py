import base64
import shlex

from ..controllers.base import SandboxController
from ..models import CommandExecutionResult


_MAX_TEXT_FILE_BYTES = 1024 * 512


def read_file(controller: SandboxController, session_key: str, path: str) -> CommandExecutionResult:
    """Read a text file from the sandbox."""
    script = f"""
import os, stat, sys
MAX_BYTES = {_MAX_TEXT_FILE_BYTES}
path = {repr(path)}
try:
    st = os.stat(path)
    if not stat.S_ISREG(st.st_mode):
        sys.stderr.write("Error: Only regular files can be read.\\n")
        sys.exit(1)
    if st.st_size > MAX_BYTES:
        sys.stderr.write(f"Error: File is too large to read safely ({{st.st_size}} bytes > {{MAX_BYTES}} byte limit).\\n")
        sys.exit(1)
    with open(path, "rb") as f:
        content = f.read()
        if b"\\0" in content:
            sys.stderr.write("Error: Binary file detected.\\n")
            sys.exit(1)
        text = content.decode("utf-8")
        sys.stdout.write(text)
except UnicodeDecodeError:
    sys.stderr.write("Error: File is not valid UTF-8 text.\\n")
    sys.exit(1)
except Exception as e:
    sys.stderr.write(f"Error reading file: {{e}}\\n")
    sys.exit(1)
"""
    cmd = f"python3 -c {shlex.quote(script)}"
    return controller.execute_command(cmd, session_key=session_key)


def apply_text_edit(
    controller: SandboxController, session_key: str, path: str, old_text: str, new_text: str
) -> CommandExecutionResult:
    """Apply an exact literal text replacement to a file."""
    script = f"""
import os, stat, sys, base64
MAX_BYTES = {_MAX_TEXT_FILE_BYTES}
path = {repr(path)}
old_text = base64.b64decode({repr(base64.b64encode(old_text.encode('utf-8')).decode('ascii'))})
new_text = base64.b64decode({repr(base64.b64encode(new_text.encode('utf-8')).decode('ascii'))})

try:
    st = os.stat(path)
    if not stat.S_ISREG(st.st_mode):
        sys.stderr.write("Error: Only regular files can be edited.\\n")
        sys.exit(1)
    if st.st_size > MAX_BYTES:
        sys.stderr.write(f"Error: File is too large to edit safely ({{st.st_size}} bytes > {{MAX_BYTES}} byte limit).\\n")
        sys.exit(1)
    with open(path, "rb") as f:
        content = f.read()
    if b"\\0" in content:
        sys.stderr.write("Error: Binary file detected.\\n")
        sys.exit(1)
    content.decode("utf-8")

    count = content.count(old_text)
    if count == 0:
        sys.stderr.write("Error: old_text not found in file.\\n")
        sys.exit(1)
    elif count > 1:
        sys.stderr.write("Error: old_text found multiple times. Match is ambiguous.\\n")
        sys.exit(1)

    new_content = content.replace(old_text, new_text)
    with open(path, "wb") as f:
        f.write(new_content)
    sys.stdout.write("File updated successfully.\\n")
except UnicodeDecodeError:
    sys.stderr.write("Error: File is not valid UTF-8 text.\\n")
    sys.exit(1)
except Exception as e:
    sys.stderr.write(f"Error editing file: {{e}}\\n")
    sys.exit(1)
"""
    cmd = f"python3 -c {shlex.quote(script)}"
    return controller.execute_command(cmd, session_key=session_key)
