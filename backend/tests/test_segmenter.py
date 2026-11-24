from jamak.config import FilterOptions, SegmentOptions
from jamak.core.filters import filter_segments
from jamak.core.segmenter import segment
from jamak.core.types import SttSegment, Word


def seg_from(chars: list[tuple[str, float, float]], **kw) -> SttSegment:
    words = [Word(start=s, end=e, text=t) for t, s, e in chars]
    return SttSegment(start=words[0].start, end=words[-1].end, text="".join(w.text for w in words),
                      words=words, **kw)


def even_words(text: str, start: float, per: float = 0.15) -> list[tuple[str, float, float]]:
    """Split text into 2-char 'words' spaced evenly; punctuation sticks to the previous word (as Whisper does)."""
    tokens: list[str] = []
    for i in range(0, len(text), 2):
        chunk = text[i:i + 2]
        while chunk and chunk[0] in "、。！？" and tokens:
            tokens[-1] += chunk[0]
            chunk = chunk[1:]
        if chunk:
            tokens.append(chunk)
    out, t = [], start
    for tok in tokens:
        out.append((tok, t, t + per * len(tok)))
        t += per * len(tok)
    return out


def test_splits_on_sentence_end_and_keeps_order():
    s = seg_from(even_words("おはよう。今日は早いね。", 1.0))
    lines = segment([s], SegmentOptions())
    # "おはよう。" alone is shorter than min_duration, so it merges with the next sentence
    assert [ln.ja for ln in lines] == ["おはよう。今日は早いね。"]
    assert all(a.end <= b.start for a, b in zip(lines, lines[1:]))


def test_long_chunk_is_split_under_max_chars():
    text = "駅前のカフェで十時に待ち合わせして、そのあと映画を見に行って、夜はみんなで焼肉を食べに行こうよ"
    s = seg_from(even_words(text, 0.0, per=0.1))
    opts = SegmentOptions(max_chars=20)
    lines = segment([s], opts)
    assert len(lines) >= 2
    assert all(len(ln.ja) <= 20 for ln in lines)
    assert "".join(ln.ja for ln in lines) == text


def test_prefers_comma_split():
    text = "昨日の約束なんだけど、やっぱり明日にしてもらってもいいかな"
    s = seg_from(even_words(text, 0.0, per=0.1))
    lines = segment([s], SegmentOptions(max_chars=20))
    assert lines[0].ja.endswith("、")


def test_hard_gap_splits_and_timing_is_sane():
    a = seg_from(even_words("うん", 0.0))
    b = seg_from(even_words("そうだね", 5.0))
    lines = segment([a, b], SegmentOptions())
    assert len(lines) == 2
    for ln in lines:
        assert ln.end - ln.start >= 1.0 - 1e-6
    assert lines[0].end <= lines[1].start


def test_short_fragment_merges_with_close_neighbour():
    a = seg_from(even_words("えっと", 0.0, per=0.1))  # 0.6 s
    b = seg_from(even_words("それでね", 0.65, per=0.1))
    lines = segment([a, b], SegmentOptions(pause_split=0.4))
    assert len(lines) == 1
    assert lines[0].ja == "えっとそれでね"


def test_ids_are_sequential():
    segs = [seg_from(even_words(t, i * 3.0)) for i, t in enumerate(["はい。", "いいえ。", "たぶん。"])]
    lines = segment(segs, SegmentOptions())
    assert [ln.id for ln in lines] == list(range(1, len(lines) + 1))


def test_filters_drop_hallucinations():
    good = seg_from(even_words("今日は晴れ", 0.0))
    phrase = seg_from(even_words("ご視聴ありがとうございました", 3.0))
    quiet = seg_from(even_words("あああ", 6.0), no_speech_prob=0.9, avg_logprob=-1.5)
    rep = [seg_from(even_words("はい", 8.0 + i)) for i in range(4)]
    kept, dropped = filter_segments([good, phrase, quiet, *rep], FilterOptions(max_repeats=2))
    reasons = sorted(d["reason"] for d in dropped)
    assert reasons == ["known_phrase", "no_speech", "repeat", "repeat"]
    assert len(kept) == 3


def test_song_ranges_mark_or_skip():
    from jamak.config import SongOptions
    from jamak.core.pipeline import apply_song_ranges
    from jamak.core.types import Line

    def lines():
        return [Line(id=1, start=10, end=12, ja="歌"), Line(id=2, start=89, end=93, ja="境界"),
                Line(id=3, start=100, end=102, ja="会話")]

    marked = apply_song_ranges(lines(), SongOptions(mode="translate", ranges=[(0, 90)]))
    assert [ln.flags for ln in marked] == [["song"], [], []]  # line 2 is only 25% inside
    skipped = apply_song_ranges(lines(), SongOptions(mode="skip", ranges=[(0, 90)]))
    assert [ln.ja for ln in skipped] == ["境界", "会話"] and [ln.id for ln in skipped] == [1, 2]


def test_cli_time_parsing():
    from jamak.cli import parse_range, parse_time

    assert parse_time("1:30") == 90 and parse_time("0:01:30.5") == 90.5 and parse_time("45") == 45
    assert parse_range("0:00-1:30") == (0.0, 90.0)
