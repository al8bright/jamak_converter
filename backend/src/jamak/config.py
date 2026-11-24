"""Paths and user-tunable options.

Everything that affects output quality (models, segmentation rules, filter thresholds)
lives here as an option so it can be tuned later (PLAN.md Phase 6) without code changes.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field


def _default_home() -> Path:
    env = os.environ.get("JAMAK_HOME")
    if env:
        return Path(env)
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "jamak_converter"
    return Path.home() / ".local" / "share" / "jamak_converter"


class Paths:
    """Runtime folders. Resolved lazily so tests can override JAMAK_HOME."""

    @property
    def home(self) -> Path:
        return _default_home()

    @property
    def bin(self) -> Path:
        return self.home / "bin"

    @property
    def llama_dir(self) -> Path:
        return self.bin / "llama-server"

    @property
    def llama_server_exe(self) -> Path:
        name = "llama-server.exe" if sys.platform == "win32" else "llama-server"
        return self.llama_dir / name

    @property
    def models(self) -> Path:
        return self.home / "models"

    @property
    def whisper_models(self) -> Path:
        return self.models / "whisper"

    @property
    def jobs(self) -> Path:
        return self.home / "jobs"

    @property
    def glossaries(self) -> Path:
        return self.home / "glossaries"

    def ensure(self) -> None:
        for p in (self.models, self.whisper_models, self.jobs, self.glossaries):
            p.mkdir(parents=True, exist_ok=True)


paths = Paths()

DEFAULT_LLM_FILE = "gemma-4-12b-it-qat-q4_0.gguf"


def default_llm_path() -> Path | None:
    """Provisional default translation model; any *.gguf in the models dir otherwise."""
    preferred = paths.models / DEFAULT_LLM_FILE
    if preferred.exists():
        return preferred
    if paths.models.exists():
        ggufs = sorted(p for p in paths.models.glob("*.gguf") if not p.name.startswith("mmproj"))
        if ggufs:
            return ggufs[0]
    return None


# --------------------------------------------------------------------------- options


class GlossaryEntry(BaseModel):
    ja: str
    ko: str
    note: str = ""


class AudioOptions(BaseModel):
    track: int | None = None  # audio stream index among audio streams; None = auto (prefer Japanese)


class SeparateOptions(BaseModel):
    enabled: bool = False
    model: str = "htdemucs"
    segment_seconds: float = 7.8  # htdemucs max segment length


class SttOptions(BaseModel):
    engine: Literal["faster-whisper"] = "faster-whisper"
    model: str = "large-v3"
    device: Literal["cuda", "cpu", "auto"] = "auto"
    compute_type: str = "auto"  # float16 on cuda, int8 on cpu
    beam_size: int = 5
    vad: bool = True
    vad_min_silence_ms: int = 500
    vad_threshold: float = 0.5


class FilterOptions(BaseModel):
    enabled: bool = True
    no_speech_prob: float = 0.6  # drop segment if no_speech_prob above this ...
    logprob: float = -1.0  # ... and avg_logprob below this
    compression_ratio: float = 2.4  # drop highly repetitive segments
    max_repeats: int = 2  # identical consecutive lines kept at most this many times
    phrases: list[str] = Field(
        default_factory=lambda: [
            "ご視聴ありがとうございました",
            "チャンネル登録",
            "最後までご視聴",
            "字幕は視聴者によって作成されました",
        ]
    )


class SegmentOptions(BaseModel):
    max_chars: int = 32  # Japanese characters per subtitle (about two lines)
    max_duration: float = 7.0
    min_duration: float = 1.0
    pause_split: float = 0.4  # gap that makes a preferred split point
    hard_gap: float = 1.2  # gap that always splits
    end_padding: float = 0.25  # keep text a little after the last word, if there is room
    min_gap: float = 0.05  # minimum gap between consecutive subtitles


class TranslateOptions(BaseModel):
    mode: Literal["managed", "external"] = "managed"
    model_path: str | None = None  # managed: GGUF path (None = default_llm_path())
    base_url: str | None = None  # external: OpenAI-compatible base URL, e.g. http://127.0.0.1:11434/v1
    model_name: str = "local"  # external: model name sent in the request
    api_key: str | None = None
    batch_size: int = 25
    context_lines: int = 5
    temperature: float = 0.2
    parallel: int = 1  # >1 translates batches concurrently (context = source text only)
    ctx_size: int = 8192  # per slot
    gpu_layers: int = 99
    request_timeout: float = 300.0
    max_retries: int = 1


class OutputOptions(BaseModel):
    formats: list[Literal["srt", "ass", "vtt"]] = Field(default_factory=lambda: ["srt"])
    include_ja: bool = False  # also write <name>.ja.<fmt>
    dual_ass: bool = False  # also write <name>.ja-ko.ass
    out_dir: str | None = None  # default: next to the video
    ko_max_line_chars: int = 20  # wrap Korean lines longer than this


class PipelineOptions(BaseModel):
    audio: AudioOptions = Field(default_factory=AudioOptions)
    separate: SeparateOptions = Field(default_factory=SeparateOptions)
    stt: SttOptions = Field(default_factory=SttOptions)
    filters: FilterOptions = Field(default_factory=FilterOptions)
    segment: SegmentOptions = Field(default_factory=SegmentOptions)
    translate: TranslateOptions = Field(default_factory=TranslateOptions)
    output: OutputOptions = Field(default_factory=OutputOptions)
    glossary: list[GlossaryEntry] = Field(default_factory=list)
