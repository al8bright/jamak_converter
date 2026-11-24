"""Batch subtitle translation through any OpenAI-compatible chat endpoint.

Works with the managed llama-server and with external servers (Ollama, LM Studio, ...).
Line alignment is enforced by a JSON schema whose keys are exactly the batch's ids.
"""

from __future__ import annotations

import httpx

from jamak.config import GlossaryEntry, TranslateOptions
from jamak.core.translate.base import (  # noqa: F401  (re-exported for older imports)
    AuthError,
    BatchError,
    BatchTranslator,
    EngineError,
    TranslationCancelled,
    parse_ids,
)
from jamak.core.translate.prompts import SYSTEM_PROMPT, build_user_message, response_schema
from jamak.core.types import Line


class OpenAICompatTranslator(BatchTranslator):
    transient = (httpx.HTTPError,)
    kind = "openai_compat"

    def __init__(self, base_url: str, opts: TranslateOptions, model_name: str = "local",
                 api_key: str | None = None, transport: httpx.BaseTransport | None = None):
        super().__init__(opts)
        self.base_url = base_url.rstrip("/")
        self.model_name = model_name
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        self.client = httpx.Client(timeout=opts.request_timeout, headers=headers, transport=transport)

    def close(self) -> None:
        self.client.close()

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
        try:
            r = self.client.post(f"{self.base_url}/chat/completions", json=body)
        except httpx.ConnectError as e:
            raise EngineError(f"서버에 연결할 수 없습니다: {self.base_url}") from e
        if r.status_code in (401, 403):
            raise AuthError(f"서버가 키를 거부했습니다 (HTTP {r.status_code})")
        if r.status_code == 404:
            raise EngineError(f"{self.base_url}/chat/completions 이 없습니다. 서버 주소를 확인하세요")
        if r.status_code != 200:
            raise BatchError(f"HTTP {r.status_code}: {r.text[:300]}")
        data = r.json()
        usage = data.get("usage") or {}
        self._record(usage.get("prompt_tokens"), usage.get("completion_tokens"))
        content = data["choices"][0]["message"].get("content") or ""
        return parse_ids(content, ids)
