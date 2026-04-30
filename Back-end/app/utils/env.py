"""Single-source .env path resolution for the project.

All entry points that need to load .env should call
:func:`load_project_dotenv` instead of reaching for python-dotenv directly.
The function walks up from the caller's file location until it finds a
``.env`` file or hits the filesystem root.
"""

from pathlib import Path


def _find_env_file(
    start_dir: Path | None = None, env_name: str = ".env"
) -> Path | None:
    """Walk up from *start_dir* (the caller file's location) until *env_name* is found.

    Returns the path to the file, or ``None`` if the filesystem root is reached.
    """
    current = (start_dir or Path.cwd()).resolve()
    for _ in range(100):  # safety cap
        candidate = current / env_name
        if candidate.is_file():
            return candidate
        parent = current.parent
        if parent == current:
            return None  # reached the filesystem root
        current = parent
    return None


def load_project_dotenv(start_dir: Path | None = None, env_name: str = ".env") -> bool:
    """Find and load a ``.env`` file, searching upward from *start_dir*.

    By default the search starts from the directory containing the file that
    calls this function (via ``Path(__file__).resolve().parent`` when
    *start_dir* is ``None`` and a caller frame can't be inferred
    automatically).

    Returns ``True`` when a file was found and loaded, ``False`` otherwise.
    """
    try:
        from dotenv import load_dotenv as _load
    except ImportError:
        return False

    env_path = _find_env_file(start_dir=start_dir, env_name=env_name)
    if env_path is None:
        return False

    _load(env_path)
    return True
