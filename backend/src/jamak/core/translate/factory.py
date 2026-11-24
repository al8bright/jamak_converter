"""Translation profile -> options -> engine.

Priority for a job: the profile picked for the job -> the default profile in the settings
database -> the TranslateOptions already in the job options.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from jamak.config import PipelineOptions, TranslateOptions, default_llm_path
from jamak.core.store import ENV_KEYS, Profile, Store, store
from jamak.core.translate.base import BatchTranslator, EngineError

KIND_TO_MODE = {"local_managed": "managed", "local_external": "external", "anthropic": "anthropic",
                "gemini": "gemini"}
CLOUD_MODES = ("anthropic", "gemini")
PROVIDER_NAME = {"anthropic": "Anthropic", "gemini": "Google"}


def apply_profile(t: TranslateOptions, p: Profile) -> TranslateOptions:
    """Copy a profile's engine settings onto translate options (keeps request_timeout etc.)."""
    o = p.options
    data = t.model_dump()
    data.update(
        mode=KIND_TO_MODE[p.kind], profile_id=p.id, profile_name=p.name,
        model_path=None, base_url=None, model_name="local", api_model=None, api_key_id=p.api_key_id,
        batch_size=o.batch_size, context_lines=o.context_lines, gpu_layers=o.gpu_layers,
        parallel=o.parallel or (4 if p.cloud else 1), effort=o.effort, refusal=o.refusal,
    )
    if o.temperature is not None:
        data["temperature"] = o.temperature
    if p.kind == "local_managed":
        data["model_path"] = p.model or None
    elif p.kind == "local_external":
        data["base_url"] = p.base_url
        data["model_name"] = p.model or "local"
    else:
        data["api_model"] = p.model
    return TranslateOptions.model_validate(data)


def resolve_options(opts: PipelineOptions, profile_id: int | None = None, db: Store | None = None) -> PipelineOptions:
    """Return a copy of `opts` with the chosen (or default) profile applied."""
    db = db or store
    pid = profile_id if profile_id is not None else opts.translate.profile_id
    p = db.get_profile(pid) if pid is not None else db.default_profile()
    if pid is not None and p is None:
        raise EngineError(f"번역 프로필 {pid}이(가) 없습니다")
    out = opts.model_copy(deep=True)
    if p is not None:
        out.translate = apply_profile(out.translate, p)
    return out


def local_fallback_options(t: TranslateOptions, db: Store | None = None) -> TranslateOptions | None:
    """Options for retranslating refused lines on this PC: the first local profile, or the default GGUF."""
    db = db or store
    local = next((p for p in db.list_profiles() if p.kind == "local_managed"), None)
    base = TranslateOptions(request_timeout=t.request_timeout, max_retries=t.max_retries)
    out = apply_profile(base, local) if local else base
    out.parallel = 1
    mp = Path(out.model_path) if out.model_path else default_llm_path()
    return out if mp is not None and mp.exists() else None


def resolve_secret(t: TranslateOptions, db: Store | None = None) -> str | None:
    if t.api_key:
        return t.api_key
    db = db or store
    if t.api_key_id is not None:
        return db.key_secret(t.api_key_id)
    for name in ENV_KEYS.get(t.mode, ()):
        if os.environ.get(name):
            return os.environ[name]
    return None


def model_label(t: TranslateOptions) -> str:
    if t.mode == "managed":
        mp = Path(t.model_path) if t.model_path else default_llm_path()
        return mp.name if mp else "(no GGUF model)"
    if t.mode == "external":
        return f"{t.model_name} @ {t.base_url}"
    return t.api_model or "?"


@contextmanager
def open_engine(t: TranslateOptions, log_path: Path | None = None, cancel: threading.Event | None = None,
                progress: Any = None, db: Store | None = None) -> Iterator[BatchTranslator]:
    """Create the engine for `t`; for the managed mode, run llama-server around it."""
    server = None
    engine: BatchTranslator
    try:
        if t.mode == "managed":
            from jamak.core.translate.llama_server import LlamaServer
            from jamak.core.translate.openai_compat import OpenAICompatTranslator

            model = Path(t.model_path) if t.model_path else default_llm_path()
            if model is None:
                raise EngineError("models 폴더에 번역 모델(*.gguf)이 없습니다")
            if progress:
                progress(0.0, f"loading {model.name}")
            server = LlamaServer(model, t, log_path=log_path)
            base_url = server.start(cancel=cancel)
            if progress:
                progress(0.0, f"llama-server ready :{server.port}")
            engine = OpenAICompatTranslator(base_url, t, "local")
        elif t.mode == "external":
            from jamak.core.translate.openai_compat import OpenAICompatTranslator

            if not t.base_url:
                raise EngineError("외부 서버 프로필에 서버 주소가 없습니다")
            engine = OpenAICompatTranslator(t.base_url, t, t.model_name, resolve_secret(t, db))
        else:
            key = resolve_secret(t, db)
            if not key:
                raise EngineError(f"{PROVIDER_NAME[t.mode]} API 키가 없습니다. 설정 › 번역 엔진에서 키를 추가하세요")
            if not t.api_model:
                raise EngineError("이 프로필에 모델 ID가 없습니다")
            if t.mode == "anthropic":
                from jamak.core.translate.anthropic_api import AnthropicTranslator

                engine = AnthropicTranslator(t, key, t.api_model)
            else:
                from jamak.core.translate.gemini_api import GeminiTranslator

                engine = GeminiTranslator(t, key, t.api_model)
            del key
        try:
            yield engine
        finally:
            engine.close()
    finally:
        if server is not None:
            server.stop()


TEST_LINE = "おはよう。今日も早いね"


def test_profile(p: Profile, db: Store | None = None) -> dict[str, Any]:
    """Translate one short line with this profile: checks key, model and response format."""
    from jamak.core.types import Line

    db = db or store
    t = apply_profile(TranslateOptions(max_retries=0, request_timeout=60), p)
    t.parallel = 1
    lines = [Line(id=1, start=0, end=1, ja=TEST_LINE)]
    try:
        with open_engine(t, db=db) as engine:
            engine.translate(lines, [])
        ln = lines[0]
        if "refused" in ln.flags:
            ok, msg = False, "서비스가 테스트 문장 번역을 거절했습니다"
        elif not ln.ko:
            ok, msg = False, "응답 형식이 맞지 않습니다"
        else:
            ok, msg = True, f"응답 확인 · [{{id, ko}}] 형식 OK · “{ln.ko}”"
    except EngineError as e:
        ok, msg = False, str(e)
    except Exception as e:  # network errors etc.; never include secrets (engines don't put them in messages)
        ok, msg = False, f"{type(e).__name__}: {str(e)[:200]}"
    db.set_test_result(p.id, ok, msg)
    return {"ok": ok, "message": msg}
