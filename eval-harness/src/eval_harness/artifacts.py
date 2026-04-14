from __future__ import annotations

import json
from pathlib import Path

from .models import ArtifactPack, GraderOutput


class ArtifactStore:
    """File-backed artifact persistence for replayable post-run analysis."""

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def group_directory(self, group_id: str) -> Path:
        directory = self.root / group_id
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def pack_path(self, group_id: str) -> Path:
        return self.group_directory(group_id) / "artifact-pack.json"

    def plugin_directory(self, group_id: str) -> Path:
        directory = self.group_directory(group_id) / "plugins"
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def save_pack(self, pack: ArtifactPack) -> Path:
        path = self.pack_path(pack.group_id)
        path.write_text(json.dumps(pack.to_dict(), indent=2), encoding="utf-8")
        return path

    def load_pack(self, path: str | Path) -> ArtifactPack:
        pack_path = Path(path)
        payload = json.loads(pack_path.read_text(encoding="utf-8"))
        return ArtifactPack.from_dict(payload)

    def save_plugin_output(self, group_id: str, output: GraderOutput) -> Path:
        path = self.plugin_directory(group_id) / f"{output.plugin_name}.json"
        path.write_text(json.dumps(output.to_dict(), indent=2), encoding="utf-8")
        return path
