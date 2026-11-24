from pathlib import Path

from jamak.config import OutputOptions, SegmentOptions, SttOptions
from jamak.core.cache import Job, fingerprint
from jamak.core.subtitle import export, wrap_ko
from jamak.core.types import Line


def test_wrap_ko_breaks_at_space_near_middle():
    s = "역 앞 카페에서 10시에 만나기로 했잖아"
    out = wrap_ko(s, 12)
    assert "\n" in out
    a, b = out.split("\n")
    assert abs(len(a) - len(b)) <= 6
    assert wrap_ko("짧은 문장", 20) == "짧은 문장"


def test_export_writes_srt_and_dual_ass(tmp_path: Path):
    video = tmp_path / "ep01.mkv"
    video.write_bytes(b"x")
    lines = [
        Line(id=1, start=1.0, end=2.5, ja="おはよう", ko="안녕"),
        Line(id=2, start=3.0, end=4.0, ja="うん", ko=""),
    ]
    out = export(lines, video, OutputOptions(formats=["srt", "vtt"], include_ja=True, dual_ass=True))
    names = sorted(p.name for p in out)
    assert names == ["ep01.ja-ko.ass", "ep01.ja.srt", "ep01.ja.vtt", "ep01.ko.srt", "ep01.ko.vtt"]
    srt = (tmp_path / "ep01.ko.srt").read_text(encoding="utf-8")
    assert "00:00:01,000 --> 00:00:02,500" in srt and "안녕" in srt
    assert srt.count("-->") == 1  # empty translation is skipped
    ass = (tmp_path / "ep01.ja-ko.ass").read_text(encoding="utf-8")
    assert "Style: KO" in ass and "Style: JA" in ass


def test_fingerprint_changes_with_options_and_upstream():
    a = fingerprint(SttOptions(), "up1")
    assert a == fingerprint(SttOptions(), "up1")
    assert a != fingerprint(SttOptions(model="large-v3-turbo"), "up1")
    assert a != fingerprint(SttOptions(), "up2")
    assert fingerprint(SegmentOptions()) != fingerprint(SegmentOptions(max_chars=20))


def test_job_key_is_content_based_and_freshness(tmp_path: Path, jamak_home):
    v1 = tmp_path / "a.mkv"
    v1.write_bytes(b"same content" * 1000)
    v2 = tmp_path / "renamed.mkv"
    v2.write_bytes(b"same content" * 1000)
    j1, j2 = Job(v1), Job(v2)
    assert j1.key == j2.key
    assert not j1.is_fresh("audio", "fp1", "audio.wav")
    j1.path("audio.wav").write_bytes(b"wav")
    j1.mark_done("audio", "fp1", 0.1)
    assert Job(v1).is_fresh("audio", "fp1", "audio.wav")
    assert not Job(v1).is_fresh("audio", "fp2", "audio.wav")
