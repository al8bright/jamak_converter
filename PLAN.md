# 개발 계획

전체 구조는 [README.md](README.md)를 참고하세요. 이 문서는 **무엇을 어떤 순서로 만들고, 각 단계가 끝났는지 무엇으로 판단하는지**를 정리합니다.

## 원칙

1. **core → CLI → API → UI → Tauri 순서로 쌓습니다.** 아래 층이 혼자 동작해야 다음 층으로 넘어갑니다.
2. **실제 영상 품질 검증은 가장 마지막(Phase 6)에 합니다.** 그 전까지는 잠정 기본값(STT `large-v3`, Phase 0에서 고른 GGUF 1개)으로 개발합니다. 개발 중에는 짧은 일본어 클립으로 **끝까지 동작하는지만** 확인하고 품질은 판단하지 않습니다. 모델 선택, 분할 규칙, 필터 임계값은 Phase 6에서 샘플 세트로 측정해 확정합니다.
3. **단계별 캐시는 처음부터 넣습니다.** 나중에 붙이면 반복 실험 속도가 크게 떨어집니다.
4. **배포 패키징(Phase 5)은 기능이 굳은 뒤에 합니다.** 개발 중에는 `uv run`과 `pnpm tauri dev`로만 실행합니다.

## 마일스톤 요약

| Phase | 목표 | 결과물 | 완료 기준 |
|---|---|---|---|
| 0 | 환경 준비 | 개발 도구, git 저장소, 잠정 번역 모델 | GPU 추론과 llama-server 응답 확인 |
| 1 | CLI MVP | `jamak transcribe <video>` → `.ko.srt` | 짧은 클립을 끝까지 처리 |
| 2 | 기능 확장 | 보컬 분리, 용어집, 이중 자막, 환각 필터, 번역 병렬화 | 각 기능이 켜고 끄기에 따라 동작 |
| 3 | API 서버 | FastAPI + 작업 큐 + WebSocket | CLI와 같은 결과를 HTTP로 얻음 |
| 4 | 웹 UI + Tauri | 파일 선택 → 진행률 → 검수·편집 → 저장 | 앱 안에서 처음부터 끝까지 완료 |
| 5 | 배포 (필요할 때) | 설치 파일 | 개발 도구가 없는 PC에서 설치하고 실행 |
| 6 | **샘플 영상 검증 (마지막)** | 벤치마크 보고서, 확정된 기본값 | 기준 샘플 세트로 측정하고 기본값 확정 |

---

## Phase 0 — 환경 준비

- [x] Rust 설치 (`rustup`, stable-x86_64-pc-windows-msvc, rustc 1.100.0). MSVC는 VS Build Tools 2026이 이미 있음
- [x] `scripts/setup_llama.ps1`: 고정 빌드의 `llama-server.exe`와 CUDA DLL 내려받기, SHA-256 검증, 설치
- [x] 잠정 번역 모델로 쓸 GGUF 1개 내려받기 → Google 공식 `gemma-4-12b-it-qat-q4_0.gguf` (7GB, Apache 2.0, SHA-256 검증)
- [x] llama-server 동작 확인: `-ngl 99`로 GPU에 올리고 `/v1/chat/completions`에 `response_format`(JSON 스키마)을 넣었을 때 `[{id, ko}]` 형식이 지켜지는지, VRAM 사용량 확인
- [x] `backend/` 생성: `uv init --python 3.12`, faster-whisper, CUDA 휠(`nvidia-cublas-cu12`, `nvidia-cudnn-cu12`) 설치
- [x] GPU 확인 스크립트: faster-whisper `large-v3`를 `cuda` + `float16`으로 로드해서 짧은 wav 인식
- [x] git 저장소 초기화(`github.com/al8bright/jamak_converter`, `main`), `.gitignore` 작성 (모델, 샘플, 작업 캐시 제외)

**완료 기준**: GPU 확인 스크립트가 CUDA로 동작하고, llama-server가 JSON 스키마대로 응답함.

> 결과 (2026-11-24): `jamak doctor` 전 항목 OK. llama-server + Gemma 4 12B: JSON 스키마(id 키 고정) 준수, 약 60 tok/s, 로드 후 VRAM 약 12.5GB(ctx 8192). Windows에서는 `nvidia-cublas-cu12` 최신판에 Windows 휠이 없어 `12.9.2.10`으로 고정.

## Phase 1 — CLI MVP (핵심 파이프라인)

보컬 분리 없이 가장 짧은 경로로 영상에서 한국어 SRT까지 만듭니다.

