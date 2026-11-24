"""Every cloud translation service the app knows, in one place.

A provider id is also the translation profile kind and the TranslateOptions mode for it.
Adding a service means adding one entry here (and an engine class when its API is not
OpenAI-compatible). No prices are kept here: they change too often; the key screen links
to each service's own pricing page.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Family = Literal["anthropic", "gemini", "openai", "deepl", "papago"]


@dataclass(frozen=True)
class ProviderInfo:
    id: str
    name: str  # company / service name shown in the UI
    family: Family  # which engine class talks to it
    env: tuple[str, ...] = ()  # environment variables that may hold the key
    base_url: str | None = None  # openai family: OpenAI-compatible endpoint
    key_url: str = ""  # where to create a key
    pricing_url: str = ""
    model_hint: str = ""  # example model id for the input placeholder
    models: tuple[str, ...] = ()  # offered in the model list when the service cannot list them
    lists_models: bool = False  # GET {base_url}/models works with the key
    llm: bool = True  # False: a translation API (DeepL, Papago), no prompt, no model choice
    key_parts: tuple[str, ...] = ("API 키",)  # Papago needs Client ID + Client Secret
    key_note: str = ""
    # OpenAI-compatible request details that differ between services
    schema: Literal["json_schema", "json_object"] = "json_schema"
    token_param: str = "max_tokens"
    send_temperature: bool = True
    tags: tuple[str, ...] = field(default_factory=tuple)


PROVIDERS: list[ProviderInfo] = [
    ProviderInfo("anthropic", "Anthropic Claude", "anthropic", env=("ANTHROPIC_API_KEY",),
                 key_url="https://console.anthropic.com/settings/keys", pricing_url="https://www.anthropic.com/pricing",
                 model_hint="claude-sonnet-5-5", lists_models=True),
    ProviderInfo("gemini", "Google Gemini", "gemini", env=("GEMINI_API_KEY", "GOOGLE_API_KEY"),
                 key_url="https://aistudio.google.com/apikey", pricing_url="https://ai.google.dev/gemini-api/docs/pricing",
                 model_hint="gemini-flash-latest", lists_models=True),
    ProviderInfo("openai", "OpenAI", "openai", env=("OPENAI_API_KEY",), base_url="https://api.openai.com/v1",
                 key_url="https://platform.openai.com/api-keys", pricing_url="https://openai.com/api/pricing/",
                 model_hint="gpt-5-mini", lists_models=True, token_param="max_completion_tokens",
                 send_temperature=False),
    ProviderInfo("deepseek", "DeepSeek", "openai", env=("DEEPSEEK_API_KEY",), base_url="https://api.deepseek.com",
                 key_url="https://platform.deepseek.com/api_keys",
                 pricing_url="https://api-docs.deepseek.com/quick_start/pricing",
                 model_hint="deepseek-chat", lists_models=True, schema="json_object", tags=("저렴",)),
    ProviderInfo("xai", "xAI Grok", "openai", env=("XAI_API_KEY",), base_url="https://api.x.ai/v1",
                 key_url="https://console.x.ai/", pricing_url="https://docs.x.ai/docs/models",
                 model_hint="grok-4", lists_models=True),
    ProviderInfo("mistral", "Mistral AI", "openai", env=("MISTRAL_API_KEY",), base_url="https://api.mistral.ai/v1",
                 key_url="https://console.mistral.ai/api-keys", pricing_url="https://mistral.ai/pricing",
                 model_hint="mistral-large-latest", lists_models=True),
    ProviderInfo("openrouter", "OpenRouter", "openai", env=("OPENROUTER_API_KEY",),
                 base_url="https://openrouter.ai/api/v1", key_url="https://openrouter.ai/settings/keys",
                 pricing_url="https://openrouter.ai/models", model_hint="anthropic/claude-sonnet-5.5",
                 lists_models=True, key_note="여러 회사 모델을 키 하나로 씁니다. 모델마다 구조화 출력 지원이 다릅니다",
                 tags=("여러 모델",)),
    ProviderInfo("deepl", "DeepL", "deepl", env=("DEEPL_AUTH_KEY",), key_url="https://www.deepl.com/your-account/keys",
                 pricing_url="https://www.deepl.com/pro-api", llm=False,
                 key_note="무료 키(끝이 :fx)는 월 50만 자까지입니다. 문맥과 말투 지시는 반영되지 않습니다",
                 tags=("번역 전용",)),
    ProviderInfo("papago", "NAVER Papago", "papago", key_url="https://console.ncloud.com/papago",
                 pricing_url="https://www.ncloud.com/product/aiService/papagoTranslation", llm=False,
                 key_parts=("Client ID", "Client Secret"),
                 key_note="NAVER Cloud Platform의 Papago Translation 애플리케이션 키입니다", tags=("번역 전용",)),
]

BY_ID: dict[str, ProviderInfo] = {p.id: p for p in PROVIDERS}
CLOUD_KINDS: tuple[str, ...] = tuple(p.id for p in PROVIDERS)
ENV_KEYS: dict[str, tuple[str, ...]] = {p.id: p.env for p in PROVIDERS if p.env}


def get(provider_id: str) -> ProviderInfo | None:
    return BY_ID.get(provider_id)


def join_secret(parts: list[str]) -> str:
    """Papago's two values are kept as one encrypted secret: '<client id>\\n<client secret>'."""
    return "\n".join(p.strip() for p in parts)


def split_secret(secret: str, n: int) -> list[str]:
    parts = secret.split("\n")
    if len(parts) != n:
        raise ValueError("키 형식이 맞지 않습니다")
    return parts


def public(p: ProviderInfo) -> dict:
    return {
        "id": p.id, "name": p.name, "family": p.family, "key_url": p.key_url, "pricing_url": p.pricing_url,
        "model_hint": p.model_hint, "lists_models": p.lists_models, "llm": p.llm, "key_parts": list(p.key_parts),
        "key_note": p.key_note, "tags": list(p.tags), "env": list(p.env),
    }
