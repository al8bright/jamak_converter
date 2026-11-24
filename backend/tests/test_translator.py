import json

import httpx

from jamak.config import GlossaryEntry, TranslateOptions
from jamak.core.translate.openai_compat import OpenAICompatTranslator
from jamak.core.types import Line


def make_lines(n: int) -> list[Line]:
    return [Line(id=i, start=i, end=i + 0.5, ja=f"日本語{i}") for i in range(1, n + 1)]


class FakeServer:
    """Fake /chat/completions: answers 'ko<id>' for every requested id, with optional faults."""

    def __init__(self, drop_id_when_batch_over: int | None = None, garbage_first: bool = False):
        self.calls: list[dict] = []
        self.drop_over = drop_id_when_batch_over
        self.garbage_first = garbage_first

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.calls.append(body)
        ids = body["response_format"]["json_schema"]["schema"]["required"]
        if self.garbage_first and len(self.calls) == 1:
            content = "not json"
        else:
            out = {i: f"ko{i}" for i in ids}
            if self.drop_over is not None and len(ids) > self.drop_over:
                out.pop(ids[-1])
            content = json.dumps(out)
        return httpx.Response(200, json={
            "choices": [{"message": {"content": content}}],
            "usage": {"completion_tokens": 3 * len(ids), "prompt_tokens": 10},
        })


def translator(fake: FakeServer, **opt) -> OpenAICompatTranslator:
    return OpenAICompatTranslator("http://fake/v1", TranslateOptions(**opt), transport=httpx.MockTransport(fake))


def test_fills_every_line_in_batches_with_context():
    fake = FakeServer()
    lines = make_lines(7)
    translator(fake, batch_size=3, context_lines=2).translate(lines, [GlossaryEntry(ja="ユウキ", ko="유키")])
    assert [ln.ko for ln in lines] == [f"ko{i}" for i in range(1, 8)]
    assert len(fake.calls) == 3
    second_user = fake.calls[1]["messages"][1]["content"]
    payload = json.loads(second_user[second_user.index("{"):])
    assert [c["id"] for c in payload["context"]] == [2, 3]
    assert payload["context"][0]["ko"] == "ko2"
    assert payload["glossary"][0]["ko"] == "유키"


def test_locked_lines_are_never_sent_or_changed():
    fake = FakeServer()
    lines = make_lines(4)
    lines[1].ko, lines[1].locked = "직접 고친 번역", True
    translator(fake, batch_size=10).translate(lines, [])
    assert lines[1].ko == "직접 고친 번역"
    sent = fake.calls[0]["response_format"]["json_schema"]["schema"]["required"]
    assert sent == ["1", "3", "4"]


def test_retry_then_split_on_bad_batches():
    fake = FakeServer(drop_id_when_batch_over=2)  # batches larger than 2 always miss an id
    lines = make_lines(4)
    t = translator(fake, batch_size=4, max_retries=1)
    t.translate(lines, [])
    assert [ln.ko for ln in lines] == ["ko1", "ko2", "ko3", "ko4"]
    assert t.stats["splits"] >= 1 and t.stats["retries"] >= 1
    assert t.stats["failed_lines"] == 0


def test_garbage_response_is_retried():
    fake = FakeServer(garbage_first=True)
    lines = make_lines(2)
    t = translator(fake, batch_size=5, max_retries=1)
    t.translate(lines, [])
    assert [ln.ko for ln in lines] == ["ko1", "ko2"]
    assert t.stats["retries"] == 1


def test_parallel_mode_uses_source_only_context():
    fake = FakeServer()
    lines = make_lines(9)
    translator(fake, batch_size=3, parallel=3, context_lines=2).translate(lines, [])
    assert [ln.ko for ln in lines] == [f"ko{i}" for i in range(1, 10)]
    for call in fake.calls:
        user = call["messages"][1]["content"]
        payload = json.loads(user[user.index("{"):])
        for c in payload.get("context", []):
            assert "ko" not in c


def test_only_ids_limits_work():
    fake = FakeServer()
    lines = make_lines(5)
    translator(fake).translate(lines, [], only_ids={2, 4})
    assert [ln.ko for ln in lines] == ["", "ko2", "", "ko4", ""]