- [x] `core/audio.py`: FFmpeg로 16kHz 모노 WAV 추출, `ffprobe`로 오디오 트랙 목록을 보고 일본어 트랙 선택
- [x] `core/cache.py`: 작업 폴더, `meta.json`, 단계별 설정 해시, 바뀐 단계만 다시 실행하는 로직
- [x] `core/vad.py` 대신 faster-whisper 내장 Silero VAD 사용 (`vad_filter=True`, 별도 단계 없음)
- [x] `core/stt/faster_whisper.py`: `word_timestamps=True`, `condition_on_previous_text=False`, VAD 구간 적용
- [x] `core/segmenter.py`: 단어 타임스탬프를 바탕으로 자막 단위로 다시 나누기
  - 초기 규칙(설정으로 바꿀 수 있게): 자막 하나는 1~7초, 일본어 한 줄은 최대 20자 정도, 문장부호(。？！、)와 0.4초 이상 쉼에서 자르기, 짧은 조각은 앞뒤와 합치기
- [x] `core/translate/llama_server.py`: `llama-server` 프로세스 관리
  - 빈 포트를 골라 실행(`-m <gguf> -ngl 99 -c <ctx> --host 127.0.0.1 --port <p>`), `/health`가 준비될 때까지 대기
  - 번역이 끝나거나 오류·취소가 생기면 종료. 백엔드가 죽어도 남지 않도록 Windows Job Object로 묶음
- [x] `core/translate/openai_compat.py`: `/v1/chat/completions` 번역 클라이언트 (외부 서버 모드에서도 그대로 사용)
  - 25줄 정도씩 묶고, 앞에서 번역한 5줄을 문맥으로 함께 전달
  - `response_format`에 JSON 스키마를 지정해 `[{id, ko}]` 형태로 받음. id나 줄 수가 안 맞으면 묶음 크기를 줄여 다시 시도
  - 시스템 프롬프트: 구어체 자막, 존댓말·반말 유지, 한 줄 길이 제한
- [x] `core/subtitle.py`: pysubs2로 SRT 쓰기
- [x] `core/pipeline.py` + `cli.py`: `jamak transcribe <video> [--model] [--llm] [--out]`, 단계별 진행률 로그 출력
- [x] 단위 테스트: 분할 규칙, 캐시 무효화, 번역 응답 검증과 재시도

**완료 기준**: 짧은 일본어 클립(1~2분)을 처리해 `.ko.srt`가 나오고, 플레이어에서 열었을 때 싱크가 대략 맞음. 단계별 소요 시간이 로그에 남음. 번역 품질은 이 단계에서 판단하지 않음.

> 결과 (2026-11-24): 합성 대사 영상(31초, `tests/fixtures_gen.py`)으로 끝까지 통과(`pytest -m gpu`). CER 6.1%(용어집 없이, 이름 1개 오인식) → 용어집 사용 시 이름도 정확. 소요: 오디오 0.7s, STT 7.0s(모델 로드 포함), 번역 8.9s(llama-server 시작 포함). 실제 영상 클립 확인은 사용자 클립을 받으면 추가.

## Phase 2 — 기능 확장 (애니·드라마 대응)

이 단계에서는 기능을 만들고 켜고 끌 수 있게만 합니다. 효과 측정과 기본값 결정은 Phase 6에서 합니다.

- [x] STT 엔진 교체 구조: `large-v3-turbo`, `kotoba-whisper-v2.0`도 설정으로 고를 수 있게 함
- [x] `core/separate.py`: Demucs `htdemucs`로 보컬 분리, 옵션 처리, 끝나면 GPU 메모리 해제
- [x] 환각 필터: 반복 문장, 알려진 환각 문구, `no_speech_prob`와 `avg_logprob` 임계값으로 거르기
- [x] 용어집: `glossary.json`(작품별) → Whisper `initial_prompt`와 번역 프롬프트에 반영
- [x] 오프닝·엔딩 노래 구간 처리 옵션(가사 번역 / 건너뛰기)
- [x] 출력 확장: `.ja.srt`, `.ja-ko.ass` 이중 자막(스타일 템플릿), VTT
- [x] GPU 메모리 순서 제어: STT가 끝나면 Whisper 모델을 해제한 뒤 llama-server 실행. 단계 사이 VRAM 사용량을 기록해 확인
- [x] 번역 병렬화: llama-server `-np 2~4` 슬롯으로 여러 묶음을 동시에 처리. 문맥은 "앞 묶음의 번역 결과" 대신 "앞 묶음의 일본어 원문"을 넘겨 묶음끼리 서로 기다리지 않게 함. 순차 / 병렬을 옵션으로 둠
- [x] (선택) GBNF 문법 제한 → 불필요: llama-server가 JSON 스키마를 내부에서 문법으로 바꿔 강제하고, 키를 묶음의 id로 고정해 줄 수·순서가 어긋날 수 없음

**완료 기준**: 각 기능을 옵션으로 켜고 끌 수 있고 테스트 클립에서 동작함. 이중 자막 ASS가 플레이어에서 열림. 환각 필터와 병렬 번역의 줄 맞춤은 단위 테스트로 확인.

