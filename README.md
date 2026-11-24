# jamak_converter

일본어 영상(애니메이션·드라마)을 선택하면 **한국어 자막 파일**을 만들어 주는 데스크톱 프로그램입니다.
음성 인식과 번역을 모두 **로컬 GPU**에서 처리하므로 API 비용이 없고, 영상과 대사가 외부로 나가지 않습니다.

> 상태: 설계 단계 · 개발 계획은 [PLAN.md](PLAN.md)를 참고하세요.

![architecture](docs/architecture.png)

구성도 원본은 [docs/architecture.html](docs/architecture.html)에 있습니다(브라우저에서 열기). 어두운 테마 버전은 [docs/architecture-dark.png](docs/architecture-dark.png)입니다.

---

## 1. 목표와 범위

| 항목 | 결정 |
|---|---|
| 대상 콘텐츠 | 일본어 애니메이션·드라마 (배경음악·효과음이 많고, 고유명사와 말투가 중요함) |
| 처리 방식 | 실시간이 아닌 **파일 단위 일괄 처리** (전체 문맥을 활용해 정확도 우선) |
| 음성 인식 / 번역 | **전부 로컬** (faster-whisper + llama.cpp `llama-server`) |
| GUI | Python 로컬 서버 + 웹 UI, **Tauri로 감싼 데스크톱 앱** |
| 결과물 | **자막 파일만** 생성 (SRT / ASS / VTT). 재생은 별도 플레이어에서 함 |

다루지 않는 것(현재 범위 밖): 실시간 번역, 영상에 자막 입히기와 MKV 자막 트랙 넣기, 클라우드 번역 API, 앱 내 영상 재생.

## 2. 처리 흐름

```
Video ─▶ ① FFmpeg ─▶ ② Demucs ─▶ ③ Silero VAD ─▶ ④ faster-whisper ─▶ ⑤ Segmenter ─▶ ⑥ llama-server ─▶ ⑦ Writer
 mkv/mp4   16k mono    보컬 분리     발화 구간        일본어 STT          자막 단위 분할     일→한 번역           srt/ass/vtt
                       (선택)                        (단어 타임스탬프)
```

| # | 단계 | 기술 | 출력 (작업 캐시) | 비고 |
|---|---|---|---|---|
| ① | 오디오 추출 | FFmpeg | `audio.wav` | 16kHz 모노 PCM. 여러 오디오 트랙이 있으면 일본어 트랙을 고름 |
| ② | 보컬 분리 | Demucs (htdemucs) | `vocals.wav` | 배경음악 때문에 인식이 틀리거나 환각이 생기는 것을 줄임. 옵션이며 기본값은 켬 |
| ③ | 발화 감지 | Silero VAD (faster-whisper 내장) | — | ④ 안에서 함께 실행. 무음·음악 구간을 잘라내 Whisper 환각을 막음 |
| ④ | 음성 인식 | faster-whisper `large-v3` (CUDA fp16) | `transcript.json` | 단어 단위 타임스탬프 포함. `kotoba-whisper-v2.0`은 비교 후보 |
| ⑤ | 자막 단위 분할 | 자체 구현 | `segments.ja.json` | 길이·글자 수·쉼 기준으로 다시 나누고 합침 |
| ⑥ | 번역 | llama.cpp `llama-server` (로컬 LLM, GGUF) | `segments.ko.json` | 묶음 단위 번역 + 앞뒤 문맥 + 용어집. 출력은 JSON 스키마로 강제 |
| ⑦ | 자막 쓰기 | pysubs2 | `*.ko.srt` 등 | 영상 파일 옆에 저장 |

### 단계별 캐시

각 단계의 결과는 작업 폴더에 JSON이나 WAV로 저장됩니다. 단계마다 **입력과 설정의 해시**를 기록해 두고, 바뀐 단계부터만 다시 실행합니다.
예를 들어 번역 모델을 바꾸면 ⑥과 ⑦만 다시 돌고, 시간이 오래 걸리는 ②와 ④는 건너뜁니다.

