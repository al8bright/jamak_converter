"""Per-video job folder with per-stage fingerprints.

A stage is skipped when its artifact exists and its fingerprint (own options + upstream
fingerprint) is unchanged, so e.g. swapping the translation model re-runs only translation.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
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


_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock_for(path: Path) -> threading.Lock:
    k = str(path.resolve()).lower()
    with _locks_guard:
        return _locks.setdefault(k, threading.Lock())


def write_json_atomic(path: Path, data: Any) -> None:
    if isinstance(data, BaseModel):
        text = data.model_dump_json(indent=1)
    else:
        text = json.dumps(data, ensure_ascii=False, indent=1)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    for attempt in range(40):  # Windows: replace fails while another handle has the target open
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == 39:
                tmp.unlink(missing_ok=True)
                raise
            time.sleep(0.05)


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


class Job:
    """Job folder. `meta.json` is only changed through `update_meta`, which re-reads the file
    under a per-path lock, so several Job objects (worker thread, API requests) never lose
    each other's updates."""

    def __init__(self, video: Path, root: Path | None = None, key: str | None = None):
        self.video = video.resolve()
        self.key = key or video_key(self.video)
        self.dir = (root or paths.jobs) / self.key
        self.dir.mkdir(parents=True, exist_ok=True)
        self.meta_path = self.dir / "meta.json"
        self.meta: dict[str, Any] = self._load_meta()
        if self.meta.get("video_path") != str(self.video) or "created" not in self.meta:
            self.update_meta(video_path=str(self.video), video_name=self.video.name,
                             created=self.meta.get("created") or _now())

    @classmethod
    def open_key(cls, key: str, root: Path | None = None) -> Job:
        d = (root or paths.jobs) / key
        meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
        return cls(Path(meta["video_path"]), root, key=key)  # works even if the video was moved

    def _load_meta(self) -> dict[str, Any]:
        for _ in range(5):
            if not self.meta_path.exists():
                break
            try:
                return json.loads(self.meta_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, PermissionError):
                time.sleep(0.02)
        return {"key": self.key, "stages": {}}

    def update_meta(self, _fn: Any = None, **values: Any) -> dict[str, Any]:
        with _lock_for(self.meta_path):
            meta = self._load_meta()
            if _fn is not None:
                _fn(meta)
            meta.update(values)
            meta["key"] = self.key
            meta["updated"] = _now()
            meta.setdefault("stages", {})
            write_json_atomic(self.meta_path, meta)
            self.meta = meta
            return meta

    def reload(self) -> dict[str, Any]:
        self.meta = self._load_meta()
        return self.meta

    def path(self, name: str) -> Path:
        return self.dir / name

    def is_fresh(self, stage: str, fp: str, *artifacts: str) -> bool:
        st = self.meta.get("stages", {}).get(stage)
        return bool(st and st.get("fingerprint") == fp and all(self.path(a).exists() for a in artifacts))

    def mark_done(self, stage: str, fp: str, elapsed: float, **extra: Any) -> None:
        info = {"fingerprint": fp, "finished": _now(), "elapsed": round(elapsed, 2), **extra}
        self.update_meta(lambda m: m.setdefault("stages", {}).__setitem__(stage, info))

    def stage_info(self, stage: str) -> dict[str, Any] | None:
        return self.meta.get("stages", {}).get(stage)