> 결과 (2026-11-24): 보컬 분리는 선택 설치(`uv sync --extra separation`, PyTorch 2.14 + CUDA 12.6). 60초 단위로 스트리밍하고 1초 겹쳐 이어 붙여 2시간 영상도 메모리에 다 올리지 않음(작은 청크로 이음매 경로 GPU 테스트 통과). 노래 구간은 사용자가 지정한 시간 범위(`--song 0:00-1:30`)로 처리. STT 모델은 `large-v3` / `large-v3-turbo` / `kotoba-whisper-v2.0` 이름으로 선택. 병렬 번역(`-np 2`) 실서버 동작 확인. 효과 측정과 기본값은 Phase 6.

## Phase 3 — API 서버

- [x] `api/`: FastAPI 앱, `127.0.0.1`에만 바인딩, 포트는 인자로 받음
- [x] 작업 큐: GPU 하나 기준으로 한 번에 하나씩 처리, 작업 취소 지원
- [x] 엔드포인트: `/health`, `/system`, `/jobs`(생성·조회·삭제), `/jobs/{id}/segments`(조회·수정), `/retranslate`, `/export`
- [x] WebSocket `/jobs/{id}/events`: 단계, 진행 비율, 로그, 남은 시간 추정
- [x] 사용자가 수정한 자막을 `segments.ko.json`에 저장. 같은 줄을 다시 번역해도 수정한 내용은 덮어쓰지 않음(잠금 플래그)
- [x] `/system`: CUDA 사용 여부, VRAM, `llama-server` 실행 파일 확인, `models/`의 GGUF 목록
- [x] 설정: 번역 서버 모드 선택(내장 llama-server / 외부 OpenAI 호환 서버 주소) → `PipelineOptions.translate.mode`로 작업마다 지정
- [x] API 테스트 (STT와 LLM은 가짜 엔진으로 대체)

**완료 기준**: HTTP만으로 작업을 만들고, 진행률을 받고, 내보내기까지 할 수 있으며 결과가 CLI와 같음.

> 결과 (2026-11-24): API 테스트 6개(가짜 파이프라인)와 실제 서버로 GPU 작업 1건 통과(`jamak serve --port 0` → 등록 → 진행률 → 자막 조회 → 내보내기, STT 7.0s·번역 9.3s로 CLI와 같은 결과). 보안: 루프백 전용, Host 헤더 검사, 선택적 토큰. 작업 폴더 `meta.json`을 여러 스레드가 동시에 쓰다 Windows에서 충돌하는 문제를 발견해, 경로별 잠금 + 매번 다시 읽고 고쳐 쓰는 방식으로 수정.

## Phase 4 — 웹 UI + Tauri

- [ ] `app/` 생성: Vite + React + TS, `pnpm tauri init`(Tauri 2)
- [ ] 개발 모드: 백엔드는 `uv run jamak serve`로 따로 띄우고, UI는 환경 변수로 받은 API 주소에 연결
- [ ] 화면
  - [ ] 홈: 파일 선택(Tauri dialog, 브라우저에서는 경로 입력) + 최근 작업 목록
  - [ ] 옵션: 보컬 분리, STT 모델, 번역 모델, 용어집 선택과 편집, 출력 형식
  - [ ] 진행: 단계별 진행 막대, 로그, 취소 버튼
  - [ ] 검수·편집: 시간 / 원문 / 번역 표, 줄 단위 수정, 여러 줄 골라 다시 번역, 글자 수 초과 경고
  - [ ] 저장: 형식 선택, 영상 옆에 저장하고 폴더 열기
  - [ ] 시스템 상태: GPU, llama-server, 모델 파일 상태 표시. 문제가 있으면 해결 방법 안내
- [ ] Tauri 사이드카 연동
  - 개발 중에는 `uv run`으로 실행, 배포 때는 PyInstaller exe를 `externalBin`으로 실행
  - 빈 포트를 골라 넘기고 `/health`가 응답할 때까지 대기한 뒤 UI에 포트 전달
  - 앱을 종료하면 사이드카도 종료. 비정상 종료 시 남은 프로세스 정리
- [ ] 영상을 끌어다 놓아 선택하는 기능

**완료 기준**: `pnpm tauri dev`로 띄운 앱에서 영상 선택 → 처리 → 수정 → 저장까지 마우스로만 끝낼 수 있음.

## Phase 5 — 배포 (필요할 때)

