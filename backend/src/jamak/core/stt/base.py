from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from jamak.config import SttOptions
from jamak.core.types import Transcript

ProgressFn = Callable[[float, str], None]  # (fraction 0..1, message)


class SpeechRecognizer(Protocol):
    def transcribe(
        self,
        audio: Path,
        initial_prompt: str | None,
        progress: ProgressFn | None = None,
        cancel: threading.Event | None = None,
    ) -> Transcript: ...

    def close(self) -> None:
        """Release the model (GPU memory)."""
        ...


def engine_for(opts: SttOptions) -> str:
    from jamak import models

    s = models.spec(opts.model)
    return s.engine if s and s.engine else "faster-whisper"  # unknown names: a CTranslate2 model folder


def create_recognizer(opts: SttOptions) -> SpeechRecognizer:
    engine = engine_for(opts)
    if engine == "faster-whisper":
        from jamak.core.stt.faster_whisper import FasterWhisperRecognizer

        return FasterWhisperRecognizer(opts)
    if engine == "qwen-asr":
        from jamak.core.stt.qwen_asr import QwenAsrRecognizer

        return QwenAsrRecognizer(opts)
    raise ValueError(f"unknown STT engine: {engine}")
