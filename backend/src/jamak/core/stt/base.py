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


def create_recognizer(opts: SttOptions) -> SpeechRecognizer:
    if opts.engine == "faster-whisper":
        from jamak.core.stt.faster_whisper import FasterWhisperRecognizer

        return FasterWhisperRecognizer(opts)
    raise ValueError(f"unknown STT engine: {opts.engine}")