```
%LOCALAPPDATA%/jamak_converter/jobs/<video-hash>/
├─ meta.json            # 원본 경로, 길이, 단계별 설정 해시와 상태
├─ audio.wav
├─ vocals.wav
├─ transcript.json      # 단어 타임스탬프 포함 원문
├─ filtered.json        # 환각 필터가 걸러낸 구간 (디버깅용)
├─ segments.ja.json     # 자막 단위로 나눈 원문
└─ segments.ko.json     # 번역 결과 (사용자 수정 내용 포함)
```

### GPU 메모리 운용 (RTX 4080 16GB 기준)

Whisper large-v3와 14B급 LLM을 **동시에 올리면 VRAM이 부족할 수 있습니다**. 그래서 단계를 순서대로 실행하고, 한 단계가 끝나면 그 모델을 내립니다.

- ②와 ④ 단계가 끝나면 Demucs와 Whisper 모델을 메모리에서 해제합니다.
- ⑥ 단계에서만 `llama-server` 프로세스를 띄웁니다. 번역이 끝나면 프로세스를 종료하므로 GPU 메모리가 확실히 비워집니다.

## 3. 구성 요소

### Tauri 셸 (`app/src-tauri`)
- Tauri 2를 사용합니다. Windows에서는 WebView2로 React UI를 띄웁니다.
- 앱이 시작될 때 Python 백엔드(PyInstaller로 만든 exe)를 **사이드카**로 실행합니다. 빈 포트를 골라 인자로 넘기고, `/health`가 응답할 때까지 기다린 뒤 UI에 포트를 알려 줍니다. 앱을 종료하면 백엔드 프로세스도 함께 종료합니다.
- 파일 선택은 Tauri dialog 플러그인으로 합니다. 백엔드에는 **파일 경로만** 넘기고 업로드는 하지 않습니다. 백엔드가 같은 PC에서 파일을 직접 읽습니다.

### 웹 UI (`app/src`)
- React, Vite, TypeScript로 만듭니다.
- 화면 구성: 파일 선택 → 옵션(보컬 분리, 모델, 용어집) → 진행률 → 자막 검수와 편집 → 저장.
- 진행률은 WebSocket으로 받습니다. 단계 이름, 진행 비율, 남은 시간 추정치가 표시됩니다.
- 개발 중에는 Tauri 없이 브라우저만으로도 동작해야 합니다. 이때 파일 선택은 경로를 직접 입력하는 방식으로 대신합니다.

### Python 백엔드 (`backend/`)
- Python 3.12를 쓰고 uv로 관리합니다. 시스템에 깔린 3.14는 CTranslate2·PyTorch 휠 호환성 문제가 생길 수 있어 사용하지 않습니다.
- `jamak.core`: 파이프라인 단계. UI와 무관한 순수 라이브러리입니다.
- `jamak.api`: FastAPI 앱. `127.0.0.1`에만 바인딩하고, 작업을 하나씩 순서대로 처리하는 큐를 둡니다(GPU를 하나만 쓰기 때문).
- `jamak.cli`: 같은 core를 쓰는 명령줄 도구. 일괄 처리와 디버깅에 씁니다.

STT와 번역 엔진은 인터페이스 뒤에 숨겨 교체할 수 있게 만듭니다.

```python
class SpeechRecognizer(Protocol):
    def transcribe(self, audio: Path, initial_prompt: str | None, progress, cancel) -> Transcript: ...

class Translator(Protocol):
    def translate(self, lines: list[Line], glossary, progress, cancel, only_ids=None) -> None: ...
```

### 번역 서버: llama.cpp `llama-server`

번역은 llama.cpp의 `llama-server`가 담당합니다. Ollama를 쓰지 않는 이유는 다음과 같습니다.

