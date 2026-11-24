"""Batch subtitle translation through any OpenAI-compatible chat endpoint.

Works with the managed llama-server, external servers (Ollama, LM Studio, ...) and the cloud
services in core/providers.py whose family is "openai" (OpenAI, DeepSeek, xAI, Mistral, OpenRouter).
Line alignment is enforced by a JSON schema whose keys are exactly the batch's ids.
"""

from __future__ import annotations

import httpx

from jamak.config import GlossaryEntry, TranslateOptions
from jamak.core.providers import ProviderInfo
from jamak.core.translate.base import (  # noqa: F401  (re-exported for older imports)
    AuthError,
    BatchError,
    BatchTranslator,
    EngineError,
    Refused,
    TranslationCancelled,
    parse_ids,
)
from jamak.core.translate.prompts import SYSTEM_PROMPT, build_user_message, response_schema
from jamak.core.types import Line


class OpenAICompatTranslator(BatchTranslator):
    transient = (httpx.HTTPError,)
    kind = "openai_compat"

    def __init__(self, base_url: str, opts: TranslateOptions, model_name: str = "local",
                 api_key: str | None = None, transport: httpx.BaseTransport | None = None,
                 provider: ProviderInfo | None = None):
        super().__init__(opts)
        self.provider = provider
        if provider is not None:
            self.kind = provider.id
        self.base_url = base_url.rstrip("/")
        self.model_name = model_name
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        self.client = httpx.Client(timeout=opts.request_timeout, headers=headers, transport=transport)

    def close(self) -> None:
        self.client.close()

    def _request(self, batch: list[Line], context: list[dict], glossary: list[GlossaryEntry]) -> dict[int, str]:
        from jamak.core.glossary import relevant

        ids = [ln.id for ln in batch]
        # local context windows are small: only the entries that occur in this batch or its context
        glossary = relevant(glossary, [ln.ja for ln in batch] + [c.get("ja", "") for c in context])
        pv = self.provider
        body: dict = {
            "model": self.model_name,
            (pv.token_param if pv else "max_tokens"): 120 * len(batch) + 64 + (2048 if pv else 0),
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
        if pv is None or pv.send_temperature:
            body["temperature"] = self.opts.temperature
        if pv is not None and pv.schema == "json_object":
            body["response_format"] = {"type": "json_object"}  # DeepSeek: no JSON schema; ids are checked anyway
        try:
            r = self.client.post(f"{self.base_url}/chat/completions", json=body)
        except httpx.ConnectError as e:
            raise EngineError(f"서버에 연결할 수 없습니다: {self.base_url}") from e
        if r.status_code in (401, 403):
            raise AuthError(f"서버가 키를 거부했습니다 (HTTP {r.status_code})")
        if pv is not None and r.status_code == 402:
            raise EngineError(f"{pv.name}: 잔액이 없거나 결제가 필요합니다 (HTTP 402)")
        if pv is not None and r.status_code == 400:
            raise EngineError(f"{pv.name}가 요청을 거부했습니다: {_error_text(r)}")
        if pv is not None and r.status_code == 429 and "quota" in r.text.lower():
            raise EngineError(f"{pv.name}: 사용 한도를 넘었습니다 (HTTP 429)")
        if r.status_code == 404:
            raise EngineError(f"{self.base_url}/chat/completions 이 없습니다. 서버 주소를 확인하세요")
        if r.status_code != 200:
            raise BatchError(f"HTTP {r.status_code}: {r.text[:300]}")
        data = r.json()
        usage = data.get("usage") or {}
        self._record(usage.get("prompt_tokens"), usage.get("completion_tokens"))
        choice = data["choices"][0]
        if choice.get("finish_reason") == "content_filter":
            raise Refused("content_filter")
        content = choice["message"].get("content") or ""
        return parse_ids(content, ids)


def _error_text(r: httpx.Response) -> str:
    try:
        err = r.json().get("error")
        msg = err.get("message") if isinstance(err, dict) else err
        return str(msg or r.text)[:200]
    except ValueError:
        return r.text[:200]


def list_models(base_url: str, key: str, transport: httpx.BaseTransport | None = None) -> list[str]:
    with httpx.Client(timeout=20, headers={"Authorization": f"Bearer {key}"}, transport=transport) as c:
        r = c.get(f"{base_url.rstrip('/')}/models")
        if r.status_code in (401, 403):
            raise AuthError(f"키가 거부되었습니다 (HTTP {r.status_code})")
        r.raise_for_status()
        return sorted({m["id"] for m in r.json().get("data", []) if isinstance(m, dict) and m.get("id")})
