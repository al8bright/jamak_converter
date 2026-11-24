"""Shared batching for every translation engine.

Each engine only implements `_request(batch, context, glossary) -> {id: ko}`. This class
splits the lines into batches, adds context lines, checks that every id came back,
retries, splits a failing batch in half down to single lines, and runs batches in parallel.

Errors an engine can raise from `_request`:
- BatchError (or one of `transient`): retry, then split
- Refused: the service declined this batch (safety). Lines are flagged, not split
- EngineError: configuration/auth problem. Stops the whole translation at once
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from jamak.config import GlossaryEntry, TranslateOptions
from jamak.core.types import Line

ProgressFn = Callable[[float, str], None]


class TranslationCancelled(Exception):
    pass


class BatchError(Exception):
    pass


class Refused(Exception):
    def __init__(self, category: str | None = None):
        super().__init__(category or "refused")
        self.category = category


class EngineError(RuntimeError):
    """Not worth retrying: bad key, unknown model, unreachable configuration."""


class AuthError(EngineError):
    pass


def parse_ids(content: str, ids: list[int]) -> dict[int, str]:
    """Validate a `{"<id>": "<ko>"}` response against the requested ids."""
    content = (content or "").strip()
    if content.startswith("```"):
        content = content.strip("`").removeprefix("json").strip()
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as e:
        raise BatchError(f"invalid JSON: {e}") from e
    if not isinstance(parsed, dict):
        raise BatchError("response is not an object")
    out: dict[int, str] = {}
    for i in ids:
        v = parsed.get(str(i))
        if not isinstance(v, str):
            raise BatchError(f"missing id {i}")
        out[i] = v.strip()
    return out


class BatchTranslator:
    transient: tuple[type[BaseException], ...] = ()
    kind = "engine"

    def __init__(self, opts: TranslateOptions):
        self.opts = opts
        self.stats: dict[str, Any] = {
            "requests": 0, "retries": 0, "splits": 0, "failed_lines": 0, "refused_lines": 0,
            "prompt_tokens": 0, "completion_tokens": 0, "cache_read_tokens": 0, "cache_write_tokens": 0,
            "cost_usd": None, "batches_total": 0, "batches_done": 0,
        }
        self.refused_ids: set[int] = set()
        self._lock = threading.Lock()

    def close(self) -> None:
        pass

    # ------------------------------------------------------------------ engine hook
    def _request(self, batch: list[Line], context: list[dict], glossary: list[GlossaryEntry]) -> dict[int, str]:
        raise NotImplementedError

    def _record(self, prompt: int = 0, completion: int = 0, cache_read: int = 0, cache_write: int = 0,
                cost: float | None = None) -> None:
        with self._lock:
            s = self.stats
            s["requests"] += 1
            s["prompt_tokens"] += int(prompt or 0)
            s["completion_tokens"] += int(completion or 0)
            s["cache_read_tokens"] += int(cache_read or 0)
            s["cache_write_tokens"] += int(cache_write or 0)
            if cost is not None:
                s["cost_usd"] = round((s["cost_usd"] or 0.0) + cost, 6)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return dict(self.stats)

    # ------------------------------------------------------------------ batching
    def _translate_batch(self, batch: list[Line], context: list[dict], glossary: list[GlossaryEntry],
                         cancel: threading.Event | None) -> dict[int, str | None]:
        """{id: ko}; None means the service refused the line."""
        for attempt in range(self.opts.max_retries + 1):
            if cancel is not None and cancel.is_set():
                raise TranslationCancelled()
            try:
                return dict(self._request(batch, context, glossary))
            except Refused:
                with self._lock:
                    self.stats["refused_lines"] += len(batch)
                    self.refused_ids.update(ln.id for ln in batch)
                return {ln.id: None for ln in batch}
            except EngineError:
                raise
            except (BatchError, KeyError, IndexError, *self.transient):
                if attempt < self.opts.max_retries:
                    with self._lock:
                        self.stats["retries"] += 1
        if len(batch) == 1:
            with self._lock:
                self.stats["failed_lines"] += 1
            return {batch[0].id: ""}  # flagged by the caller
        with self._lock:
            self.stats["splits"] += 1
        mid = len(batch) // 2
        left = self._translate_batch(batch[:mid], context, glossary, cancel)
        right_ctx = context + [{"id": ln.id, "ja": ln.ja, "ko": left.get(ln.id) or ""} for ln in batch[:mid]]
        n = self.opts.context_lines
        right = self._translate_batch(batch[mid:], right_ctx[-n:] if n else [], glossary, cancel)
        return {**left, **right}

    def translate(self, lines: list[Line], glossary: list[GlossaryEntry],
                  progress: ProgressFn | None = None, cancel: threading.Event | None = None,
                  only_ids: set[int] | None = None) -> None:
        """Fill `ko` in place. Locked lines are never changed. `only_ids` limits the work."""
        todo = [ln for ln in lines if not ln.locked and (only_ids is None or ln.id in only_ids)]
        if not todo:
            return
        by_id = {ln.id: ln for ln in lines}
        index = {ln.id: i for i, ln in enumerate(lines)}
        bs = max(1, self.opts.batch_size)
        batches = [todo[i:i + bs] for i in range(0, len(todo), bs)]
        self.stats["batches_total"] = len(batches)
        done = 0
        total = len(todo)
        ctx_n = self.opts.context_lines

        def apply(result: dict[int, str | None]) -> None:
            nonlocal done
            for i, ko in result.items():
                ln = by_id[i]
                ln.flags = [f for f in ln.flags if f not in ("translate_failed", "refused")]
                if ko is None:
                    ln.flags.append("refused")  # keep any earlier translation
                    continue
                ln.ko = ko
                if not ko and ln.ja.strip():
                    ln.flags.append("translate_failed")
            done += len(result)
            with self._lock:
                self.stats["batches_done"] += 1
            if progress:
                progress(min(done / total, 0.999), f"{done}/{total} lines")

        def ctx_before(first: Line, with_ko: bool) -> list[dict]:
            if ctx_n <= 0:
                return []
            start = index[first.id]
            prev = lines[max(0, start - ctx_n):start]
            if with_ko:
                return [{"id": p.id, "ja": p.ja, "ko": p.ko} for p in prev]
            return [{"id": p.id, "ja": p.ja} for p in prev]

        if self.opts.parallel <= 1:
            for b in batches:
                if cancel is not None and cancel.is_set():
                    raise TranslationCancelled()
                apply(self._translate_batch(b, ctx_before(b[0], True), glossary, cancel))
        else:
            # concurrent batches cannot wait for each other's output: context is source text only
            with ThreadPoolExecutor(max_workers=self.opts.parallel) as pool:
                futures = [pool.submit(self._translate_batch, b, ctx_before(b[0], False), glossary, cancel)
                           for b in batches]
                try:
                    for f in futures:
                        apply(f.result())
                except BaseException:
                    for f in futures:
                        f.cancel()
                    raise
