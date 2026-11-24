"""Drop Whisper hallucinations before segmentation."""

from __future__ import annotations

import re

from jamak.config import FilterOptions
from jamak.core.types import SttSegment

_PUNCT = re.compile(r"[\s、。！？!?…・「」『』（）()\[\]【】〜~ー-]+")


def _norm(text: str) -> str:
    return _PUNCT.sub("", text)


def filter_segments(segments: list[SttSegment], opts: FilterOptions) -> tuple[list[SttSegment], list[dict]]:
    """Return kept segments and a log of dropped ones (for debugging / the UI)."""
    if not opts.enabled:
        return segments, []
    kept: list[SttSegment] = []
    dropped: list[dict] = []
    phrases = [_norm(p) for p in opts.phrases if p.strip()]

    def drop(seg: SttSegment, reason: str) -> None:
        dropped.append({"start": seg.start, "end": seg.end, "text": seg.text, "reason": reason})

    run_text = None
    run_len = 0
    for seg in segments:
        norm = _norm(seg.text)
        if not norm:
            drop(seg, "empty")
            continue
        if seg.no_speech_prob > opts.no_speech_prob and seg.avg_logprob < opts.logprob:
            drop(seg, "no_speech")
            continue
        if seg.compression_ratio > opts.compression_ratio:
            drop(seg, "repetitive")
            continue
        if any(p and p in norm and len(norm) <= len(p) + 6 for p in phrases):
            drop(seg, "known_phrase")
            continue
        if norm == run_text:
            run_len += 1
            if run_len > opts.max_repeats:
                drop(seg, "repeat")
                continue
        else:
            run_text, run_len = norm, 1
        kept.append(seg)
    return kept, dropped
