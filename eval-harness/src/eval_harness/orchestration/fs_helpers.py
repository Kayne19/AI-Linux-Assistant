import base64
import shlex

from ..controllers.base import SandboxController
from ..models import CommandExecutionResult


def read_file(controller: SandboxController, session_key: str, path: str) -> CommandExecutionResult:
    """Read a text file from the sandbox."""
    script = f"""
import os, sys
path = {repr(path)}
try:
    with open(path, "rb") as f:
        content = f.read(1024 * 512) # read up to 512KB
        if b"\\0" in content:
            sys.stderr.write("Error: Binary file detected.\\n")
            sys.exit(1)
        sys.stdout.buffer.write(content)
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
import os, sys, base64
path = {repr(path)}
old_text = base64.b64decode({repr(base64.b64encode(old_text.encode('utf-8')).decode('ascii'))}).decode('utf-8')
new_text = base64.b64decode({repr(base64.b64encode(new_text.encode('utf-8')).decode('ascii'))}).decode('utf-8')

try:
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    
    count = content.count(old_text)
    if count == 0:
        sys.stderr.write("Error: old_text not found in file.\\n")
        sys.exit(1)
    elif count > 1:
        sys.stderr.write("Error: old_text found multiple times. Match is ambiguous.\\n")
        sys.exit(1)
        
    new_content = content.replace(old_text, new_text)
    with open(path, "w", encoding="utf-8") as f:
        f.write(new_content)
    sys.stdout.write("File updated successfully.\\n")
except Exception as e:
    sys.stderr.write(f"Error editing file: {{e}}\\n")
    sys.exit(1)
"""
    cmd = f"python3 -c {shlex.quote(script)}"
    return controller.execute_command(cmd, session_key=session_key)
