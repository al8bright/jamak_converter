"""Claude (Anthropic API) subtitle translation.

- Structured outputs (`output_config.format`, JSON schema keyed by line id) keep lines aligned.
- The system prompt + glossary is the stable prefix and carries a cache breakpoint.
- Claude Opus 5.5 / Sonnet 5.5 requests opt into server-side refusal fallbacks
  (`fallbacks: "default"`). A refusal that survives the fallback flags the batch.
- The SDK retries 429/5xx itself (honouring retry-after); 401/403 stop the job at once.
- Only the Japanese dialogue text of each batch leaves the PC.
"""

from __future__ import annotations

from typing import Any

import anthropic

from jamak.config import GlossaryEntry, TranslateOptions
from jamak.core.translate.base import AuthError, BatchError, BatchTranslator, EngineError, Refused, parse_ids
from jamak.core.translate.prompts import build_user_message, response_schema, system_with_glossary
from jamak.core.types import Line

MODELS = ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"]
DEFAULT_MODEL = "claude-opus-5-5"
# server-side refusal fallback ("default" routing): Opus 5.5 / Sonnet 5.5; Haiku 5.5 has none
FALLBACK_MODELS = {"claude-opus-5-5", "claude-sonnet-5-5"}
FALLBACK_BETA = "server-side-fallback-2026-07-01"

# USD per 1M tokens: input, output, cache read. Cache writes cost 1.25x input (5-minute TTL).
PRICES: dict[str, tuple[float, float, float]] = {
    "claude-opus-5-5": (4.00, 20.00, 0.20),
    "claude-sonnet-5-5": (2.00, 10.00, 0.10),
    "claude-haiku-5-5": (0.10, 0.50, 0.01),
}


def estimate_cost(model: str, inp: int, out: int, cache_read: int, cache_write: int) -> float | None:
    p = PRICES.get(model)
    if p is None:
        return None
    return (inp * p[0] + out * p[1] + cache_read * p[2] + cache_write * p[0] * 1.25) / 1_000_000


class AnthropicTranslator(BatchTranslator):
    transient = (anthropic.APIConnectionError, anthropic.APITimeoutError)
    kind = "anthropic"

    def __init__(self, opts: TranslateOptions, api_key: str, model: str = DEFAULT_MODEL,
                 client: Any | None = None):
        super().__init__(opts)
        self.model = model
        self.client = client or anthropic.Anthropic(api_key=api_key, max_retries=4, timeout=opts.request_timeout)

    def close(self) -> None:
        close = getattr(self.client, "close", None)
        if close:
            close()

    def _params(self, batch: list[Line], context: list[dict], glossary: list[GlossaryEntry]) -> dict[str, Any]:
        output_config: dict[str, Any] = {
            "format": {"type": "json_schema", "schema": response_schema([ln.id for ln in batch])},
        }
        if self.opts.effort:
            output_config["effort"] = self.opts.effort
        params: dict[str, Any] = {
            "model": self.model,
            "max_tokens": 16000,  # thinking counts against this; non-streaming stays under HTTP timeouts
            "system": [{"type": "text", "text": system_with_glossary(glossary),
                        "cache_control": {"type": "ephemeral"}}],
            "messages": [{"role": "user", "content": build_user_message(
                [{"id": ln.id, "ja": ln.ja} for ln in batch], context, [])}],
            "output_config": output_config,
        }
        return params

    def _create(self, params: dict[str, Any]) -> Any:
        if self.model in FALLBACK_MODELS:
            return self.client.beta.messages.create(betas=[FALLBACK_BETA], fallbacks="default", **params)
        return self.client.messages.create(**params)

    def _request(self, batch: list[Line], context: list[dict], glossary: list[GlossaryEntry]) -> dict[int, str]:
        try:
            resp = self._create(self._params(batch, context, glossary))
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as e:
            raise AuthError("Anthropic이 API 키를 거부했습니다. 설정 › 번역 엔진에서 키를 확인하세요") from e
        except anthropic.NotFoundError as e:
            raise EngineError(f"모델을 찾을 수 없습니다: {self.model}") from e
        except (anthropic.RateLimitError, anthropic.InternalServerError) as e:
            raise BatchError(f"{type(e).__name__} after retries") from e
        except anthropic.BadRequestError as e:
            raise BatchError(f"bad request: {getattr(e, 'message', e)}") from e
        except anthropic.APIStatusError as e:
            raise BatchError(f"HTTP {e.status_code}") from e

        u = resp.usage
        served = getattr(resp, "model", None) or self.model
        inp = getattr(u, "input_tokens", 0) or 0
        out = getattr(u, "output_tokens", 0) or 0
        cr = getattr(u, "cache_read_input_tokens", 0) or 0
        cw = getattr(u, "cache_creation_input_tokens", 0) or 0
        self._record(inp, out, cr, cw, estimate_cost(served if served in PRICES else self.model, inp, out, cr, cw))

        if resp.stop_reason == "refusal":
            details = getattr(resp, "stop_details", None)
            raise Refused(getattr(details, "category", None) if details else None)
        if resp.stop_reason == "max_tokens":
            raise BatchError("response cut off at max_tokens")
        text = next((b.text for b in resp.content if getattr(b, "type", None) == "text"), "")
        return parse_ids(text, [ln.id for ln in batch])
