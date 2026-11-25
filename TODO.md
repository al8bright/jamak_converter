# TODO

> 끝난 항목은 [PLAN.md](PLAN.md)의 해당 Phase로 옮기고 이 문서에서 지웁니다. 2026-11-25에 1~5장의 끝난 항목을 Phase 4.5로 옮겼습니다.
> 전체 검토 결과와 결정할 것은 [docs/REVIEW-2026-11-25.md](docs/REVIEW-2026-11-25.md)에 있습니다.

## 1. 음성 인식

- [ ] **노래 가사가 사라지는 문제**: large-v3가 가사 구간을 환각 문구로 바꾸고 환각 필터가 지움. 노래 구간(사용자가 지정한 시간 범위)만 Qwen3-ASR로 다시 인식하는 방법을 검토. Qwen을 받아 둔 경우에만 가능하고, 작업 프로세스를 한 번 더 띄우는 비용(모델 올리기 약 10초)이 듦
- [ ] **anime-whisper는 보류**: 변환본을 직접 배포하지 않기로 함. 다른 사람이 올린 변환본(`flyfront/anime-whisper-faster`, 내용 미확인)이나 사용자 PC 변환(PyTorch 필요)뿐이라 지금은 추가하지 않음. 쓰게 되면 인식 힌트는 넣지 말 것
- [ ] Qwen3-ASR의 이름 인식: 용어집 힌트로 しんのすけ·シロ·ボーちゃん은 고쳐졌지만 風間은 クゼマ로 남음. 힌트 문구 형식(목록 / 문장)을 바꿔 시험
- [ ] Phase 6에서 정답 자막(직접 받아 적은 3~5분 구간)으로 모델별 CER 측정. 정답 자막이 있어야 해서 지금은 할 수 없음

## 2. 클라우드 번역

- [ ] 새 서비스 7곳(OpenAI, DeepSeek, xAI, Mistral, OpenRouter, DeepL, Papago)을 실제 키로 연결 테스트. 지금은 가짜 응답으로만 확인함
- [ ] DeepL 용어집 기능(일본어→한국어)이 되면 치환 방식 대신 쓰는 것을 검토
- [ ] OpenRouter는 모델마다 JSON 스키마 지원이 달라, 지원하지 않는 모델이면 `json_object`로 자동 전환할지 결정

## 3. 내려받기

- [ ] 앱의 받기 버튼으로 PyTorch 실행 환경(uv 내려받기 → Python 3.12 → 패키지 51개)을 처음부터 받아 보기. 지금은 같은 명령을 이 PC의 uv로만 확인함
- [ ] 받기 도중 앱을 닫았을 때 남는 `torch-runtime.new`, `downloads/uv-cache` 정리
- [ ] 모델 관리에 추가한 번역 모델 4개(Qwen3.5 9B, Gemma 4 26B A4B, Mistral Small 3.2 24B, Qwen3.5 35B A3B)를 실제로 받아 보기: 지금 llama-server(b12675)가 불러오는지, GPU 메모리 실측(지금 값은 파일 크기 + 1.5GB 추정)

## 4. Mac 지원 (Apple Silicon)

지금은 Windows 전용입니다. NVIDIA GPU 대신 Apple GPU(Metal)를 씁니다. Intel Mac은 대상에서 뺍니다.

### 음성 인식

- [ ] **mlx-whisper 엔진 추가**(1순위): `core/stt/base.py`의 `engine_for`에 `"mlx-whisper"` 추가
  - 모델: large-v3, large-v3-turbo의 MLX 변환본(`mlx-community/whisper-*-mlx`)
  - 단어 시간과 결과 형식(`avg_logprob`, `no_speech_prob`, `compression_ratio`)이 openai-whisper와 같아 분할, 환각 필터, 캐시 구조를 그대로 쓸 수 있는지 확인
  - `models.py`에 Mac 전용 항목을 따로 둘지, 같은 id(`large-v3`)를 플랫폼마다 다른 변환본으로 받을지 결정. 캐시 키(`stt_identity`)에 엔진이 들어가야 Windows 캐시와 섞이지 않음
