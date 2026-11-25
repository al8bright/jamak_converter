"""Everything the app can download: tools, runtimes, speech-recognition and translation models.

The installer (installer.py) downloads these on demand; nothing large ships in the setup exe.
`requires` lists what has to be installed with an item (CUDA only when an NVIDIA GPU is present).
VRAM figures were measured on a 105-minute film (PLAN.md Phase 4.5): beam 5 + word timestamps for Whisper,
batch 8 + forced aligner for Qwen3-ASR.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from jamak.config import paths

Kind = Literal["tool", "runtime", "stt", "llm"]
GB = 1_000_000_000


@dataclass(frozen=True)
class Spec:
    id: str
    kind: Kind
    name: str
    approx_bytes: int
    note: str = ""
    license: str = ""
    requires: tuple[str, ...] = ()
    gpu_requires: tuple[str, ...] = ()  # added only when an NVIDIA GPU is present
    vram_gb: float | None = None
    engine: str | None = None  # stt: "faster-whisper" | "qwen-asr"
    repos: tuple[str, ...] = ()  # Hugging Face repos (stt models)
    gguf: tuple[str, str] = ("", "")  # llm: (Hugging Face repo, file name) saved as models/<file name>
    summary: str = ""  # one line for the model list
    removable: bool = True
    tags: tuple[str, ...] = field(default_factory=tuple)


SPECS: list[Spec] = [
    Spec("ffmpeg", "tool", "FFmpeg (영상에서 소리 꺼내기)", 80_000_000, "LGPL 빌드, BtbN/FFmpeg-Builds", "LGPL",
         removable=False),
    Spec("cuda", "runtime", "NVIDIA CUDA 라이브러리", 1_250_000_000, "cuBLAS, cuDNN (PyPI)", "NVIDIA",
         summary="Whisper 계열 모델을 GPU로 돌릴 때 필요합니다"),
    Spec("torch", "runtime", "PyTorch 실행 환경", 2_700_000_000, "Python 3.12 + PyTorch 2.14 (CUDA 12.6) · 설치 후 약 4.5GB",
         "BSD 등", summary="Qwen3-ASR에 필요합니다. CUDA가 들어 있어 따로 받지 않습니다"),
    Spec("llama", "runtime", "llama-server (로컬 번역 엔진)", 700_000_000, "llama.cpp, CUDA 12.4", "MIT",
         summary="로컬 번역에 필요합니다"),
    Spec("large-v3", "stt", "Whisper large-v3", 3_090_000_000, "OpenAI · faster-whisper 변환본", "MIT",
         gpu_requires=("cuda",), vram_gb=5.7, engine="faster-whisper", repos=("Systran/faster-whisper-large-v3",),
         summary="가장 안정적입니다. 구두점이 거의 없고, 노래 구간을 환각 문구로 바꾸는 일이 있습니다",
         tags=("기본",)),
    Spec("large-v3-turbo", "stt", "Whisper large-v3-turbo", 1_620_000_000, "OpenAI · faster-whisper 변환본", "MIT",
         gpu_requires=("cuda",), vram_gb=2.2, engine="faster-whisper",
         repos=("mobiuslabsgmbh/faster-whisper-large-v3-turbo",),
         summary="large-v3보다 2.5배 빠르고 VRAM을 적게 씁니다. 정확도는 조금 낮습니다", tags=("빠름",)),
    Spec("kotoba-whisper-v2.0", "stt", "kotoba-whisper v2.0", 1_520_000_000, "kotoba-tech · 일본어 특화 경량 모델",
         "Apache-2.0", gpu_requires=("cuda",), vram_gb=2.4, engine="faster-whisper",
         repos=("kotoba-tech/kotoba-whisper-v2.0-faster",),
         summary="빠르지만 영화 비교에서 문장을 빠뜨리거나 같은 말을 반복했습니다", tags=("일본어 특화",)),
    Spec("qwen3-asr-1.7b", "stt", "Qwen3-ASR 1.7B", 6_200_000_000, "Alibaba Qwen · 인식 4.4GB + 시간 정렬 1.8GB",
         "Apache-2.0", requires=("torch",), vram_gb=7.5, engine="qwen-asr",
         repos=("Qwen/Qwen3-ASR-1.7B", "Qwen/Qwen3-ForcedAligner-0.6B"),
         summary="구두점이 있고 용어집을 인식에 반영합니다. 가끔 중국어가 섞이며 VRAM을 많이 씁니다",
         tags=("구두점", "용어집 반영")),
    # translation models (GGUF for llama-server). Only Gemma 4 12B has been tried on real jobs; the others
    # are candidates for the Phase 6 comparison. VRAM for them = file size + ~1.5 GB context (estimate).
    Spec("llm", "llm", "Gemma 4 12B (QAT Q4_0)", 6_980_000_000, "Google 공식 QAT 변환본", "Apache-2.0",
         requires=("llama",), vram_gb=9.0, gguf=("google/gemma-4-12B-it-qat-q4_0-gguf", "gemma-4-12b-it-qat-q4_0.gguf"),
         summary="지금까지 실제 작업에 써 온 기본 모델입니다", tags=("기본",)),
    Spec("qwen3.5-9b", "llm", "Qwen3.5 9B (Q4_K_M)", 5_680_000_000, "Alibaba Qwen · unsloth 변환본", "Apache-2.0",
         requires=("llama",), vram_gb=7.5, gguf=("unsloth/Qwen3.5-9B-GGUF", "Qwen3.5-9B-Q4_K_M.gguf"),
         summary="작고 빠릅니다. GPU 메모리가 8GB인 PC용 후보입니다. 아직 비교 전입니다", tags=("가벼움",)),
    Spec("gemma-4-26b-a4b", "llm", "Gemma 4 26B A4B (QAT Q4_0)", 14_440_000_000, "Google 공식 QAT 변환본 · MoE",
         "Apache-2.0", requires=("llama",), vram_gb=16.0,
         gguf=("google/gemma-4-26B-A4B-it-qat-q4_0-gguf", "gemma-4-26B_q4_0-it.gguf"),
         summary="한 번에 4B만 계산하는 MoE라 크기에 비해 빠릅니다. 아직 비교 전입니다"),
    Spec("mistral-small-3.2-24b", "llm", "Mistral Small 3.2 24B (Q4_K_M)", 14_330_000_000,
         "Mistral AI · unsloth 변환본", "Apache-2.0", requires=("llama",), vram_gb=16.0,
         gguf=("unsloth/Mistral-Small-3.2-24B-Instruct-2506-GGUF", "Mistral-Small-3.2-24B-Instruct-2506-Q4_K_M.gguf"),
         summary="지시를 잘 따릅니다. 일본어·한국어는 Qwen보다 약하다는 평가가 있습니다. 아직 비교 전입니다"),
    Spec("qwen3.5-35b-a3b", "llm", "Qwen3.5 35B A3B (Q4_K_M)", 22_020_000_000, "Alibaba Qwen · unsloth 변환본 · MoE",
         "Apache-2.0", requires=("llama",), vram_gb=23.5,
         gguf=("unsloth/Qwen3.5-35B-A3B-GGUF", "Qwen3.5-35B-A3B-Q4_K_M.gguf"),
         summary="한 번에 3B만 계산하는 MoE입니다. 16GB GPU에서는 일부를 CPU 메모리로 돌려 느려집니다. 아직 비교 전입니다"),
]

BY_ID: dict[str, Spec] = {s.id: s for s in SPECS}
STT_IDS = [s.id for s in SPECS if s.kind == "stt"]
LLM_IDS = [s.id for s in SPECS if s.kind == "llm"]
DEFAULT_STT = "large-v3"


def spec(model_id: str) -> Spec | None:
    return BY_ID.get(model_id)


# ---------------------------------------------------------------------- where downloaded models live


def hf_cache_dir(s: Spec) -> Path:
    """faster-whisper models use the cache layout faster-whisper itself uses (models/whisper);
    PyTorch models get their own cache (models/hf)."""
    return paths.whisper_models if s.engine == "faster-whisper" else paths.models / "hf"


def repo_dir(cache: Path, repo: str) -> Path:
    return cache / ("models--" + repo.replace("/", "--"))


def snapshot(cache: Path, repo: str, marker: str) -> Path | None:
    """The newest downloaded snapshot of `repo` that has `marker` (a file name or glob)."""
    snaps = sorted((repo_dir(cache, repo) / "snapshots").glob("*"), key=lambda p: p.stat().st_mtime, reverse=True)
    for d in snaps:
        if any(d.glob(marker)):
            return d
    return None


def marker_for(s: Spec) -> str:
    return "model.bin" if s.engine == "faster-whisper" else "*.safetensors"


def local_dirs(s: Spec) -> list[Path] | None:
    """Snapshot folder of every repo of an STT model, or None if any is missing."""
    out = []
    for repo in s.repos:
        d = snapshot(hf_cache_dir(s), repo, marker_for(s))
        if d is None:
            return None
        out.append(d)
    return out


class ModelMissing(RuntimeError):
    """The chosen model (or what it needs) is not downloaded yet."""


def require_stt(model_id: str) -> Spec:
    """Raise ModelMissing (with a message for the user) unless the STT model and its runtime are ready."""
    from jamak.installer import has_nvidia_gpu, needs, present

    s = spec(model_id)
    if s is None or s.kind != "stt":
        raise ModelMissing(f"알 수 없는 음성 인식 모델입니다: {model_id}")
    missing = [d for d in (*needs(s, has_nvidia_gpu()), s.id) if not present(d)]
    if missing:
        names = ", ".join(BY_ID[m].name for m in missing)
        raise ModelMissing(f"{s.name}을(를) 쓰려면 먼저 받아야 합니다: {names}. 설정 › 모델 관리에서 받으세요")
    return s
