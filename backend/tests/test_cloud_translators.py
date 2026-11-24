"""Claude / Gemini engines with fake SDK clients: alignment, retries, refusals, auth errors."""

import json
from types import SimpleNamespace as NS

import httpx
import pytest

from jamak.config import GlossaryEntry, TranslateOptions
from jamak.core.translate.base import AuthError
from jamak.core.types import Line


def make_lines(n: int) -> list[Line]:
    return [Line(id=i, start=i, end=i + 0.5, ja=f"日本語{i}") for i in range(1, n + 1)]


# ---------------------------------------------------------------------- Claude


class FakeAnthropic:
    def __init__(self, refuse_ids=(), garbage_first=False, error=None):
        self.calls: list[dict] = []
        self.refuse_ids = set(refuse_ids)
        self.garbage_first = garbage_first
        self.error = error
        self.messages = NS(create=self._create)
        self.beta = NS(messages=NS(create=self._create))

    def _create(self, **kw):
        self.calls.append(kw)
        if self.error:
            raise self.error
        ids = kw["output_config"]["format"]["schema"]["required"]
        usage = NS(input_tokens=100, output_tokens=10 * len(ids), cache_read_input_tokens=50,
                   cache_creation_input_tokens=0)
        if self.refuse_ids & {int(i) for i in ids}:
            return NS(stop_reason="refusal", stop_details=NS(category="general_harms"), content=[], usage=usage,
                      model=kw["model"])
        text = "oops" if self.garbage_first and len(self.calls) == 1 else json.dumps({i: f"ko{i}" for i in ids})
        return NS(stop_reason="end_turn", content=[NS(type="text", text=text)], usage=usage, model=kw["model"])


def claude(fake, model="claude-opus-5-5", **opt):
    from jamak.core.translate.anthropic_api import AnthropicTranslator

    return AnthropicTranslator(TranslateOptions(mode="anthropic", **opt), "k", model, client=fake)


def test_claude_structured_output_cache_and_fallback_params():
    fake = FakeAnthropic()
    lines = make_lines(5)
    tr = claude(fake, batch_size=2, effort="low")
    tr.translate(lines, [GlossaryEntry(ja="ユウキ", ko="유키")])
    assert [ln.ko for ln in lines] == [f"ko{i}" for i in range(1, 6)]
    kw = fake.calls[0]
    assert kw["betas"] == ["server-side-fallback-2026-07-01"] and kw["fallbacks"] == "default"
    assert kw["system"][0]["cache_control"] == {"type": "ephemeral"} and "유키" in kw["system"][0]["text"]
    assert kw["output_config"]["effort"] == "low"
    assert kw["output_config"]["format"]["schema"]["required"] == ["1", "2"]
    assert "temperature" not in kw and "thinking" not in kw
    s = tr.snapshot()
    assert s["requests"] == 3 and s["prompt_tokens"] == 300 and s["cache_read_tokens"] == 150
    assert s["cost_usd"] > 0


def test_claude_haiku_has_no_fallback_beta():
    fake = FakeAnthropic()
    claude(fake, model="claude-haiku-5-5").translate(make_lines(1), [])
    assert "fallbacks" not in fake.calls[0] and "betas" not in fake.calls[0]


def test_claude_refusal_flags_batch_without_splitting():
    fake = FakeAnthropic(refuse_ids={3})
    lines = make_lines(4)
    lines[2].ko = "이전 번역"
    tr = claude(fake, batch_size=2)
    tr.translate(lines, [])
    assert lines[0].ko == "ko1" and "refused" not in lines[0].flags
    assert "refused" in lines[2].flags and lines[2].ko == "이전 번역"
    assert tr.refused_ids == {3, 4} and tr.stats["splits"] == 0 and len(fake.calls) == 2


def test_claude_bad_json_is_retried():
    fake = FakeAnthropic(garbage_first=True)
    lines = make_lines(2)
    tr = claude(fake)
    tr.translate(lines, [])
    assert [ln.ko for ln in lines] == ["ko1", "ko2"] and tr.stats["retries"] == 1


