"""Whisper on the Apple GPU (Metal) through mlx-whisper, for Macs with Apple Silicon.

Same models as on Windows (large-v3, large-v3-turbo, kotoba-whisper v2.0) in MLX conversions, so the
segmenter, filters and caches work unchanged: mlx-whisper returns openai-whisper's result format
(segments with words, avg_logprob, no_speech_prob, compression_ratio).

Differences from faster-whisper:
- no beam search (mlx-whisper decodes greedily, with the usual temperature fallback),
- no built-in VAD: the same Silero VAD spans are passed as `clip_timestamps`, so silence is skipped
  and timestamps stay on the original timeline.
Progress and cancel go through the tqdm bar mlx-whisper updates after every 30-second window.

mlx-whisper (like openai-whisper) runs the encoder again for word timestamps and for every temperature
retry of the same 30-second window. The encoder is most of the work, so its last result is kept and
reused while the input stays the same (`EncoderOnce`).
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from types import SimpleNamespace

from jamak.config import SttOptions
from jamak.core.stt.base import ProgressFn
from jamak.core.types import SttSegment, Transcript, Word

log = logging.getLogger(__name__)

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


def transcribe_module():
    """mlx_whisper's transcribe *module*. `import mlx_whisper.transcribe as x` gives the function instead,
    because the package's __init__ rebinds the name `transcribe` to it."""
    import importlib

    return importlib.import_module("mlx_whisper.transcribe")


def _same_input(a, b) -> bool:
    if a is b:
        return True
    if a.shape != b.shape or a.dtype != b.dtype:
        return False
    import mlx.core as mx

    return bool(mx.array_equal(a, b).item())


class EncoderOnce:
    """Wraps a Whisper encoder: the same mel window is encoded once, however often it is asked for."""

    def __init__(self, encoder):
        self.encoder = encoder
        self.calls = self.hits = 0
        self._mel = self._out = None

    def __call__(self, mel):
        self.calls += 1
        if self._mel is not None and _same_input(self._mel, mel):
            self.hits += 1
            return self._out
        out = self.encoder(mel)
        self._mel, self._out = mel, out
        return out


def _set_encoder(model, encoder) -> None:
    """mlx's nn.Module is a dict of its sub-modules; a plain callable has to go in as a normal attribute
    (which is looked up first), and the real encoder back in as a dict item."""
    if isinstance(model, dict):
        model.pop("encoder", None)
        model.__dict__.pop("encoder", None)
        if isinstance(encoder, dict):
            model["encoder"] = encoder
            return
    object.__setattr__(model, "encoder", encoder)


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
        self.encoder_calls = self.encoder_reused = 0  # of the last transcribe()

    def transcribe(
        self,
        audio: Path,
        initial_prompt: str | None,
        progress: ProgressFn | None = None,
        cancel: threading.Event | None = None,
    ) -> Transcript:
        from jamak.core.audio import load_wav

        mt = transcribe_module()

        path = model_dir(self.opts.model)
        samples = load_wav(audio)
        duration = len(samples) / 16000
        clips = clip_timestamps(samples, self.opts)
        if self.opts.vad and not clips:
            return Transcript(language="ja", duration=duration, model=self.opts.model, segments=[])
        if progress:
            progress(0.0, f"말소리 구간 {len(clips) // 2}개" if clips else "")

        model = once = None
        try:  # the model transcribe() is about to use (ModelHolder keeps one loaded model per path)
            import mlx.core as mx

            model = mt.ModelHolder.get_model(str(path), mx.float16)
            once = EncoderOnce(model.encoder)
            _set_encoder(model, once)
        except Exception as e:  # reuse is only a speed-up: never fail a job over it
            log.warning("encoder reuse is off: %s: %s", type(e).__name__, e)
            model = once = None

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
            if once is not None:
                _set_encoder(model, once.encoder)
                self.encoder_calls, self.encoder_reused = once.calls, once.hits
                log.info("encoder: %d calls, %d reused", once.calls, once.hits)
        return to_transcript(result, duration, self.opts.model)

    def close(self) -> None:
        try:
            import mlx.core as mx

            mx.clear_cache()
        except (ImportError, AttributeError):
            pass