- **따로 설치할 필요가 없습니다.** Windows용 CUDA 빌드 exe를 앱에 함께 넣어 배포할 수 있습니다.
- **GPU 메모리 관리가 확실합니다.** 백엔드가 번역 단계에서만 프로세스를 띄우고 끝나면 종료하므로, Whisper와 LLM이 GPU 메모리를 순서대로 쓰는 구조가 깔끔하게 지켜집니다.
- **출력 형식을 강하게 제한할 수 있습니다.** JSON 스키마(`response_format`)와 GBNF 문법을 모두 지원합니다.
- **모델 파일을 그대로 씁니다.** Hugging Face의 GGUF 파일을 별도 변환 없이 사용합니다.

실행 방식:

1. 번역 단계가 시작되면 백엔드가 빈 포트를 골라 `llama-server`를 실행합니다. 이때 GGUF 경로, `-ngl 99`(모든 레이어를 GPU에 올림), 컨텍스트 길이, `--host 127.0.0.1`을 인자로 넘깁니다.
2. `/health`가 준비 상태가 될 때까지 기다립니다. 모델을 불러오는 진행 상황은 UI 진행률에 반영합니다.
3. OpenAI 호환 API인 `/v1/chat/completions`로 묶음 번역을 요청합니다.
4. 번역이 끝나거나 오류가 나거나 작업이 취소되면 프로세스를 종료합니다.

번역 클라이언트는 **OpenAI 호환 API 기준**으로 작성합니다(`translate/openai_compat.py`). 그래서 설정에서 "외부 서버" 모드를 고르고 주소를 입력하면, 이미 실행 중인 Ollama, LM Studio, 다른 PC의 `llama-server`도 코드 수정 없이 쓸 수 있습니다.

```
%LOCALAPPDATA%/jamak_converter/
├─ bin/llama-server/     # llama.cpp Windows CUDA 빌드 (scripts/setup_llama.ps1이 준비)
└─ models/              # *.gguf 번역 모델
```

### API 초안

| 메서드 | 경로 | 설명 |
|---|---|---|
| `GET` | `/health` | 준비 상태 확인 |
| `GET` | `/system` | GPU, CUDA, `llama-server` 실행 파일 확인, 사용할 수 있는 GGUF 모델 목록 |
| `POST` | `/jobs` | `{video_path, options}` → `job_id` |
| `GET` | `/jobs/{id}` | 단계별 상태 |
| `WS` | `/jobs/{id}/events` | 진행률 이벤트 |
| `GET` | `/jobs/{id}/segments` | 원문과 번역 자막 목록 |
| `PATCH` | `/jobs/{id}/segments` | 사용자가 수정한 내용 저장 |
| `POST` | `/jobs/{id}/retranslate` | 선택한 줄만 다시 번역 |
| `POST` | `/jobs/{id}/export` | `{formats, dual, path}`로 자막 파일 저장 |
| `DELETE` | `/jobs/{id}` | 작업 취소 또는 캐시 삭제 |

## 4. 애니·드라마에 맞춘 설계 포인트

- **배경음악과 노래**: Demucs로 보컬을 분리합니다. 오프닝·엔딩곡은 가사를 번역할지 건너뛸지 옵션으로 정합니다.
- **Whisper 환각**: VAD를 쓰고 `condition_on_previous_text=False`로 설정합니다. 같은 문장이 반복되거나 "ご視聴ありがとうございました" 같은 알려진 환각 문구가 나오면 걸러냅니다.
- **고유명사**: 작품별 **용어집**(`glossary.json`, 일본어 표기 → 한국어 표기)을 Whisper의 `initial_prompt`(인식 힌트)와 번역 프롬프트 양쪽에 넣습니다.
- **말투**: 번역 프롬프트에 "자연스러운 구어체 한국어 자막, 원문의 존댓말·반말 유지, 의역 허용, 한 줄 길이 제한" 규칙을 둡니다.
- **문맥**: 앞에서 번역한 몇 줄을 문맥으로 함께 넘겨 주어가 생략된 문장도 이어지게 번역합니다.
- **줄 맞춤 보장**: 번역 결과는 `[{id, ko}]` 형태의 JSON으로 받습니다(`response_format`에 JSON 스키마 지정, 필요하면 GBNF 문법으로 더 엄격하게 제한). 줄 수나 id가 맞지 않으면 묶음 크기를 줄여 다시 시도합니다.

