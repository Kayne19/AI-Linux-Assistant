import shutil
from pathlib import Path


def rule(char: str = "─", width: int = 72) -> str:
    terminal_width = shutil.get_terminal_size((width, 20)).columns
    return char * min(width, max(40, terminal_width))


def print_banner(title: str, lines: list[str] | None = None, char: str = "─") -> None:
    print(f"\n{rule(char)}")
    print(title)
    for line in lines or []:
        print(line)
    print(rule(char))


def print_state(label: str, detail: str = "") -> None:
    suffix = f" • {detail}" if detail else ""
    print(f"\n{label}{suffix}")


def print_kv(label: str, value) -> None:
    print(f"  {label:<14} {value}")


def print_artifact(label: str, path: Path) -> None:
    print(f"  {label:<14} {path}")


def print_summary(title: str, items: list[tuple[str, object]]) -> None:
    print(f"\n{title}")
    for label, value in items:
        print_kv(label, value)


def print_progress(label: str, current: int, total: int, detail: str = "") -> None:
    suffix = f" • {detail}" if detail else ""
    print(f"  [{current}/{total}] {label}{suffix}")
