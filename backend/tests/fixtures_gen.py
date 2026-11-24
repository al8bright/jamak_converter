"""Generate a synthetic Japanese test video with known dialogue (no copyrighted material).

Speech is synthesized offline with Open JTalk (pyopenjtalk). The MKV has two audio tracks:
track 0 = English-tagged silence, track 1 = Japanese dialogue over a quiet background tone,
so track selection, VAD and transcription are all exercised.
"""

from __future__ import annotations

import json
import math
import subprocess
import wave
from pathlib import Path

import numpy as np

DIALOGUE = [
    "おはよう、ユウキ。今日は早いね。",
    "うん、昨日の約束、覚えてる？",
    "もちろん。駅前のカフェで十時に待ち合わせでしょ。",
    "そうそう。遅れたら、ケーキおごってもらうからね。",
    "分かりました、先輩。では、また後ほど。",
    "ちょっと待って！財布を忘れちゃった。",
]
GAPS = [1.0, 0.8, 1.4, 0.9, 1.6, 1.0]  # silence before each line
SR = 48000


def synth(out_dir: Path) -> tuple[Path, Path]:
    import pyopenjtalk

    out_dir.mkdir(parents=True, exist_ok=True)
    wav_path = out_dir / "dialogue.wav"
    mkv_path = out_dir / "dialogue.mkv"
    truth_path = out_dir / "dialogue.truth.json"
    if mkv_path.exists() and truth_path.exists():
        return mkv_path, truth_path

    pieces: list[np.ndarray] = []
    truth = []
    t = 0.0
    for text, gap in zip(DIALOGUE, GAPS):
        pieces.append(np.zeros(int(gap * SR)))
        t += gap
        wav, sr = pyopenjtalk.tts(text)
        assert sr == SR, sr
        wav = wav.astype(np.float64) / 32768.0 * 0.8
        pieces.append(wav)
        dur = len(wav) / SR
        truth.append({"start": round(t, 3), "end": round(t + dur, 3), "ja": text})
        t += dur
    pieces.append(np.zeros(int(1.0 * SR)))
    speech = np.concatenate(pieces)
    n = len(speech)
    tt = np.arange(n) / SR
    bgm = sum(np.sin(2 * math.pi * f * tt) for f in (220.0, 277.18, 329.63)) * 0.01  # soft A-major chord
    mix = np.clip(speech + bgm, -1.0, 1.0)
    with wave.open(str(wav_path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((mix * 32767).astype("<i2").tobytes())

    duration = n / SR
    cmd = [
        "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", f"color=c=black:s=320x240:r=10:d={duration:.3f}",
        "-f", "lavfi", "-i", f"anullsrc=r=48000:cl=stereo:d={duration:.3f}",
        "-i", str(wav_path),
        "-map", "0:v", "-map", "1:a", "-map", "2:a",
        "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-b:a", "128k",
        "-metadata:s:a:0", "language=eng", "-metadata:s:a:1", "language=jpn",
        "-disposition:a:0", "default", "-disposition:a:1", "0",
        str(mkv_path),
    ]
    subprocess.run(cmd, check=True)
    truth_path.write_text(json.dumps(truth, ensure_ascii=False, indent=1), encoding="utf-8")
    return mkv_path, truth_path


if __name__ == "__main__":
    import sys

    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / ".data"
    print(synth(target))
