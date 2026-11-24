"""Gemini (google-genai SDK) subtitle translation.

JSON output via `response_json_schema`; blocked prompts and safety stops count as refusals,
handled the same way as Claude refusals. 429/5xx are retried by the SDK.
"""

from __future__ import annotations

from typing import Any

from jamak.config import GlossaryEntry, TranslateOptions
from jamak.core.translate.base import AuthError, BatchError, BatchTranslator, EngineError, Refused, parse_ids
from jamak.core.translate.prompts import build_user_message, response_schema, system_with_glossary
from jamak.core.types import Line

REFUSAL_REASONS = {"SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII", "RECITATION", "MODEL_ARMOR"}


def _client(api_key: str, timeout_s: float) -> Any:
    from google import genai
    from google.genai import types

    return genai.Client(api_key=api_key, http_options=types.HttpOptions(
        timeout=int(timeout_s * 1000),
        retry_options=types.HttpRetryOptions(attempts=5, http_status_codes=[408, 429, 500, 502, 503, 504]),
    ))


def list_models(api_key: str) -> list[str]:
    """Model IDs this key can call generateContent on."""
    out = []
    for m in _client(api_key, 30).models.list():
        actions = m.supported_actions or []
        if "generateContent" in actions:
            out.append((m.name or "").removeprefix("models/"))
    return sorted(x for x in out if x)


class GeminiTranslator(BatchTranslator):
    kind = "gemini"

    def __init__(self, opts: TranslateOptions, api_key: str, model: str, client: Any | None = None):
        super().__init__(opts)
        from google.genai import errors

        self.errors = errors
        self.model = model
        self.client = client or _client(api_key, opts.request_timeout)

    def close(self) -> None:
        close = getattr(self.client, "close", None)
        if close:
            close()

    def _request(self, batch: list[Line], context: list[dict], glossary: list[GlossaryEntry]) -> dict[int, str]:
        from google.genai import types

        ids = [ln.id for ln in batch]
        config = types.GenerateContentConfig(
            system_instruction=system_with_glossary(glossary),
            response_mime_type="application/json",
            response_json_schema=response_schema(ids),
            temperature=self.opts.temperature,
        )
        user = build_user_message([{"id": ln.id, "ja": ln.ja} for ln in batch], context, [])
        try:
            resp = self.client.models.generate_content(model=self.model, contents=user, config=config)
        except self.errors.ClientError as e:
            msg = str(getattr(e, "message", "") or e)
            if e.code in (401, 403) or (e.code == 400 and "API key" in msg):
                raise AuthError("Google이 API 키를 거부했습니다. 설정 › 번역 엔진에서 키를 확인하세요") from e
            if e.code == 404:
                raise EngineError(f"모델을 찾을 수 없습니다: {self.model}") from e
            raise BatchError(f"HTTP {e.code}") from e
        except self.errors.APIError as e:
            raise BatchError(f"HTTP {getattr(e, 'code', '?')}") from e

        u = resp.usage_metadata
        if u is not None:
            self._record(u.prompt_token_count or 0,
                         (u.candidates_token_count or 0) + (u.thoughts_token_count or 0),
                         u.cached_content_token_count or 0)
        else:
            self._record()
        fb = resp.prompt_feedback
        if fb is not None and fb.block_reason:
            raise Refused(str(fb.block_reason))
        cands = resp.candidates or []
        reason = str(getattr(cands[0].finish_reason, "name", cands[0].finish_reason)) if cands else ""
        if reason in REFUSAL_REASONS:
            raise Refused(reason)
        if reason == "MAX_TOKENS":
            raise BatchError("response cut off at max tokens")
        return parse_ids(resp.text or "", ids)
