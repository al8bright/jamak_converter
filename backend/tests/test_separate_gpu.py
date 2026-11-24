"""Demucs separation across several chunks (exercises the overlap/cross-fade path).

uv run pytest -m gpu -k separate -s
"""

import pytest

from jamak.config import SeparateOptions
from jamak.core import separate
from jamak.core.audio import load_wav, pick_track, probe

pytestmark = pytest.mark.gpu


def test_chunked_separation_keeps_length_and_speech(dialogue_video, tmp_path, monkeypatch):
    if not separate.available():
        pytest.skip("separation extra not installed")
    monkeypatch.setattr(separate, "CHUNK_SECONDS", 7.0)
    monkeypatch.setattr(separate, "OVERLAP_SECONDS", 0.5)
    video, _ = dialogue_video
    info = probe(video)
    track = pick_track(info)
    out = tmp_path / "vocals.wav"
    seen = []
    separate.separate_vocals(video, track.index, out, SeparateOptions(enabled=True), duration=info.duration,
                             progress=lambda f, m: seen.append(f))
    v = load_wav(out)
    assert abs(len(v) / 16000 - info.duration) < 0.1
    assert len(seen) >= 4  # one progress tick per chunk
    assert float(abs(v).max()) > 0.05  # speech survives
    # the background tone is removed: the 1 s of silence-plus-tone at the start is much quieter
    lead = float(abs(v[: 16000 // 2]).mean())
    speech = float(abs(v[int(1.2 * 16000): int(2.2 * 16000)]).mean())
    assert lead < speech * 0.2
