"""Provider registry, OpenAI-compatible presets, DeepL / Papago, cloud fallback, key check."""

from __future__ import annotations

import json
import typing
from urllib.parse import parse_qs

import httpx
import pytest

from jamak.config import GlossaryEntry, TranslateOptions
from jamak.core import providers
from jamak.core.store import Kind, ProfileInput, ProfileOptions, Provider, Store, StoreError
from jamak.core.translate.base import EngineError
from jamak.core.translate.factory import apply_profile
from jamak.core.translate.mt_api import DeepLTranslator, PapagoTranslator, apply_glossary
from jamak.core.translate.openai_compat import OpenAICompatTranslator
from jamak.core.types import Line

pytest_plugins = ["tests.test_api"]  # the `client` fixture


def lines(*ja: str) -> list[Line]:
    return [Line(id=i + 1, start=i, end=i + 0.5, ja=t) for i, t in enumerate(ja)]


def test_registry_matches_store_and_options_types():
    cloud = set(providers.CLOUD_KINDS)
    assert cloud <= set(typing.get_args(Kind)) and cloud <= set(typing.get_args(Provider))
    mode = typing.get_args(TranslateOptions.model_fields["mode"].annotation)
    assert cloud <= set(mode)
    for p in providers.PROVIDERS:
        assert p.key_url.startswith("https://") and p.pricing_url.startswith("https://")
        if p.family == "openai":
            assert p.base_url and p.base_url.startswith("https://")


def test_preset_profile_gets_base_url_and_needs_model(tmp_path):
    db = Store(tmp_path / "s.db")
    with pytest.raises(StoreError, match="모델 ID"):
        db.add_profile(ProfileInput(name="o", kind="openai"))
    p = db.add_profile(ProfileInput(name="o", kind="openai", model="gpt-x"))
    t = apply_profile(TranslateOptions(), p)
    assert t.mode == "openai" and t.base_url == "https://api.openai.com/v1" and t.api_model == "gpt-x"
    assert db.add_profile(ProfileInput(name="d", kind="deepl")).cloud  # translation APIs have no model


def test_papago_key_needs_two_parts(tmp_path):
    db = Store(tmp_path / "s.db")
    with pytest.raises(StoreError, match="Client ID"):
        db.add_key("papago", "p", "onlyonepart123")
    k = db.add_key("papago", "p", "client-id-1234\nclient-secret-5678")
    assert k["last4"] == "5678" and "secret" not in k
    assert db.key_secret(k["id"]) == "client-id-1234\nclient-secret-5678"


def test_fallback_must_be_cloud(tmp_path):
    db = Store(tmp_path / "s.db")
    local = next(p for p in db.list_profiles() if p.kind == "local_managed")
    with pytest.raises(StoreError, match="클라우드"):
        db.add_profile(ProfileInput(name="o", kind="openai", model="m",
                                    options=ProfileOptions(fallback_profile_id=local.id)))


def test_openai_preset_request_shape():
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        seen.append((req.headers.get("authorization"), body))
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({"1": "안녕"})}}]})

    for pid in ("openai", "deepseek"):
        info = providers.BY_ID[pid]
        tr = OpenAICompatTranslator(info.base_url, TranslateOptions(), "m", "sk-test-123456",
                                    transport=httpx.MockTransport(handler), provider=info)
        ls = lines("おはよう")
        tr.translate(ls, [])
        assert ls[0].ko == "안녕"
    (auth, o), (_, d) = seen
    assert auth == "Bearer sk-test-123456"
    assert "max_completion_tokens" in o and "temperature" not in o and o["response_format"]["type"] == "json_schema"
    assert "max_tokens" in d and d["response_format"] == {"type": "json_object"}


def test_openai_preset_quota_stops():
    info = providers.BY_ID["openai"]
    tr = OpenAICompatTranslator(info.base_url, TranslateOptions(), "m", "k" * 10, provider=info,
                                transport=httpx.MockTransport(lambda r: httpx.Response(
                                    429, json={"error": {"message": "You exceeded your current quota"}})))
    with pytest.raises(EngineError, match="한도"):
        tr.translate(lines("あ"), [])


def test_glossary_substitution_longest_first():
    g = [GlossaryEntry(ja="風間", ko="카자마"), GlossaryEntry(ja="風間くん", ko="카자마")]
    assert apply_glossary("風間くんと風間", g, lambda k: f"<keep>{k}</keep>") == "<keep>카자마</keep>と<keep>카자마</keep>"


def test_deepl_request_and_characters():
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        seen.append((str(req.url), req.headers["authorization"], body))
        return httpx.Response(200, json={"translations": [
            {"text": t.replace("<keep>", "<keep>").replace("は来た", "가 왔다")} for t in body["text"]]})

    tr = DeepLTranslator(TranslateOptions(batch_size=10), "abc-123:fx", transport=httpx.MockTransport(handler))
    ls = lines("しんのすけは来た", "テスト")
    tr.translate(ls, [GlossaryEntry(ja="しんのすけ", ko="짱구")])
    url, auth, body = seen[0]
    assert url.startswith("https://api-free.deepl.com/v2/translate") and auth == "DeepL-Auth-Key abc-123:fx"
    assert body["text"][0] == "<keep>짱구</keep>は来た" and body["ignore_tags"] == ["keep"]
    assert ls[0].ko == "짱구가 왔다"
    assert tr.snapshot()["characters"] == len(body["text"][0]) + len("テスト")


