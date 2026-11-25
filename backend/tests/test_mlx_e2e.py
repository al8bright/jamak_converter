"""Real mlx-whisper on Apple Silicon: `uv run pytest -m mlx -s` (CI runs it on the macOS runner).

Uses whisper-tiny (75 MB, downloaded once into tests/.data) and the synthetic Open JTalk dialogue, so
it checks the plumbing (word times, VAD spans, progress, cancel, encoder reuse), not accuracy.
"""

from __future__ import annotations

import re
import sys
import threading
import time
from pathlib import Path

import pytest

from jamak.config import SttOptions

pytestmark = [pytest.mark.mlx,
              pytest.mark.skipif(sys.platform != "darwin", reason="mlx-whisper runs on macOS (Apple Silicon) only")]

DATA = Path(__file__).parent / ".data"
REPO = "mlx-community/whisper-tiny"


@pytest.fixture(scope="module")
def tiny() -> str:
    from huggingface_hub import snapshot_download

    import jamak.installer as ins

    return snapshot_download(REPO, cache_dir=str(DATA / "hf"), allow_patterns=ins.HF_PATTERNS["mlx-whisper"])


@pytest.fixture(scope="module")
def speech():
    from tests.fixtures_gen import speech_wav_16k

    return speech_wav_16k(DATA)


def _run(model: str, wav: Path, vad: bool = True, **kw):
    from jamak.core.stt.mlx_whisper import MlxWhisperRecognizer

    rec = MlxWhisperRecognizer(SttOptions(model=model, vad=vad))
    t = time.time()
    tr = rec.transcribe(wav, "登場人物・用語：ユウキ。", **kw)
    return rec, tr, time.time() - t


def test_transcribes_with_word_times(tiny, speech):
    wav, truth = speech
    seen = []
    rec, tr, took = _run(tiny, wav, progress=lambda f, m: seen.append(f))
    text = "".join(s.text for s in tr.segments)
    print(f"\n[mlx] {took:.1f}s for {tr.duration:.1f}s of audio, {len(tr.segments)} segments, "
          f"encoder {rec.encoder_calls} calls / {rec.encoder_reused} reused")
    for s in tr.segments:
        print(f"  {s.start:6.2f}-{s.end:6.2f} lp {s.avg_logprob:.2f} cr {s.compression_ratio:.2f} "
              f"ns {s.no_speech_prob:.2f} {s.text}  {[(round(w.start, 2), w.text) for w in s.words][:6]}")

    assert tr.language == "ja" and tr.duration == pytest.approx(truth[-1]["end"] + 1.0, abs=0.2)
    assert tr.segments and len(text) >= 20 and re.search(r"[ぁ-んァ-ン一-龥]{2}", text), text
    for s in tr.segments:
        assert 0 <= s.start <= s.end <= tr.duration + 0.5
        assert s.words and s.avg_logprob <= 0 and s.compression_ratio > 0
        assert all(0 <= w.start <= w.end <= tr.duration + 0.5 and 0 <= w.prob <= 1 for w in s.words)
        assert [w.start for w in s.words] == sorted(w.start for w in s.words)
    # VAD spans go in as clip_timestamps, so times stay on the original timeline: speech starts ~1.0 s in
    assert abs(tr.segments[0].start - truth[0]["start"]) < 1.5
    assert tr.segments[-1].end > truth[-1]["start"]
    assert seen and seen == sorted(seen) and seen[-1] > 0.5
    # every window with text is encoded for decoding and again for word timestamps: the second is reused
    assert rec.encoder_reused >= 1 and rec.encoder_calls >= 2 * rec.encoder_reused


def test_reuse_does_not_change_the_result(tiny, speech, monkeypatch):
    from jamak.core.stt import mlx_whisper as mw

    wav, _truth = speech
    rec, with_reuse, t1 = _run(tiny, wav)
    monkeypatch.setattr(mw, "EncoderOnce", lambda enc: (_ for _ in ()).throw(RuntimeError("off for this test")))
    rec2, without, t2 = _run(tiny, wav)
    print(f"\n[mlx] with reuse {t1:.2f}s ({rec.encoder_reused} reused), without {t2:.2f}s")
    assert rec2.encoder_calls == 0 and rec.encoder_reused >= 1
    assert [s.text for s in with_reuse.segments] == [s.text for s in without.segments]
    a = [(round(w.start, 2), round(w.end, 2), w.text) for s in with_reuse.segments for w in s.words]
    b = [(round(w.start, 2), round(w.end, 2), w.text) for s in without.segments for w in s.words]
    assert a == b


def test_whole_file_without_vad(tiny, speech):
    wav, truth = speech
    _rec, tr, _ = _run(tiny, wav, vad=False)
    assert tr.segments and tr.segments[-1].end > truth[-1]["start"] - 2


def test_cancel_then_run_again(tiny, speech):
    from jamak.core.stt import mlx_whisper as mw

    wav, _truth = speech
    mt = mw.transcribe_module()
    real_tqdm = mt.tqdm
    stop = threading.Event()
    stop.set()
    with pytest.raises(mw.Cancelled):
        _run(tiny, wav, cancel=stop)
    assert mt.tqdm is real_tqdm
    model = mt.ModelHolder.model
    assert not isinstance(model.encoder, mw.EncoderOnce) and "encoder" in model  # the real encoder is back
    _rec, tr, _ = _run(tiny, wav)
    assert tr.segments


def test_folder_models_use_mlx_on_a_mac(tiny, monkeypatch):
    from jamak import hw
    from jamak.core.stt.base import create_recognizer

    monkeypatch.setattr(hw, "IS_MAC", True)  # conftest pins the Windows list for the other tests
    assert type(create_recognizer(SttOptions(model=tiny))).__name__ == "MlxWhisperRecognizer"
