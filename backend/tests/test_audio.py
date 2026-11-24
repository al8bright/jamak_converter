import shutil

import pytest

from jamak.core.audio import extract_wav, load_wav, pick_track, probe

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")


def test_probe_picks_japanese_track_and_extracts(dialogue_video, tmp_path):
    video, _ = dialogue_video
    info = probe(video)
    assert len(info.tracks) == 2
    assert info.duration > 10
    track = pick_track(info)
    assert track.index == 1 and track.language == "jpn"
    out = tmp_path / "a.wav"
    extract_wav(video, out, track)
    samples = load_wav(out)
    assert abs(len(samples) / 16000 - info.duration) < 0.5
    assert float(abs(samples).max()) > 0.1  # the Japanese track has speech, not the silent one


def test_explicit_track(dialogue_video):
    video, _ = dialogue_video
    info = probe(video)
    assert pick_track(info, 0).language == "eng"