- [ ] 백엔드를 PyInstaller로 묶기(onedir). CUDA DLL 포함 여부와 용량 점검
- [ ] 모델은 처음 실행할 때 내려받기(진행률 표시), 저장 위치는 `%LOCALAPPDATA%`
- [ ] llama-server(CUDA 빌드)와 CUDA DLL을 앱에 함께 배포. 풀린 1.4GB 중 `llama-server.exe`와 이 파일이 실제로 쓰는 DLL만 남겨 용량 줄이기. 번역 GGUF 모델은 처음 실행할 때 내려받기
- [ ] FFmpeg 함께 배포 여부와 라이선스 검토
- [ ] Tauri 번들러로 NSIS 또는 MSI 설치 파일 생성
- [ ] (선택) GPU가 없을 때 CPU로 대체 실행. 매우 느리다는 경고 표시

**완료 기준**: 개발 도구가 없는 Windows PC에서 설치 후 영상 1편 처리 성공.

## Phase 6 — 샘플 영상 검증 (가장 마지막)

실제 애니·드라마 영상으로 품질을 측정하고 기본값을 확정합니다. 결과는 `docs/benchmark.md`에 기록합니다.

- [ ] **기준 샘플 세트** 만들기 (`samples/`는 git에서 제외)
  - 애니 2편, 드라마 1편에서 각 3~5분짜리 구간: 대사가 많은 장면, 배경음악이 큰 장면, 오프닝곡 포함
  - 가능하면 공식 일본어 자막을 정답으로 확보해 인식 오류율(CER) 계산에 씀
  - 처리 시간 측정용으로 전체 길이 영상(24분 1편, 2시간 1편)도 준비
- [ ] STT 비교: `large-v3`, `large-v3-turbo`, `kotoba-whisper-v2.0`의 CER과 처리 속도
- [ ] 보컬 분리 효과: 배경음악이 큰 구간에서 Demucs 켜기 / 끄기에 따른 CER 차이
- [ ] 번역 비교: GGUF 후보 2~3개를 직접 읽어 보고 오역, 말투, 이름 표기, 처리 속도를 점검
- [ ] 병렬 번역: 순차와 병렬의 품질 차이와 속도
- [ ] 분할 규칙(길이, 글자 수, 쉼)과 환각 필터 임계값 조정
- [ ] 전체 처리 시간 측정(24분, 2시간) → 실측값을 README에 추가
- [ ] 측정 결과로 기본값 확정하고 설정 파일에 반영

**완료 기준**: `docs/benchmark.md`에 측정 결과와 확정된 기본값이 정리되고, 기준 샘플 세트를 기본값으로 처리했을 때 검수에서 고칠 줄이 납득할 수준임.

---

## 이후 고려 (백로그)

- 화자 구분(pyannote): ASS에서 화자별로 색을 다르게 하고, 화자별 말투를 번역에 반영
- 여러 파일 / 폴더 단위 일괄 처리, 시리즈 단위로 용어집 자동 공유
- 내장 영상 미리보기(저해상도 MP4 프록시 + `<video>` + VTT)
- yt-dlp로 URL 입력
- MKV에 자막 트랙 넣기(ffmpeg, 재인코딩 없음)
- 번역 품질을 높이는 2차 단계: 1차 번역을 같은 LLM이나 더 큰 LLM으로 다듬기

## 위험 요소와 대응

| 위험 | 영향 | 대응 |
|---|---|---|
| Python 3.14와 CTranslate2·PyTorch 휠 호환성 | 설치 실패 | uv로 3.12 고정 |
| Whisper와 LLM을 동시에 올리면 VRAM 부족 | 메모리 부족 오류 | 단계를 순서대로 실행. Whisper는 해제하고, llama-server는 번역할 때만 실행 |
| 배경음악 구간에서 Whisper 환각 | 엉뚱한 자막 | VAD + Demucs + 환각 필터 |
| 로컬 LLM이 줄 수나 형식을 어김 | 자막이 어긋남 | JSON 스키마 강제(필요하면 GBNF) + 검증 + 묶음을 줄여 재시도 |
| llama.cpp는 업데이트가 잦아 옵션이나 API가 바뀔 수 있음 | 실행 실패 | 검증한 빌드 번호를 고정하고, 올릴 때는 Phase 0 확인 항목을 다시 실행 |
| 로컬 LLM 번역 품질의 한계 | 어색한 번역 | 용어집, 문맥 창, 검수 UI, Phase 6 모델 비교 |
| 품질 검증이 마지막이라 늦게 문제를 발견할 수 있음 | 분할 규칙이나 구조를 뒤늦게 고쳐야 함 | 모델, 규칙, 임계값을 처음부터 설정값으로 빼 두고, 단계별 캐시로 다시 돌리는 비용을 낮춤 |
| PyInstaller에 CUDA까지 묶으면 용량이 큼 | 배포 부담 | 배포는 Phase 5로 미루고, 모델은 처음 실행할 때 내려받기 |
| 사이드카 프로세스(Python 백엔드, llama-server)가 남는 문제 | 포트 충돌, GPU 점유 | Windows Job Object로 부모와 함께 종료되게 묶고, 시작할 때 남은 프로세스 정리 |
