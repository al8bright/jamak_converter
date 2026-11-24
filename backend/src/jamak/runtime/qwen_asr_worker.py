"""Qwen3-ASR worker. Runs in the separate PyTorch environment (bin/torch-runtime), not in the backend.

    python qwen_asr_worker.py <request.json>

request: {"audio": wav path (16 kHz mono), "chunks": [[start, end], ...] in seconds,
          "model_dir", "aligner_dir", "language": "Japanese", "context": str, "batch_size": int, "out": path}

stdout: one JSON object per line: {"event": "loaded"} / {"event": "progress", "done", "total", "text"}
out:    {"segments": [{"start", "end", "text", "language", "words": [{"start", "end", "text"}]}]}

Only the standard library, numpy, soundfile, torch and qwen_asr are used here: this file must not
import jamak (the backend's packages are not installed in this environment).
"""

from __future__ import annotations

import json
import sys
import warnings


def emit(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main(request_path: str) -> int:
    warnings.filterwarnings("ignore")
    with open(request_path, encoding="utf-8") as f:
        req = json.load(f)

    import numpy as np
    import soundfile as sf
    import torch
    from qwen_asr import Qwen3ASRModel

    audio, sr = sf.read(req["audio"], dtype="float32")
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    cuda = torch.cuda.is_available()
    dtype = torch.bfloat16 if cuda else torch.float32
    device = "cuda:0" if cuda else "cpu"
    bs = max(1, int(req.get("batch_size") or 8))
    model = Qwen3ASRModel.from_pretrained(
        req["model_dir"], dtype=dtype, device_map=device, max_inference_batch_size=bs, max_new_tokens=256,
        forced_aligner=req["aligner_dir"], forced_aligner_kwargs={"dtype": dtype, "device_map": device},
    )
    emit({"event": "loaded", "device": device})

    chunks = req["chunks"]
    out = []
    for i in range(0, len(chunks), bs):
        group = chunks[i:i + bs]
        pieces = [(np.ascontiguousarray(audio[int(a * sr):int(b * sr)]), sr) for a, b in group]
        res = model.transcribe(pieces, language=req.get("language") or None, context=req.get("context") or "",
                               return_time_stamps=True)
        for (a, b), r in zip(group, res, strict=True):
            items = list(r.time_stamps or [])
            out.append({
                "start": a, "end": b, "text": r.text or "", "language": r.language or "",
                "words": [{"start": round(a + float(u.start_time), 3), "end": round(a + float(u.end_time), 3),
                           "text": u.text} for u in items],
            })
        emit({"event": "progress", "done": min(i + bs, len(chunks)), "total": len(chunks),
              "text": (res[-1].text or "")[:40] if res else ""})

    with open(req["out"], "w", encoding="utf-8") as f:
        json.dump({"segments": out}, f, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
