"""Audio track probing and extraction with ffmpeg/ffprobe."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

SAMPLE_RATE = 16000
_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0  # CREATE_NO_WINDOW


class FfmpegError(RuntimeError):
    pass


def ffmpeg_exe() -> str:
    exe = shutil.which("ffmpeg")
    if not exe:
        raise FfmpegError("ffmpeg not found on PATH")
    return exe


def ffprobe_exe() -> str:
    exe = shutil.which("ffprobe")
    if not exe:
        raise FfmpegError("ffprobe not found on PATH")
    return exe


@dataclass
class AudioTrack:
    index: int  # position among audio streams (ffmpeg "a:<index>")
    stream_index: int  # absolute stream index in the container
    codec: str
    language: str
    title: str
    channels: int
    default: bool


@dataclass
class MediaInfo:
    duration: float
    tracks: list[AudioTrack]


def probe(video: Path) -> MediaInfo:
    cmd = [
        ffprobe_exe(), "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", "-select_streams", "a", str(video),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", creationflags=_NO_WINDOW)
    if res.returncode != 0:
        raise FfmpegError(f"ffprobe failed: {res.stderr.strip()}")
    data = json.loads(res.stdout or "{}")
    tracks = []
    for i, s in enumerate(data.get("streams", [])):
        tags = {k.lower(): v for k, v in (s.get("tags") or {}).items()}
        tracks.append(
            AudioTrack(
                index=i,
                stream_index=int(s.get("index", i)),
                codec=s.get("codec_name", "?"),
                language=(tags.get("language") or "").lower(),
                title=tags.get("title", ""),
                channels=int(s.get("channels") or 0),
                default=bool((s.get("disposition") or {}).get("default")),
            )
        )
    duration = float((data.get("format") or {}).get("duration") or 0.0)
    return MediaInfo(duration=duration, tracks=tracks)


_JA_CODES = {"jpn", "ja", "jp", "japanese"}


def pick_track(info: MediaInfo, requested: int | None = None) -> AudioTrack:
    if not info.tracks:
        raise FfmpegError("no audio track in file")
    if requested is not None:
        for t in info.tracks:
            if t.index == requested:
                return t
        raise FfmpegError(f"audio track {requested} not found ({len(info.tracks)} tracks)")
    for t in info.tracks:
        if t.language in _JA_CODES or "japan" in t.title.lower() or "日本語" in t.title:
            return t
    for t in info.tracks:
        if t.default:
            return t
    return info.tracks[0]


def extract_wav(video: Path, out: Path, track: AudioTrack) -> None:
    """Decode one audio track to 16 kHz mono 16-bit PCM WAV."""
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp.wav")
    cmd = [
        ffmpeg_exe(), "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(video), "-map", f"0:a:{track.index}", "-vn", "-sn", "-dn",
        "-ac", "1", "-ar", str(SAMPLE_RATE), "-c:a", "pcm_s16le", str(tmp),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                         creationflags=_NO_WINDOW)
    if res.returncode != 0:
        tmp.unlink(missing_ok=True)
        raise FfmpegError(f"ffmpeg failed: {res.stderr.strip()[-2000:]}")
    tmp.replace(out)


def load_wav(path: Path):
    """Read a 16 kHz mono PCM16 WAV as float32 numpy array."""
    import wave

    import numpy as np

    with wave.open(str(path), "rb") as w:
        if w.getframerate() != SAMPLE_RATE or w.getnchannels() != 1 or w.getsampwidth() != 2:
            raise ValueError(f"{path} is not 16 kHz mono PCM16")
        data = w.readframes(w.getnframes())
    return np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