def test_deepl_quota_is_engine_error():
    tr = DeepLTranslator(TranslateOptions(), "k" * 10, transport=httpx.MockTransport(lambda r: httpx.Response(456)))
    with pytest.raises(EngineError, match="한도"):
        tr.translate(lines("あ"), [])


def test_papago_batches_by_newline_and_falls_back_per_line():
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        form = parse_qs(req.content.decode())
        text = form["text"][0]
        calls.append((req.headers["x-ncp-apigw-api-key-id"], text))
        out = "합친 결과" if "\n" in text and len(calls) == 1 and "X" in text else text.replace("あ", "아")
        return httpx.Response(200, json={"message": {"result": {"translatedText": out}}})

    tr = PapagoTranslator(TranslateOptions(batch_size=5), "cid-123456\nsecret-123456",
                          transport=httpx.MockTransport(handler))
    ls = lines("あ", "ああ")
    tr.translate(ls, [])
    assert [ln.ko for ln in ls] == ["아", "아아"] and len(calls) == 1 and calls[0][0] == "cid-123456"
    calls.clear()
    ls = lines("X", "あ")  # the joined answer lost the line break: one request per line
    tr.translate(ls, [])
    assert len(calls) == 3 and [ln.ko for ln in ls] == ["X", "아"]


def test_cloud_fallback_finishes_after_engine_error(jamak_home, monkeypatch, tmp_path):
    from contextlib import contextmanager

    from jamak.config import PipelineOptions
    from jamak.core import pipeline
    from jamak.core.cache import Job
    from jamak.core.store import store

    primary = store.add_profile(ProfileInput(name="A", kind="openai", model="m"))
    backup = store.add_profile(ProfileInput(name="B", kind="deepseek", model="m"))
    primary = store.update_profile(primary.id, ProfileInput(name="A", kind="openai", model="m",
                                                            options=ProfileOptions(fallback_profile_id=backup.id)))

    class Fake:
        def __init__(self, t):
            self.t = t
            self.refused_ids = set()

        def translate(self, ls, glossary, progress=None, cancel=None, only_ids=None):
            if self.t.mode == "openai":
                ls[0].ko = "첫 줄"
                raise EngineError("OpenAI: 사용 한도를 넘었습니다")
            for ln in ls:
                if only_ids is None or ln.id in only_ids:
                    ln.ko = f"B{ln.id}"

        def snapshot(self):
            return {"requests": 1}

    @contextmanager
    def fake_open(t, **_):
        yield Fake(t)

    monkeypatch.setattr("jamak.core.translate.factory.open_engine", fake_open)
    opts = PipelineOptions()
    opts.translate = apply_profile(opts.translate, primary)
    video = tmp_path / "v.mkv"
    video.write_bytes(b"x")
    ls = lines("一", "二", "三")
    stats = pipeline.run_translation(ls, opts, Job(video))
    assert [ln.ko for ln in ls] == ["첫 줄", "B2", "B3"]
    assert stats["cloud_fallback"]["profile"] == "B" and "한도" in stats["cloud_fallback"]["reason"]
    assert "cloud_fallback" in ls[1].flags and "cloud_fallback" not in ls[0].flags


def test_providers_endpoint_and_key_check_hides_secret(client, monkeypatch):
    ps = client.get("/settings/providers").json()
    assert {p["id"] for p in ps} >= {"openai", "deepseek", "xai", "mistral", "openrouter", "deepl", "papago"}
    papago = next(p for p in ps if p["id"] == "papago")
    assert papago["key_parts"] == ["Client ID", "Client Secret"] and papago["llm"] is False

    k = client.post("/settings/api-keys", json={"provider": "openai", "label": "t", "secret": "sk-SECRET-987654"}).json()
    assert "secret" not in k and k["last4"] == "7654"

    def boom(base_url, key, transport=None):
        raise RuntimeError(f"bad key {key}")

    monkeypatch.setattr("jamak.core.translate.openai_compat.list_models", boom)
    r = client.post(f"/settings/api-keys/{k['id']}/check").json()
    assert r["ok"] is False and "sk-SECRET-987654" not in r["message"] and "••••" in r["message"]
    monkeypatch.setattr("jamak.core.translate.openai_compat.list_models", lambda b, k: ["gpt-a", "gpt-b"])
    assert client.post(f"/settings/api-keys/{k['id']}/check").json() == {"ok": True, "message": "키 확인됨 · 모델 2개"}
    assert client.get(f"/settings/models?kind=openai&key_id={k['id']}").json() == {"models": ["gpt-a", "gpt-b"]}