## 5. 출력

영상 파일과 같은 폴더에 같은 이름으로 저장하므로, 대부분의 플레이어가 자막을 자동으로 불러옵니다.

```
episode01.mkv
episode01.ko.srt        # 한국어 (기본)
episode01.ja.srt        # 일본어 원문 (옵션)
episode01.ja-ko.ass     # 이중 자막: 위 원문, 아래 번역 (옵션)
```

## 6. 개발 환경

| 도구 | 용도 | 이 PC 상태 (2026-11-24) |
|---|---|---|
| NVIDIA GPU + 드라이버 | CUDA 추론 | RTX 4080 16GB, 드라이버 621.24 ✅ |
| FFmpeg | 오디오 추출 | 8.1 ✅ |
| uv | Python 3.12 가상환경과 의존성 관리 | 0.12.22 ✅ |
| Node.js + pnpm | 프런트엔드 | Node 24 / pnpm 11 ✅ |
| Rust (rustup) + MSVC | Tauri 빌드 (개발 PC에만 필요) | rustc 1.100.0 stable-msvc, VS Build Tools 2026 ✅ |
| llama.cpp `llama-server` | 로컬 LLM 서버 (Windows CUDA 빌드) | **자동 준비**: `scripts/setup_llama.ps1` (설치 불필요) |
| GGUF 번역 모델 | 일→한 번역 | ❌ 내려받기 필요 (Phase 0에서 잠정 모델 1개, Phase 6에서 확정) |

CUDA 런타임은 시스템에 따로 설치하지 않습니다.
- Python 쪽(faster-whisper)은 pip 패키지(`nvidia-cublas-cu12`, `nvidia-cudnn-cu12`)로 가상환경 안에 넣습니다.
- `llama-server` 쪽은 llama.cpp 릴리스의 `cudart` DLL을 실행 파일 옆에 둡니다. 둘은 서로 다른 프로세스라 충돌하지 않습니다.

### llama-server 준비

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup_llama.ps1
```

- 고정된 빌드(현재 `b12675`, CUDA 12.4)의 `llama-server.exe`와 CUDA 런타임 DLL을 GitHub 릴리스에서 받습니다. 받는 양은 약 750MB이고, 풀면 약 1.4GB입니다. llama.cpp의 다른 도구까지 들어 있기 때문이며, 배포할 때는 필요한 파일만 남깁니다.
- GitHub가 공개한 SHA-256 값과 비교해 파일이 온전한지 확인합니다.
- `%LOCALAPPDATA%\jamak_converter\bin\llama-server\`에 풀고, 빌드 정보를 `version.json`에 기록합니다.
- 마지막으로 `--list-devices`를 실행해 GPU가 인식되는지 확인합니다.
- 같은 빌드가 이미 있으면 아무것도 하지 않습니다. 옵션:

| 옵션 | 설명 |
|---|---|
| `-Build b12700` | 다른 빌드로 바꿈. 바꾼 뒤에는 PLAN.md Phase 0의 확인 항목을 다시 실행 |
| `-Cuda 13.5` | CUDA 13 빌드 사용. 최신 드라이버가 필요 |
| `-Dest <경로>` | 설치 위치 변경 |
| `-Force` | 같은 빌드라도 다시 받음 |

관리자 권한, PATH 등록, 서비스 등록은 필요 없습니다. 지우려면 폴더를 삭제하면 됩니다.

### 실행

```powershell
# 번역 서버 준비 (처음 한 번)
powershell -ExecutionPolicy Bypass -File scripts\setup_llama.ps1

# 백엔드
cd backend
uv sync
uv run jamak doctor                                  # ffmpeg, CUDA, llama-server, 모델 확인
uv run jamak transcribe "D:\video\episode01.mkv"   # CLI
uv run jamak serve --port 8765                      # API 서버

