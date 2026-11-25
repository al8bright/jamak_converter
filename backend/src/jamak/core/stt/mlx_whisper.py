"""Whisper on the Apple GPU (Metal) through mlx-whisper, for Macs with Apple Silicon.

Same models as on Windows (large-v3, large-v3-turbo, kotoba-whisper v2.0) in MLX conversions, so the
segmenter, filters and caches work unchanged: mlx-whisper returns openai-whisper's result format
(segments with words, avg_logprob, no_speech_prob, compression_ratio).

Differences from faster-whisper:
- no beam search (mlx-whisper decodes greedily, with the usual temperature fallback),
- no built-in VAD: the same Silero VAD spans are passed as `clip_timestamps`, so silence is skipped
  and timestamps stay on the original timeline.
Progress and cancel go through the tqdm bar mlx-whisper updates after every 30-second window.
"""

from __future__ import annotations

import threading
from pathlib import Path
from types import SimpleNamespace

from jamak.config import SttOptions
from jamak.core.stt.base import ProgressFn
from jamak.core.types import SttSegment, Transcript, Word

WINDOW = 30.0  # Whisper's input length; VAD spans are merged up to this


class Cancelled(Exception):
    pass


def model_dir(model: str) -> Path:
    """Local folder of a downloaded MLX model (never downloads during a job)."""
    from jamak import models

    if Path(model).is_dir():
        return Path(model)
    s = models.spec(model)
    if s is None or s.engine != "mlx-whisper":
        raise models.ModelMissing(f"알 수 없는 Whisper 모델입니다: {model}")
    dirs = models.local_dirs(s)
    if dirs is None:
        raise models.ModelMissing(f"{s.name}을(를) 아직 받지 않았습니다. 설정 › 모델 관리에서 받으세요")
    return dirs[0]


def clip_timestamps(samples, opts: SttOptions) -> list[float]:
    """Speech spans as mlx-whisper's flat [start, end, start, end, ...] list ([] = whole file)."""
    if not opts.vad:
        return []
    from jamak.core.stt.qwen_asr import speech_chunks

    return [t for a, b in speech_chunks(samples, opts.model_copy(update={"max_chunk": WINDOW})) for t in (a, b)]


def to_transcript(result: dict, duration: float, model: str) -> Transcript:
    segments = []
    for s in result.get("segments") or []:
        text = (s.get("text") or "").strip()
        if not text:
            continue
        segments.append(SttSegment(
            start=float(s["start"]), end=float(s["end"]), text=text,
            words=[Word(start=float(w["start"]), end=float(w["end"]), text=w["word"],
                        prob=float(w.get("probability", 1.0))) for w in s.get("words") or []],
            avg_logprob=float(s.get("avg_logprob", 0.0)), no_speech_prob=float(s.get("no_speech_prob", 0.0)),
            compression_ratio=float(s.get("compression_ratio", 1.0)),
        ))
    return Transcript(language=result.get("language") or "ja", duration=duration, model=model, segments=segments)


class _Bar:
    """Stands in for tqdm.tqdm inside mlx_whisper.transcribe: reports progress, stops on cancel."""

    def __init__(self, progress: ProgressFn | None, cancel: threading.Event | None):
        self.progress, self.cancel, self.n, self.total = progress, cancel, 0, 1

    def __call__(self, total=None, **_kw):
        self.total = max(int(total or 1), 1)
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def update(self, n: int) -> None:
        if self.cancel is not None and self.cancel.is_set():
            raise Cancelled()
        self.n += n
        if self.progress:
            self.progress(min(self.n / self.total, 0.999), "")


class MlxWhisperRecognizer:
    def __init__(self, opts: SttOptions):
        self.opts = opts

    def transcribe(
        self,
        audio: Path,
        initial_prompt: str | None,
        progress: ProgressFn | None = None,
        cancel: threading.Event | None = None,
    ) -> Transcript:
        import mlx_whisper.transcribe as mt

        from jamak.core.audio import load_wav

        path = model_dir(self.opts.model)
        samples = load_wav(audio)
        duration = len(samples) / 16000
        clips = clip_timestamps(samples, self.opts)
        if self.opts.vad and not clips:
            return Transcript(language="ja", duration=duration, model=self.opts.model, segments=[])
        if progress:
            progress(0.0, f"말소리 구간 {len(clips) // 2}개" if clips else "")

        real_tqdm = mt.tqdm
        mt.tqdm = SimpleNamespace(tqdm=_Bar(progress, cancel))  # one job at a time runs STT
        try:
            result = mt.transcribe(
                samples, path_or_hf_repo=str(path), verbose=False, language="ja", task="transcribe",
                word_timestamps=True, condition_on_previous_text=False, initial_prompt=initial_prompt or None,
                clip_timestamps=clips or "0",
            )
        finally:
            mt.tqdm = real_tqdm
        return to_transcript(result, duration, self.opts.model)

    def close(self) -> None:
        try:
            import mlx.core as mx

            mx.clear_cache()
        except (ImportError, AttributeError):
            pass
