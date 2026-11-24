"""Vocal separation with Demucs (optional extra: `uv sync --extra separation`).

The track is streamed from ffmpeg in ~60 s chunks at the model's sample rate (44.1 kHz stereo),
so a 2-hour video never has to fit in memory. Chunks overlap by 1 s and are cross-faded.
The vocals are down-mixed and resampled to the 16 kHz mono WAV the STT stage expects.
"""

from __future__ import annotations

import gc
import subprocess
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path

import numpy as np

from jamak.config import SeparateOptions
from jamak.core import cuda
from jamak.core.audio import SAMPLE_RATE, ffmpeg_exe
from jamak.core.proc import CREATE_NO_WINDOW

ProgressFn = Callable[[float, str], None]

CHUNK_SECONDS = 60.0
OVERLAP_SECONDS = 1.0


class SeparationUnavailable(RuntimeError):
    pass


class Cancelled(Exception):
    pass


_available_lock = threading.Lock()
_available: bool | None = None


def available() -> bool:
    """True when the optional `separation` extra is installed. Imported once under a lock:
    two threads importing torch at the same time can see a half-initialised module."""
    global _available
    with _available_lock:
        if _available is None:
            try:
                import demucs  # noqa: F401
                import torch  # noqa: F401

                _available = True
            except ImportError:
                _available = False
        return _available


def separate_vocals(video: Path, track_index: int, out_wav: Path, opts: SeparateOptions,
                    duration: float = 0.0, progress: ProgressFn | None = None,
                    cancel: threading.Event | None = None) -> None:
    cuda.setup()
    try:
        import torch
        from demucs.apply import apply_model
        from demucs.pretrained import get_model
    except ImportError as e:
        raise SeparationUnavailable(
            "vocal separation needs the optional extra: cd backend && uv sync --extra separation"
        ) from e

    device = "cuda" if torch.cuda.is_available() else "cpu"
    if progress:
        progress(0.0, f"loading {opts.model} ({device})")
    model = get_model(opts.model)
    model.eval().to(device)
    sr = int(model.samplerate)
    channels = int(model.audio_channels)
    chunk = int(CHUNK_SECONDS * sr)
    over = int(OVERLAP_SECONDS * sr)
    fade_in = np.linspace(0.0, 1.0, over, dtype=np.float32)

    cmd = [ffmpeg_exe(), "-nostdin", "-hide_banner", "-loglevel", "error", "-i", str(video),
           "-map", f"0:a:{track_index}", "-vn", "-ac", str(channels), "-ar", str(sr), "-f", "f32le", "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            creationflags=CREATE_NO_WINDOW)
    tmp = tempfile.NamedTemporaryFile(suffix=".f32", delete=False, dir=out_wav.parent)
    tmp_path = Path(tmp.name)
    done_samples = 0
    try:
        tail: np.ndarray | None = None  # last `over` input samples, prepended to the next chunk
        held: np.ndarray | None = None  # last `over` output samples, cross-faded with the next chunk
        bytes_per_frame = 4 * channels
        while True:
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            raw = proc.stdout.read(chunk * bytes_per_frame)  # type: ignore[union-attr]
            if not raw:
                break
            new = np.frombuffer(raw[: len(raw) - len(raw) % bytes_per_frame], dtype=np.float32)
            new = new.reshape(-1, channels).T
            buf = new if tail is None else np.concatenate([tail, new], axis=1)
            vocals = _run(model, apply_model, torch, buf, device, opts)  # (n,) mono
            start = 0 if tail is None else over
            if held is not None:
                vocals[:over] = held * (1.0 - fade_in) + vocals[:over] * fade_in
                start = 0
            last = len(raw) < chunk * bytes_per_frame
            if last:
                tmp.write(vocals[start:].astype(np.float32).tobytes())
                held = None
            else:
                tmp.write(vocals[start:-over].astype(np.float32).tobytes())
                held = vocals[-over:].copy()
                tail = buf[:, -over:].copy()
            done_samples += new.shape[1]
            if progress and duration > 0:
                progress(min(done_samples / sr / duration, 0.999), f"{done_samples / sr / 60:.1f} min")
            if last:
                break
        if held is not None:
            tmp.write(held.astype(np.float32).tobytes())
        tmp.close()
        proc.wait()
        _to_16k_wav(tmp_path, sr, out_wav)
    finally:
        if proc.poll() is None:
            proc.kill()
        tmp.close()
        tmp_path.unlink(missing_ok=True)
        del model
        gc.collect()
        if device == "cuda":
            torch.cuda.empty_cache()


def _run(model, apply_model, torch, buf: np.ndarray, device: str, opts: SeparateOptions) -> np.ndarray:
    wav = torch.from_numpy(np.ascontiguousarray(buf)).to(device)
    ref = wav.mean(0)
    mean, std = ref.mean(), ref.std() + 1e-8
    with torch.inference_mode():
        out = apply_model(model, ((wav - mean) / std)[None], device=device, shifts=0, split=True,
                          overlap=0.25, progress=False, segment=opts.segment_seconds)
    vocals = out[0, list(model.sources).index("vocals")] * std + mean  # (channels, n)
    return vocals.mean(0).float().cpu().numpy()


def _to_16k_wav(raw_f32: Path, sr: int, out_wav: Path) -> None:
    tmp_out = out_wav.with_suffix(".tmp.wav")
    cmd = [ffmpeg_exe(), "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
           "-f", "f32le", "-ar", str(sr), "-ac", "1", "-i", str(raw_f32),
           "-ar", str(SAMPLE_RATE), "-ac", "1", "-c:a", "pcm_s16le", str(tmp_out)]
    res = subprocess.run(cmd, capture_output=True, text=True, creationflags=CREATE_NO_WINDOW)
    if res.returncode != 0:
        tmp_out.unlink(missing_ok=True)
        raise RuntimeError(f"ffmpeg resample failed: {res.stderr[-500:]}")
    tmp_out.replace(out_wav)
