"""Turn word-timestamped Whisper output into subtitle-sized lines.

1. Split into chunks at hard boundaries: long pauses, sentence-final punctuation,
   and Whisper segment ends followed by a pause.
2. Recursively split chunks that are too long (chars or duration) at the best point:
   after a comma, at a pause, or at a Whisper segment boundary, preferring the middle.
3. Merge very short chunks into a neighbour when they are close in time.
4. Fix timing: padding, minimum duration, no overlaps.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from jamak.config import SegmentOptions
from jamak.core.types import Line, SttSegment

_SENTENCE_END = tuple("。！？!?")
_COMMA_END = tuple("、，,…‥")


@dataclass
class _W:
    start: float
    end: float
    text: str  # raw text (may carry a leading space for latin words)
    seg_end: bool = False


def _text(words: list[_W]) -> str:
    return re.sub(r"\s+", " ", "".join(w.text for w in words)).strip()


def _chars(words: list[_W]) -> int:
    return len(re.sub(r"\s+", "", "".join(w.text for w in words)))


def _flatten(segments: list[SttSegment]) -> list[_W]:
    out: list[_W] = []
    for seg in segments:
        words = [w for w in seg.words if w.text.strip()]
        if not words:
            if seg.text.strip():
                out.append(_W(seg.start, seg.end, seg.text, True))
            continue
        for i, w in enumerate(words):
            end = max(w.end, w.start)
            out.append(_W(w.start, end, w.text, i == len(words) - 1))
    out.sort(key=lambda w: w.start)
    return out


def _hard_chunks(words: list[_W], opts: SegmentOptions) -> list[list[_W]]:
    chunks: list[list[_W]] = []
    cur: list[_W] = []
    for i, w in enumerate(words):
        cur.append(w)
        nxt = words[i + 1] if i + 1 < len(words) else None
        if nxt is None:
            break
        gap = nxt.start - w.end
        t = w.text.strip()
        if (
            gap >= opts.hard_gap
            or t.endswith(_SENTENCE_END)
            or (w.seg_end and gap >= opts.pause_split)
        ):
            chunks.append(cur)
            cur = []
    if cur:
        chunks.append(cur)
    return chunks


def _too_long(chunk: list[_W], opts: SegmentOptions) -> bool:
    return _chars(chunk) > opts.max_chars or (chunk[-1].end - chunk[0].start) > opts.max_duration


def _split_point(chunk: list[_W], opts: SegmentOptions) -> int:
    """Index i: split into chunk[:i] and chunk[i:]. Score boundaries, prefer the middle."""
    total = _chars(chunk)
    best_i, best_score = len(chunk) // 2, float("-inf")
    acc = 0
    for i in range(1, len(chunk)):
        acc += len(chunk[i - 1].text.strip())
        prev, nxt = chunk[i - 1], chunk[i]
        gap = nxt.start - prev.end
        t = prev.text.strip()
        score = 0.0
        if t.endswith(_SENTENCE_END):
            score += 4
        elif t.endswith(_COMMA_END):
            score += 3
        if gap >= opts.pause_split:
            score += 2 + min(gap, 2.0)
        if prev.seg_end:
            score += 2
        balance = abs(acc / total - 0.5) if total else 0.5
        score -= balance * 6  # 0 at the middle, -3 at the edges
        if score > best_score:
            best_i, best_score = i, score
    return best_i


def _split_long(chunk: list[_W], opts: SegmentOptions) -> list[list[_W]]:
    if len(chunk) <= 1 or not _too_long(chunk, opts):
        return [chunk]
    i = _split_point(chunk, opts)
    return _split_long(chunk[:i], opts) + _split_long(chunk[i:], opts)


def _merge_short(chunks: list[list[_W]], opts: SegmentOptions) -> list[list[_W]]:
    out: list[list[_W]] = []
    for ch in chunks:
        dur = ch[-1].end - ch[0].start
        if out and (dur < opts.min_duration or _chars(ch) <= 2):
            prev = out[-1]
            gap = ch[0].start - prev[-1].end
            merged = prev + ch
            if gap < opts.pause_split and not _too_long(merged, opts):
                out[-1] = merged
                continue
        out.append(ch)
    # a short first chunk can merge forward
    if len(out) >= 2:
        first, second = out[0], out[1]
        if (first[-1].end - first[0].start) < opts.min_duration:
            gap = second[0].start - first[-1].end
            if gap < opts.pause_split and not _too_long(first + second, opts):
                out[0:2] = [first + second]
    return out


def _fix_timing(lines: list[Line], opts: SegmentOptions) -> None:
    for i, ln in enumerate(lines):
        limit = lines[i + 1].start - opts.min_gap if i + 1 < len(lines) else float("inf")
        end = max(ln.end + opts.end_padding, ln.start + opts.min_duration)
        ln.end = round(max(min(end, limit), ln.start + 0.1), 3)
        ln.start = round(ln.start, 3)


def segment(segments: list[SttSegment], opts: SegmentOptions) -> list[Line]:
    words = _flatten(segments)
    if not words:
        return []
    chunks: list[list[_W]] = []
    for ch in _hard_chunks(words, opts):
        chunks.extend(_split_long(ch, opts))
    chunks = _merge_short(chunks, opts)
    lines = [
        Line(id=i + 1, start=ch[0].start, end=ch[-1].end, ja=_text(ch))
        for i, ch in enumerate(chunks)
        if _text(ch)
    ]
    for i, ln in enumerate(lines):
        ln.id = i + 1
    _fix_timing(lines, opts)
    return lines