def test_claude_auth_error_stops_at_once():
    import anthropic

    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    err = anthropic.AuthenticationError("invalid x-api-key", response=httpx.Response(401, request=req), body=None)
    fake = FakeAnthropic(error=err)
    with pytest.raises(AuthError) as ei:
        claude(fake, batch_size=1).translate(make_lines(3), [])
    assert len(fake.calls) == 1 and "sk-" not in str(ei.value)


# ---------------------------------------------------------------------- Gemini


class FakeGemini:
    def __init__(self, block=False, finish="STOP", error=None):
        self.calls = []
        self.block, self.finish, self.error = block, finish, error
        self.models = NS(generate_content=self._gen)

    def _gen(self, model, contents, config):
        self.calls.append((model, contents, config))
        if self.error:
            raise self.error
        ids = config.response_json_schema["required"]
        usage = NS(prompt_token_count=80, candidates_token_count=5 * len(ids), thoughts_token_count=0,
                   cached_content_token_count=0)
        fb = NS(block_reason="PROHIBITED_CONTENT") if self.block else None
        return NS(usage_metadata=usage, prompt_feedback=fb, candidates=[NS(finish_reason=NS(name=self.finish))],
                  text=json.dumps({i: f"ko{i}" for i in ids}))


def gemini(fake, **opt):
    from jamak.core.translate.gemini_api import GeminiTranslator

    return GeminiTranslator(TranslateOptions(mode="gemini", **opt), "k", "gemini-test", client=fake)


def test_gemini_json_schema_and_system_prompt():
    fake = FakeGemini()
    lines = make_lines(3)
    gemini(fake, batch_size=3).translate(lines, [GlossaryEntry(ja="東京", ko="도쿄")])
    assert [ln.ko for ln in lines] == ["ko1", "ko2", "ko3"]
    cfg = fake.calls[0][2]
    assert cfg.response_mime_type == "application/json" and "도쿄" in cfg.system_instruction


def test_gemini_block_is_refusal():
    tr = gemini(FakeGemini(block=True))
    lines = make_lines(2)
    tr.translate(lines, [])
    assert all("refused" in ln.flags for ln in lines)
    tr = gemini(FakeGemini(finish="SAFETY"))
    tr.translate(make_lines(1), [])
    assert tr.refused_ids == {1}


def test_gemini_bad_key_is_auth_error():
    from google.genai import errors

    err = errors.ClientError(400, {"error": {"code": 400, "message": "API key not valid.", "status": "INVALID_ARGUMENT"}})
    with pytest.raises(AuthError):
        gemini(FakeGemini(error=err)).translate(make_lines(1), [])


# ---------------------------------------------------------------------- local fallback for refusals


def test_refused_lines_go_to_local_model(jamak_home, monkeypatch):
    """A cloud refusal with refusal='local' retranslates those lines with the local engine."""
    from contextlib import contextmanager

    from jamak.config import PipelineOptions
    from jamak.core import pipeline
    from jamak.core.cache import Job
    from jamak.core.translate import factory

    class Eng:
        def __init__(self, t):
            self.t, self.refused_ids = t, set()

        def translate(self, lines, glossary, progress=None, cancel=None, only_ids=None):
            for ln in lines:
                if only_ids is not None and ln.id not in only_ids:
                    continue
                if self.t.mode == "anthropic" and ln.id == 2:
                    ln.flags.append("refused")
                    self.refused_ids.add(2)
                else:
                    ln.ko = f"{self.t.mode}{ln.id}"
                    ln.flags = [f for f in ln.flags if f != "refused"]

        def snapshot(self):
            return {"requests": 1}

    @contextmanager
    def fake_open(t, **kw):
        yield Eng(t)

    monkeypatch.setattr(factory, "open_engine", fake_open)
    monkeypatch.setattr(factory, "local_fallback_options", lambda t: TranslateOptions(mode="managed"))
    opts = PipelineOptions()
    opts.translate.mode, opts.translate.api_model = "anthropic", "claude-opus-5-5"
    video = jamak_home.parent / "v.mkv"
    video.parent.mkdir(parents=True, exist_ok=True)
    video.write_bytes(b"x")
    lines = make_lines(3)
    stats = pipeline.run_translation(lines, opts, Job(video))
    assert [ln.ko for ln in lines] == ["anthropic1", "managed2", "anthropic3"]
    assert "local_fallback" in lines[1].flags and stats["local_fallback"]["lines"] == 1