- [ ] kotoba-whisper: mlx-whisper로 돌릴 변환본이 있는지, 없으면 whisper.cpp GGML 변환본(`kotoba-tech/kotoba-whisper-v2.0-ggml`, 확인 필요)을 쓸지 결정
- [ ] 대안 비교: whisper.cpp(Metal, llama-server처럼 실행 파일, 단어 시간이 덜 정확함), WhisperKit(Core ML, 가장 빠르지만 Swift), macOS 26 SpeechAnalyzer(일본어 품질 확인 필요). 같은 영화 구간으로 속도와 품질 비교
- [ ] Qwen3-ASR: PyTorch MPS에서 qwen-asr와 ForcedAligner가 도는지 확인. 안 되면 Mac에서는 목록에서 숨김
- [ ] faster-whisper는 Mac에서 CPU로만 돌아 느림. 마지막 수단으로만 남길지 결정

### 번역

- [ ] llama-server Mac 빌드(`llama-<build>-bin-macos-arm64.zip`)를 받도록 `LlamaComponent` 수정. 지금은 Windows CUDA zip만 받음(`installer.py:234`)
- [ ] GPU 메모리 대신 통합 메모리 기준으로 모델 추천과 경고 표시(`vram_gb`, `recommended_stt`, 모델 관리의 GPU 메모리 경고)

### Windows 전용 부분 바꾸기

- [ ] **API 키 암호화**: DPAPI(`core/secrets.py`) 대신 macOS 키체인. 키 원문을 로그·meta.json·캐시·API 응답에 남기지 않는 규칙은 그대로
- [ ] **하위 프로세스 정리**: Job Object(`core/proc.py`의 `bind_to_lifetime`) 대신 프로세스 그룹과 부모 감시. `CREATE_NO_WINDOW`를 쓰는 곳(`proc.py`, `audio.py`, `separate.py`, `llama_server.py`, `qwen_asr.py`, `checks.py`, `server.py`)은 플랫폼별로 나눔
- [ ] **FFmpeg**: BtbN 빌드에는 Mac 판이 없음(`installer.py:199`). 다른 배포처(서명된 정적 빌드)를 정하고 SHA-256 확인 방식 유지
- [ ] **CUDA 항목**: Mac에서는 모델 관리 목록과 의존성(`gpu_requires`)에서 빼기. `has_nvidia_gpu()` 대신 플랫폼별 가속 확인
- [ ] **PyTorch 실행 환경**: `Scripts/python.exe` 대신 `bin/python`(`installer.py:445`), uv의 macOS arm64 빌드, `qwen-requirements.lock`을 Mac용으로 따로 만들기(cu126 인덱스 대신 PyPI)
- [ ] **경로와 실행 파일 이름**: `.exe`를 붙이는 곳 정리(`config.py:44`, `installer.py`), 데이터 폴더를 `%LOCALAPPDATA%` 대신 `~/Library/Application Support/jamak_converter`로
- [ ] **Tauri**: NSIS 대신 `.dmg`/`.app` 번들, 사이드카 이름(`lib.rs:67`의 `jamak-backend.exe`), PyInstaller Mac 빌드
- [ ] **서명과 공증**: Apple Developer ID로 서명하고 공증(notarization)하지 않으면 Gatekeeper가 실행을 막음. 받은 실행 파일(llama-server, ffmpeg, uv)의 격리 속성(quarantine) 처리도 확인
- [ ] **글꼴**: Noto Sans KR/JP가 없는 Mac에서도 화면이 맞는지 확인(DESIGN.md 4.2)

### 확인

- [ ] Mac 한 대(M 시리즈, 16GB 이상)에서 처음 실행 → 모델 받기 → 영화 한 편 처리까지 확인. Mac 장비가 있어야 해서 지금은 할 수 없음
- [ ] CI에 macOS 러너로 백엔드 단위 테스트 추가
