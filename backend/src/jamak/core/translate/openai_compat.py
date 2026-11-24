"""Batch subtitle translation through any OpenAI-compatible chat endpoint.

Works with the managed llama-server and with external servers (Ollama, LM Studio, ...).
Line alignment is enforced by a JSON schema whose keys are exactly the batch's ids; a
batch that still comes back wrong is retried, then split in half, down to single lines.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

import httpx

from jamak.config import GlossaryEntry, TranslateOptions
from jamak.core.translate.prompts import SYSTEM_PROMPT, build_user_message, response_schema
from jamak.core.types import Line

ProgressFn = Callable[[float, str], None]


class TranslationCancelled(Exception):
    pass


class BatchError(Exception):
    pass


class OpenAICompatTranslator:
    def __init__(self, base_url: str, opts: TranslateOptions, model_name: str = "local",
                 api_key: str | None = None, transport: httpx.BaseTransport | None = None):
        self.base_url = base_url.rstrip("/")
        self.opts = opts
        self.model_name = model_name
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        self.client = httpx.Client(timeout=opts.request_timeout, headers=headers, transport=transport)
        self.stats = {"requests": 0, "retries": 0, "splits": 0, "failed_lines": 0,
                      "completion_tokens": 0, "prompt_tokens": 0}
        self._lock = threading.Lock()

    def close(self) -> None:
        self.client.close()

    # ------------------------------------------------------------------ one request
    def _request(self, batch: list[Line], context: list[dict], glossary: list[GlossaryEntry]) -> dict[int, str]:
        ids = [ln.id for ln in batch]
        body = {
            "model": self.model_name,
            "temperature": self.opts.temperature,
            "max_tokens": 120 * len(batch) + 64,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": build_user_message(
                    [{"id": ln.id, "ja": ln.ja} for ln in batch], context, glossary)},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "subtitles", "strict": True, "schema": response_schema(ids)},
            },
        }
        r = self.client.post(f"{self.base_url}/chat/completions", json=body)
        if r.status_code != 200:
            raise BatchError(f"HTTP {r.status_code}: {r.text[:300]}")
        data = r.json()
        with self._lock:
            self.stats["requests"] += 1
            usage = data.get("usage") or {}
            self.stats["completion_tokens"] += int(usage.get("completion_tokens") or 0)
            self.stats["prompt_tokens"] += int(usage.get("prompt_tokens") or 0)
        content = (data["choices"][0]["message"].get("content") or "").strip()
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

    def _translate_batch(self, batch: list[Line], context: list[dict], glossary: list[GlossaryEntry],
                         cancel: threading.Event | None) -> dict[int, str]:
        for attempt in range(self.opts.max_retries + 1):
            if cancel is not None and cancel.is_set():
                raise TranslationCancelled()
            try:
                return self._request(batch, context, glossary)
            except (BatchError, httpx.HTTPError, KeyError, IndexError):
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
        right_ctx = context + [{"id": ln.id, "ja": ln.ja, "ko": left.get(ln.id, "")} for ln in batch[:mid]]
        n = self.opts.context_lines
        right = self._translate_batch(batch[mid:], right_ctx[-n:] if n else [], glossary, cancel)
        return {**left, **right}

    # ------------------------------------------------------------------ public
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
        done = 0
        total = len(todo)
        ctx_n = self.opts.context_lines

        def apply(result: dict[int, str]) -> None:
            nonlocal done
            for i, ko in result.items():
                ln = by_id[i]
                ln.ko = ko
                ln.flags = [f for f in ln.flags if f != "translate_failed"]
                if not ko and ln.ja.strip():
                    ln.flags.append("translate_failed")
            done += len(result)
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
                for f in futures:
                    apply(f.result())
