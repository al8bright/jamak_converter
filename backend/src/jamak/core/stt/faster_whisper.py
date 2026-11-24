from __future__ import annotations

import gc
import os
import threading
from pathlib import Path

from jamak.config import SttOptions
from jamak.core import cuda
from jamak.core.stt.base import ProgressFn
from jamak.core.types import SttSegment, Transcript, Word


class Cancelled(Exception):
    pass


def model_path(model: str) -> str:
    """Local folder of a downloaded model (never downloads during a job: see 설정 › 모델 관리).

    A path to a CTranslate2 model folder also works (development / custom models).
    """
    from jamak import models
    from jamak.installer import fix_alignment_heads

    if Path(model).is_dir():
        return model
    s = models.spec(model)
    if s is None or s.engine != "faster-whisper":
        raise models.ModelMissing(f"알 수 없는 Whisper 모델입니다: {model}")
    dirs = models.local_dirs(s)
    if dirs is None:
        raise models.ModelMissing(f"{s.name}을(를) 아직 받지 않았습니다. 설정 › 모델 관리에서 받으세요")
    fix_alignment_heads(s.id, dirs[0])  # also for copies downloaded by older versions
    return str(dirs[0])


class FasterWhisperRecognizer:
    def __init__(self, opts: SttOptions):
        self.opts = opts
        self._model = None

    def _resolve_device(self) -> tuple[str, str]:
        device = self.opts.device
        if device == "auto":
            device = "cuda" if cuda.cuda_device_count() > 0 else "cpu"
        compute = self.opts.compute_type
        if compute == "auto":
            compute = "float16" if device == "cuda" else "int8"
        return device, compute

    def load(self):
        os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
        if self._model is None:
            cuda.setup()
            from faster_whisper import WhisperModel

            device, compute = self._resolve_device()
            self._model = WhisperModel(model_path(self.opts.model), device=device, compute_type=compute)
            self.device, self.compute_type = device, compute
        return self._model

    def transcribe(
        self,
        audio: Path,
        initial_prompt: str | None,
        progress: ProgressFn | None = None,
        cancel: threading.Event | None = None,
    ) -> Transcript:
        model = self.load()
        from jamak.core.audio import load_wav

        samples = load_wav(audio)
        vad_params = {
            "min_silence_duration_ms": self.opts.vad_min_silence_ms,
            "threshold": self.opts.vad_threshold,
        }
        seg_iter, info = model.transcribe(
            samples,
            language="ja",
            task="transcribe",
            beam_size=self.opts.beam_size,
            word_timestamps=True,
            condition_on_previous_text=False,
            vad_filter=self.opts.vad,
            vad_parameters=vad_params if self.opts.vad else None,
            initial_prompt=initial_prompt or None,
        )
        duration = float(info.duration or 0.0)
        out: list[SttSegment] = []
        for s in seg_iter:
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            out.append(
                SttSegment(
                    start=float(s.start),
                    end=float(s.end),
                    text=s.text.strip(),
                    words=[
                        Word(start=float(w.start), end=float(w.end), text=w.word, prob=float(w.probability))
                        for w in (s.words or [])
                    ],
                    avg_logprob=float(s.avg_logprob),
                    no_speech_prob=float(s.no_speech_prob),
                    compression_ratio=float(s.compression_ratio),
                )
            )
            if progress and duration > 0:
                progress(min(float(s.end) / duration, 0.999), s.text.strip()[:40])
        return Transcript(language=info.language or "ja", duration=duration, model=self.opts.model, segments=out)

    def close(self) -> None:
        if self._model is not None:
            del self._model
            self._model = None
            gc.collect()
