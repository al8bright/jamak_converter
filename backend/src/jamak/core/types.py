"""Data passed between pipeline stages (and stored as JSON in the job cache)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class Word(BaseModel):
    start: float
    end: float
    text: str
    prob: float = 1.0


class SttSegment(BaseModel):
    start: float
    end: float
    text: str
    words: list[Word] = Field(default_factory=list)
    avg_logprob: float = 0.0
    no_speech_prob: float = 0.0
    compression_ratio: float = 1.0


class Transcript(BaseModel):
    language: str = "ja"
    duration: float = 0.0
    model: str = ""
    segments: list[SttSegment] = Field(default_factory=list)


class Line(BaseModel):
    """One subtitle event."""

    id: int
    start: float
    end: float
    ja: str
    ko: str = ""
    locked: bool = False  # edited by the user: never overwritten by (re)translation
    flags: list[str] = Field(default_factory=list)  # e.g. "translate_failed"


class Lines(BaseModel):
    lines: list[Line] = Field(default_factory=list)


class Span(BaseModel):
    start: float
    end: float
