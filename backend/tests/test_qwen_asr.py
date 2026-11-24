"""Qwen3-ASR glue that runs in the backend (the worker itself needs the PyTorch runtime)."""

from __future__ import annotations

from jamak.config import PipelineOptions, SttOptions
from jamak.core.pipeline import stt_identity
from jamak.core.stt import qwen_asr as q
from jamak.core.stt.base import engine_for


def raw(text, language="Japanese", words=None):
    return {"start": 1.0, "end": 3.0, "text": text, "language": language, "words": words or []}


def test_drops_fillers_and_chinese():
    segs, dropped = q.to_segments([
        raw("テスト。"),
        raw("这个是中文", language="Chinese"),
        raw("しんのすけ、来たぞ", language="Chinese"),  # has kana: keep it
        raw(""),
        raw("おはよう。"),
    ])
    assert [s.text for s in segs] == ["しんのすけ、来たぞ", "おはよう。"]
    assert [d["reason"] for d in dropped] == ["filler", "language:chinese", "empty"]


def test_punctuation_goes_back_on_words():
    words = [{"start": 1.0, "end": 1.2, "text": "バカ"}, {"start": 1.3, "end": 1.5, "text": "笑い"},
             {"start": 1.5, "end": 1.6, "text": "すぎ"}]
    segs, _ = q.to_segments([raw("バカ。笑いすぎ！", words=words)])
    assert [w.text for w in segs[0].words] == ["バカ。", "笑い", "すぎ！"]


def test_chunks_without_vad_are_capped():
    import numpy as np

    samples = np.zeros(16000 * 70, dtype=np.float32)
    chunks = q.speech_chunks(samples, SttOptions(model="qwen3-asr-1.7b", vad=False, max_chunk=25))
    assert chunks[0] == (0.0, 25.0) and chunks[-1][1] == 70.0 and len(chunks) == 3


def test_engine_by_model():
    assert engine_for(SttOptions(model="qwen3-asr-1.7b")) == "qwen-asr"
    assert engine_for(SttOptions(model="large-v3")) == "faster-whisper"
    assert engine_for(SttOptions(model="C:/my/ct2-model")) == "faster-whisper"


def test_whisper_cache_identity_unchanged():
    """Whisper transcripts cached by older versions must stay valid."""
    d = stt_identity(PipelineOptions())
    assert d["engine"] == "faster-whisper" and "batch_size" not in d and "max_chunk" not in d
    assert "batch_size" in stt_identity(PipelineOptions(stt=SttOptions(model="qwen3-asr-1.7b")))


def test_batch_shrinks_with_free_vram():
    assert q.batch_for_vram(8, free_gb=16) == 8
    assert q.batch_for_vram(8, free_gb=7.5) == 5
    assert q.batch_for_vram(8, free_gb=5) == 1