# 프런트엔드 + Tauri
cd app
pnpm install
pnpm tauri dev
```

번역 모델은 `%LOCALAPPDATA%\jamak_converter\models\`에 GGUF 파일을 두면 됩니다. 잠정 기본값은 Google 공식 [`gemma-4-12b-it-qat-q4_0.gguf`](https://huggingface.co/google/gemma-4-12B-it-qat-q4_0-gguf)(7GB, Apache 2.0)입니다. Whisper 모델(약 3GB)은 처음 실행할 때 자동으로 내려받습니다.

#### CLI 주요 옵션

| 옵션 | 설명 |
|---|---|
| `--glossary 파일` | 용어집. `.txt`(한 줄에 `ユウキ=유키`) 또는 `.json`(`[{"ja","ko","note"}]`). 인식 힌트와 번역에 모두 반영 |
| `--formats srt,ass,vtt` | 출력 형식 (기본 `srt`) |
| `--ja` / `--dual` | 일본어 자막도 저장 / 일본어+한국어 이중 ASS 저장 |
| `--separate` | Demucs 보컬 분리 사용 |
| `--parallel N` | 번역 묶음 N개 동시 처리 |
| `--llm-model 경로` | 다른 GGUF 사용 |
| `--llm-url URL` | 외부 OpenAI 호환 서버 사용 (예: Ollama `http://127.0.0.1:11434/v1`) |
| `--force 단계` | 캐시가 있어도 해당 단계부터 다시 실행 |
| `--out 폴더` | 출력 폴더 (기본: 영상 옆) |

#### 테스트

```powershell
cd backend
uv run pytest            # 단위 테스트 (GPU 불필요)
uv run pytest -m gpu -s  # 끝까지 실행하는 테스트 (CUDA, Whisper, llama-server 필요)
```

테스트용 영상은 저작권 문제가 없도록 Open JTalk(`pyopenjtalk`)로 일본어 대사를 합성해 자동으로 만듭니다(`tests/fixtures_gen.py`). 트랙이 두 개인 MKV(영어 무음 + 일본어 대사 + 배경음)라서 트랙 선택, 발화 감지, 인식, 번역을 한 번에 확인할 수 있습니다.

## 7. 폴더 구조 (예정)

```
jamak_converter/
├─ backend/                 # Python 3.12 (uv)
│  ├─ pyproject.toml
│  ├─ src/jamak/
│  │  ├─ core/
│  │  │  ├─ audio.py        # ffmpeg 추출, 트랙 선택
│  │  │  ├─ separate.py     # Demucs
│  │  │  ├─ filters.py      # Whisper 환각 필터
│  │  │  ├─ proc.py         # 자식 프로세스 수명 관리 (Windows Job Object)
│  │  │  ├─ stt/            # base.py, faster_whisper.py, kotoba.py
│  │  │  ├─ segmenter.py
│  │  │  ├─ translate/      # base.py, openai_compat.py, llama_server.py, prompts/
│  │  │  ├─ subtitle.py     # SRT/ASS/VTT 쓰기
│  │  │  ├─ cache.py        # 단계 해시와 작업 폴더 관리
│  │  │  └─ pipeline.py
│  │  ├─ api/               # FastAPI 라우트, WebSocket, 작업 큐
│  │  └─ cli.py
│  └─ tests/
├─ app/                     # React + Vite + TS
│  ├─ src/
│  └─ src-tauri/            # Tauri 2, 사이드카 설정
├─ docs/
│  ├─ architecture.html     # 구성도 원본 (stack-diagram)
│  ├─ architecture.png
│  └─ architecture-dark.png
├─ scripts/
│  └─ setup_llama.ps1       # llama-server 빌드 내려받기·검증·설치
├─ README.md
└─ PLAN.md
```

## 8. 라이선스 메모

- Whisper 모델, faster-whisper, Silero VAD, Demucs, llama.cpp는 모두 MIT입니다. 각 LLM의 라이선스는 모델마다 다르므로 배포 전에 확인해야 합니다.
- FFmpeg는 LGPL/GPL 빌드에 따라 함께 배포할 때의 조건이 다릅니다. 배포 단계에서 다시 검토합니다.
