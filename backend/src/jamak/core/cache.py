"""Per-video job folder with per-stage fingerprints.

A stage is skipped when its artifact exists and its fingerprint (own options + upstream
fingerprint) is unchanged, so e.g. swapping the translation model re-runs only translation.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from jamak.config import paths

_CHUNK = 4 * 1024 * 1024


def video_key(video: Path) -> str:
    """Content-based key (size + first/last 4 MB), stable across renames and moves."""
    size = video.stat().st_size
    h = hashlib.sha256(str(size).encode())
    with open(video, "rb") as f:
        h.update(f.read(_CHUNK))
        if size > 2 * _CHUNK:
            f.seek(-_CHUNK, os.SEEK_END)
            h.update(f.read(_CHUNK))
    return h.hexdigest()[:16]


def fingerprint(config: Any, upstream: str = "") -> str:
    if isinstance(config, BaseModel):
        config = config.model_dump(mode="json")
    blob = json.dumps(config, sort_keys=True, ensure_ascii=False) + "|" + upstream
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def write_json_atomic(path: Path, data: Any) -> None:
    if isinstance(data, BaseModel):
        text = data.model_dump_json(indent=1)
    else:
        text = json.dumps(data, ensure_ascii=False, indent=1)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


class Job:
    def __init__(self, video: Path, root: Path | None = None):
        self.video = video.resolve()
        self.key = video_key(self.video)
        self.dir = (root or paths.jobs) / self.key
        self.dir.mkdir(parents=True, exist_ok=True)
        self.meta_path = self.dir / "meta.json"
        self.meta: dict[str, Any] = self._load_meta()
        self.meta["video_path"] = str(self.video)
        self.meta["video_name"] = self.video.name
        self.meta.setdefault("created", time.strftime("%Y-%m-%dT%H:%M:%S"))
        self.save_meta()

    @classmethod
    def open_key(cls, key: str, root: Path | None = None) -> Job:
        d = (root or paths.jobs) / key
        meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
        return cls(Path(meta["video_path"]), root)

    def _load_meta(self) -> dict[str, Any]:
        if self.meta_path.exists():
            try:
                return json.loads(self.meta_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                pass
        return {"key": self.key, "stages": {}}

    def save_meta(self) -> None:
        self.meta["key"] = self.key
        self.meta["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        write_json_atomic(self.meta_path, self.meta)

    def path(self, name: str) -> Path:
        return self.dir / name

    def is_fresh(self, stage: str, fp: str, *artifacts: str) -> bool:
        st = self.meta.get("stages", {}).get(stage)
        return bool(st and st.get("fingerprint") == fp and all(self.path(a).exists() for a in artifacts))

    def mark_done(self, stage: str, fp: str, elapsed: float, **extra: Any) -> None:
        self.meta.setdefault("stages", {})[stage] = {
            "fingerprint": fp,
            "finished": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "elapsed": round(elapsed, 2),
            **extra,
        }
        self.save_meta()

    def stage_info(self, stage: str) -> dict[str, Any] | None:
        return self.meta.get("stages", {}).get(stage)
