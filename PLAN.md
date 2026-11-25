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
| 2 | 기능 확장 | 보컬 분리, 용어집, 이중 자막, 환각 필터, 번역 병렬화, **번역 엔진 선택(로컬 / Claude / Gemini)과 설정 DB** | 각 기능이 켜고 끄기에 따라 동작 |
| 3 | API 서버 | FastAPI + 작업 큐 + WebSocket | CLI와 같은 결과를 HTTP로 얻음 |
| 4 | 웹 UI + Tauri | 파일 선택 → 진행률 → 검수·편집 → 저장 | 앱 안에서 처음부터 끝까지 완료 |
| 4.5 | 디자인·용어집·모델 관리·클라우드 확장 | 용어집 화면, 모델별 받기·지우기, Qwen3-ASR, 클라우드 9곳 | 작은 설치 파일 + 필요한 것만 받기 |
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

### 번역 엔진 선택 + 설정 저장 (TODO.md에서 옮김)

번역 단계(⑥)에서 로컬 LLM과 클라우드 API(Claude, Gemini) 중 하나를 고릅니다. 기본값은 로컬이고, 클라우드는 사용자가 키를 넣고 직접 골랐을 때만 씁니다. 클라우드를 골라도 외부로 나가는 것은 ⑥ 단계의 일본어 대사 텍스트뿐입니다.

| 항목 | 결정 |
|---|---|
| 저장소 | SQLite 1개 파일 `%LOCALAPPDATA%/jamak_converter/jamak.db`. 설정만 저장하고 작업 캐시는 파일로 둠 |
| API 키 보호 | 평문 저장 안 함. Windows DPAPI(`CryptProtectData`, 현재 사용자 범위)를 `ctypes`로 호출 |
| 선택 단위 | **번역 프로필** = 엔진 종류 + 모델 + 키·주소·옵션. 여러 개 저장하고 하나를 기본으로, 작업마다 바꿀 수 있음 |
| 클라우드 클라이언트 | 공식 SDK: Claude는 `anthropic`, Gemini는 `google-genai` |
| 기본값 | 기본 프로필은 "로컬 · Gemma 4 12B"(내장 llama-server). 클라우드 모델의 기본값과 효용은 Phase 6에서 확정 |

- [x] `core/store.py`: 표준 `sqlite3`, WAL·외래 키, `PRAGMA user_version` 마이그레이션. 테이블 `api_keys`, `translation_profiles`, `app_settings`. 처음 실행 때 로컬 프로필을 만들어 기본으로 지정
- [x] `core/secrets.py`: `protect` / `unprotect`(DPAPI), 화면에는 `last4`만. 키 원문은 로그·예외·`meta.json`·캐시 해시·API 응답에 넣지 않음. 환경 변수(`ANTHROPIC_API_KEY`, `GEMINI_API_KEY`) 대체 지원
- [x] `core/translate/base.py`: 엔진 공통 묶음 번역(응답 검증, 재시도, 묶음 쪼개기, 병렬, 토큰·비용 집계, 거절 줄 표시)
- [x] `core/translate/anthropic_api.py`: 구조화 출력(`output_config.format`)으로 `[{id, ko}]`, 시스템 프롬프트 + 용어집에 프롬프트 캐싱, `effort` 옵션, `stop_reason == "refusal"` 처리. Opus·Sonnet 5.5는 서버 측 거절 대체 경로(`fallbacks: "default"`, 베타 `server-side-fallback-2026-07-01`)를 켬
- [x] `core/translate/gemini_api.py`: JSON 스키마 응답, 차단·거절 응답 처리, 모델 목록은 API에서 조회
- [x] `core/translate/factory.py`: 프로필 → 엔진, 작업 프로필 → 기본 프로필 → `TranslateOptions` 순서로 결정. 클라우드면 llama-server를 띄우지 않음. 거절된 줄은 설정에 따라 로컬로 다시 번역(`local_fallback` 표시)하거나 표시만 함
- [x] 캐시 해시: ⑥ 단계에 `종류 + 모델 + 프롬프트 버전 + 번역 옵션`. API 키와 키 id는 넣지 않음
- [x] CLI: `transcribe --profile`, `keys add|list|remove`(getpass), `profiles add|list|set-default|remove|test`
- [x] 단위 테스트: DPAPI 왕복, 마이그레이션·초기 프로필, 키 삭제 시 연결 해제, 프로필 결정 순서, 캐시 해시에 키 없음, 설정 API가 키를 돌려주지 않음, 가짜 클라이언트로 Claude·Gemini 응답·거절·인증 오류·로컬 대체 확인

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

- [x] (번역 엔진 선택) `/settings/api-keys`(목록에는 `id, provider, label, last4`만), `/settings/translation-profiles`(CRUD, `/{id}/test` 연결 테스트), `/settings/default-profile`, `/settings/models`, `/settings/ui`
- [x] `POST /jobs`, `/jobs/{id}/retranslate`에 `profile_id`. 고른 줄만 다른 엔진으로 다시 번역 가능
- [x] `/system/checks`(점검 카드: 프로필별 키·GGUF 상태 포함), `/system/usage`(실시간 GPU·CPU·RAM). 이벤트에 시각(`t`)을 붙여 진행 로그에 씀

**완료 기준**: HTTP만으로 작업을 만들고, 진행률을 받고, 내보내기까지 할 수 있으며 결과가 CLI와 같음.

> 결과 (2026-11-24): API 테스트 6개(가짜 파이프라인)와 실제 서버로 GPU 작업 1건 통과(`jamak serve --port 0` → 등록 → 진행률 → 자막 조회 → 내보내기, STT 7.0s·번역 9.3s로 CLI와 같은 결과). 보안: 루프백 전용, Host 헤더 검사, 선택적 토큰. 작업 폴더 `meta.json`을 여러 스레드가 동시에 쓰다 Windows에서 충돌하는 문제를 발견해, 경로별 잠금 + 매번 다시 읽고 고쳐 쓰는 방식으로 수정.

## Phase 4 — 웹 UI + Tauri

- [x] `app/` 생성: Vite + React + TS, `pnpm tauri init`(Tauri 2)
- [x] 개발 모드: 백엔드는 `uv run jamak serve`로 따로 띄우고, UI는 환경 변수로 받은 API 주소에 연결
- [x] 화면
  - [x] 홈: 파일 선택(Tauri dialog, 브라우저에서는 경로 입력) + 최근 작업 목록
  - [x] 옵션: 보컬 분리, STT 모델, 번역 모델, 용어집 선택과 편집, 출력 형식
  - [x] 진행: 단계별 진행 막대, 로그, 취소 버튼
  - [x] 검수·편집: 시간 / 원문 / 번역 표, 줄 단위 수정, 여러 줄 골라 다시 번역, 글자 수 초과 경고
  - [x] 저장: 형식 선택, 영상 옆에 저장하고 폴더 열기
  - [x] 시스템 상태: GPU, llama-server, 모델 파일 상태 표시. 문제가 있으면 해결 방법 안내
- [x] Tauri 사이드카 연동
  - 개발 중에는 `uv run`으로 실행, 배포 때는 PyInstaller exe를 `externalBin`으로 실행
  - 빈 포트를 골라 넘기고 `/health`가 응답할 때까지 대기한 뒤 UI에 포트 전달
  - 앱을 종료하면 사이드카도 종료. 비정상 종료 시 남은 프로세스 정리
- [x] 영상을 끌어다 놓아 선택하는 기능
- [x] 화면 구성안([docs/mockups](docs/mockups), [DESIGN.md](DESIGN.md))대로 다시 만들기: 접히는 사이드바와 움직이는 로고, 하단 시스템 모니터, 네온 팔레트 4종, 앱 아이콘
- [x] 설정 › 번역 엔진: API 키 추가(가림 입력, 저장 후 `••••abcd`만)·삭제, 프로필 추가·수정(로컬 내장 / 외부 서버 / Claude / Gemini), 연결 테스트, 기본 프로필 지정
- [x] 작업 옵션에 번역 프로필 선택(기본값 미리 선택, 준비 안 된 프로필은 비활성). 클라우드 프로필을 처음 쓸 때 전송 안내를 한 번 확인받고, 옵션 화면에는 계속 표시
- [x] 진행 화면: 클라우드면 토큰 사용량·예상 비용, 로컬이면 llama-server 상태
- [x] 검수 화면: 고른 줄을 다른 프로필로 다시 번역, 거절·번역 빠짐 필터

**완료 기준**: `pnpm tauri dev`로 띄운 앱에서 영상 선택 → 처리 → 수정 → 저장까지 마우스로만 끝낼 수 있음.

> 결과 (2026-11-24): Tauri 앱이 백엔드를 띄우고 토큰으로 연결함. 브라우저(Playwright)로 경로 입력 → 옵션 → 처리(진행률 WebSocket) → 검수 → 저장 흐름을 실제 GPU 작업으로 확인. 앱을 정상 종료할 때와 강제 종료할 때 모두 백엔드 프로세스가 남지 않음(4개 → 0개). 화면 테스트 중 `/system` 요청 두 개가 동시에 torch를 import하다 보컬 분리가 "설치 안 됨"으로 잘못 나오는 문제를 찾아 한 번만 확인하도록 고침. 남은 것: 진행 메시지 일부가 영어(백엔드 문구), 대화 상자·끌어다 놓기는 사람이 직접 눌러 확인 필요.

## Phase 4.5 — 디자인 맞추기, 용어집, 모델 관리, 클라우드 서비스 확장 (TODO.md에서 옮김)

### 앱을 DESIGN.md에 맞추기

- [x] 로고·워드마크는 팔레트와 관계없는 브랜드 색(`--brand-pink/cyan/ink/night`)으로 고정, 글리치 잔상 2겹(자 + 字)
- [x] 상태 색(ok·warn·err)과 soft·line 파생값을 팔레트마다 다르게(`theme.ts`). "주의" 글자 바탕도 팔레트 값
- [x] 자간은 라틴 대문자에만(`tracked()`가 eyebrow의 라틴 부분만 감쌈), 사이드바 그룹명 자간 제거
- [x] Noto Sans JP 500, 원문 열 500. 번역 편집칸은 `field-sizing: content`
- [x] DESIGN.md: 3.5절 CSS 예시 보강, 파비콘 `rx=1`, 중복 문장 정리
- [x] 팔레트 4종으로 홈·검수·시스템 상태 화면을 찍어 확인(2026-11-25): 로고는 어느 팔레트에서나 핑크·시안 그대로, 상태 배지와 주요 버튼 구분됨, 한글 자간 없음

### 용어집 관리

- [x] 파일 저장 유지(`glossaries/<이름>.json`, v2 형식 `{name, aliases, entries:[{ja, ko, note, hint}]}`, v1 목록도 읽음)
- [x] 설정 › 용어집 화면: 목록, 만들기·이름 바꾸기·복제·삭제, 항목 표, 검색, 중복 경고, 여러 줄 붙여 넣기, 가져오기·내보내기(`.txt`/`.json`/`.csv`)
- [x] 작업 하나에 용어집 여러 개(뒤의 것이 이김), 파일 이름·별칭으로 자동 추천
- [x] 작업 옵션: 고른 용어집을 그 자리에서 고치고 원래 용어집에 저장 / 새 용어집으로 저장 / 이번 작업에만 사용
- [x] 작업에 용어집 이름과 그때 내용을 저장. 용어집이 바뀌면 검수 화면에 "용어집이 바뀌었습니다 · 번역 다시 하기"
- [x] 검수 화면: 원문에서 고른 단어를 "용어집에 추가"
- [x] 묶음마다 그 묶음과 문맥에 나오는 항목만 로컬 프롬프트에 넣음. 인식 힌트는 "힌트" 표시 항목(최대 40개)만. 힌트가 아닌 항목을 고치면 ⑥ 번역부터만 다시 돎

### 모델·엔진 내려받기 (설치 파일은 작게)

- [x] `models.py`: 받을 수 있는 항목 목록(종류, 크기, VRAM, 필요한 실행 환경, GPU일 때만 필요한 항목, 라이선스)
- [x] `installer.py`: 항목별 받기·지우기, 필요한 항목 자동 추가(Whisper → CUDA, Qwen → PyTorch, Gemma → llama-server), 다른 모델이 쓰는 항목과 마지막 음성 인식 모델은 지우지 못하게 막음
- [x] PyTorch 실행 환경: uv(고정 버전, SHA-256 확인)로 Python 3.12 가상 환경을 만들고 해시 고정 목록(`runtime/qwen-requirements.lock`, 51개)으로 설치. `qwen-asr`는 `--no-deps`라 gradio·vllm 등 안 쓰는 의존성은 받지 않음. 이 PC에서 새 환경 설치 후 `torch.cuda.is_available() == True` 확인(4.5GB)
- [x] PyTorch 실행 환경을 빈 폴더에서 앱의 설치 코드(`SetupManager.install(["torch"])`)로 처음부터 받음(2026-11-25): uv 0.13.9 내려받기 → Python 3.12 → 잠금 목록 설치까지 151초, `torch 2.14.1+cu126`, CUDA 사용 가능, 끝난 뒤 `torch-runtime.new`·`uv-cache` 남지 않음
- [x] 받다가 멈춘 흔적 정리: 앱이 시작할 때 `bin/*.new`, `downloads/uv-cache`, Hugging Face의 `*.incomplete`(프로세스마다 이름이 달라 이어받지 못함)를 지우고, 교체 도중 끊긴 `*.old`는 되돌림. PyTorch 설치가 실패하거나 취소되면 staging 폴더를 바로 지움. `.part`(이어받기용)는 남김
- [x] 처음 실행 화면: FFmpeg + 음성 인식 모델 하나(VRAM 8GB 이상이면 large-v3, 아니면 turbo를 미리 고름), 로컬 번역은 선택, 클라우드만 쓰면 API 키 등록으로 안내, 남은 디스크 확인
- [x] 설정 › 모델 관리 화면: 크기·VRAM·필요 항목·라이선스, 받기(무엇을 함께 받는지 미리 보여 줌)·지우기·진행률·멈추기, 이 PC의 VRAM보다 큰 모델 경고
- [x] 작업 중에 모델을 몰래 받지 않음: 받지 않은 모델이면 작업 생성이 409, 옵션 화면은 "받기 필요"와 모델 관리 링크
- [x] 로컬 번역이 없으면 로컬 프로필은 "준비 안 됨", 첫 클라우드 프로필이 기본 프로필이 됨. 시스템 상태 점검도 로컬 번역을 "선택"으로 표시

### 음성 인식 모델

- [x] **Qwen3-ASR 1.7B 추가**: 백엔드는 VAD로 구간을 나누고, PyTorch 환경의 `runtime/qwen_asr_worker.py`를 별도 프로세스로 실행(진행률은 JSON 줄, 취소 시 종료, Job Object로 묶음). 단어 시간은 Qwen3-ForcedAligner, 정렬 단어에 구두점을 되돌려 분할 규칙이 그대로 동작. 용어집 인식 힌트를 `context`로 넘김. 중국어 출력(가나 없음)·"テスト" 헛말은 버림. 남은 VRAM에 맞춰 묶음 크기를 줄임
  - 결과 (2026-11-25): 영화 3분 구간 14구간을 18.9초에, 단어 시간과 구두점 포함. 패키징 spec에 작업 스크립트와 잠금 파일 포함
- [x] **kotoba-whisper 충돌**: CTranslate2 변환본의 `alignment_heads`가 디코더 2층 모델에 없는 층(최대 25)을 가리켜 단어 시간 계산 중 segfault. 받은 뒤(그리고 예전에 받은 모델은 쓸 때) 마지막 층 20개 헤드로 고침. 단어 시간을 켜고 3분 구간 정상(120단어, 4.3초)
- [x] Qwen3-ASR 인식 힌트 형식 시험(2026-11-25, 영화의 이름이 나오는 15구간, 형식 5가지: 없음 / 지금 형식 `登場人物・用語：…` / 경칭 붙임 / 읽기 병기 / 작품 설명 문장). 힌트를 넣으면 しんのすけ 0→5, シロ 0→2, マサオ 0→2로 좋아지지만 **風間은 어느 형식에서도 クゼマ**로 남음. 문장 형식은 1인칭 「オラ」를 하나 더 맞혔을 뿐 나머지는 같아 지금 형식을 유지. 소리가 비슷한 이름은 인식 후 교정으로 다룸(TODO 1장)
- [x] Whisper 계열은 작업 중 다운로드 대신 받아 둔 폴더를 직접 엶. STT 캐시 해시는 Whisper에 대해 예전과 같아 기존 작업 캐시가 유지됨

### 클라우드 번역 서비스

- [x] `core/providers.py` 한곳에 서비스 목록: Anthropic, Google Gemini, OpenAI, DeepSeek, xAI, Mistral, OpenRouter, DeepL, NAVER Papago. `store.py`·`factory.py`·CLI·`checks.py`가 이 목록을 따름
- [x] OpenAI 형식 5곳: 주소 자동, 모델 목록은 `/models`에서 조회, 서비스별 차이(OpenAI `max_completion_tokens`·temperature 생략, DeepSeek `json_object`), 402·400·한도 초과는 바로 멈춤, `content_filter`는 거절로 처리
- [x] DeepL·Papago 번역기(`mt_api.py`): 줄 단위, 받은 줄 수 확인, 용어집은 번역 전 한국어로 치환(DeepL은 `<keep>`로 보존), 글자 수 사용량을 진행 화면에 표시. Papago는 묶음을 줄바꿈으로 합쳐 보내고 줄 수가 안 맞으면 한 줄씩
- [x] 키 화면: 서비스별 키 발급·요금 페이지 열기, Papago는 Client ID·Secret 두 칸, 저장하면 바로 키 확인(모델 목록 / DeepL 사용량). 오류 메시지에서도 키를 가림
- [x] 실패 시 대체: 프로필에 대신 쓸 클라우드 프로필 지정. 한도·인증 오류나 실패한 줄을 그 프로필로 마저 번역(`cloud_fallback` 표시). 거절 줄의 로컬 재번역은 그대로
- [x] 가격은 코드에 넣지 않음(Claude의 기존 예상 비용만 유지). 키 원문은 로그·meta.json·캐시·API 응답에 없음(DPAPI)

**완료 기준**: 백엔드 단위 테스트 87개 통과(GPU 테스트 2개 제외)(용어집, 설치 관리자, Qwen 연결부, 서비스 목록·DeepL·Papago·대체 번역·키 확인). `tsc`·oxlint 통과. 빈 설정 폴더로 띄운 앱에서 처음 실행·모델 관리·번역 엔진 화면을 Playwright로 찍어 확인.

> 아직 실제로 확인하지 못한 것: 새 서비스 7곳은 가짜 응답으로만 시험함(키 없음). 실제 키로 연결 테스트 필요. PyTorch 환경 설치는 이 PC의 uv로 확인했고, 앱의 받기 버튼으로 처음부터 받는 흐름(uv 내려받기 포함)은 사람이 한 번 눌러 봐야 함.

## Phase 4.6 — Mac 지원 코드 (Apple Silicon, TODO.md에서 옮김)

Mac이 없는 상태에서 먼저 만들어 둔 부분입니다. 실제 Mac 확인은 TODO.md 4장에 남아 있습니다.

- [x] **플랫폼 구분** (`hw.py`): Windows·Mac별 모델 목록(`Spec.platforms`), 가속 확인(`cuda`/`metal`/`cpu`), Apple 칩 이름과 통합 메모리(`sysctl hw.memsize`). 모델 메모리는 통합 메모리의 70%로 계산(Metal이 GPU에 주는 몫)
- [x] **음성 인식** (`core/stt/mlx_whisper.py`): mlx-whisper 0.4.3. 같은 모델 id(`large-v3`, `large-v3-turbo`, `kotoba-whisper-v2.0`)를 Mac에서는 MLX 변환본으로 받음(mlx-community 2개, kotoba는 `kaiinui` 변환본). 결과 형식이 openai-whisper와 같아 분할·환각 필터를 그대로 씀. 빔 서치가 없어(`NotImplementedError`) 넘기지 않음. VAD는 Qwen과 같은 Silero 구간을 30초 단위로 묶어 `clip_timestamps`로 넘김. 진행률과 취소는 mlx-whisper 안의 tqdm을 바꿔 끼워 받음. 캐시 키(`stt_identity`)에 엔진이 들어가 Windows 결과와 섞이지 않음
- [x] **Mac에서 숨김**: CUDA 라이브러리, PyTorch 실행 환경, Qwen3-ASR. Whisper 항목의 `gpu_requires`도 비움
- [x] **llama-server Metal 빌드**: `llama-b12675-bin-macos-arm64.tar.gz`(16.8MB, GitHub 공개 SHA-256). 맨 위 폴더를 벗기고 dylib 심볼릭 링크째 풀기(`filter="data"`)
- [x] **FFmpeg**: BtbN에는 Mac 판이 없어 martin-riedl.de의 9.1 빌드(서명된 정적 빌드, GPL)를 받음. 같은 서버의 `.sha256`을 믿지 않고 해시와 크기를 코드에 고정. zip을 풀며 실행 권한을 붙임. `audio._tool`이 `.exe`를 붙이지 않게 수정
- [x] **API 키**: macOS 로그인 키체인(`keyring`의 macOS 백엔드, Security.framework 직접 호출이라 키가 명령줄에 나오지 않음). DB에는 `keychain:<임의 id>`만 저장, 키를 지우면 키체인 항목도 지움. 오류 메시지에 키 원문이 들어가지 않게 예외를 감쌈. Windows에서 만든 DB를 Mac에서 열면 "다시 등록" 안내
- [x] **하위 프로세스**: Job Object 대신 띄운 프로세스를 기억했다가 정상 종료(atexit)와 부모 감시 종료 때 kill
- [x] **경로·화면**: 데이터 폴더 `~/Library/Application Support/jamak_converter`, Finder에서 보기(`open -R`), 시스템 점검의 GPU·CUDA 줄, 처음 실행·모델 관리의 "GPU 메모리" 문구를 Mac에서는 "모델에 쓸 수 있는 메모리"로. `/setup`·`/system`에 `platform`, `accelerator` 추가
- [x] **의존성**: `mlx-whisper`·`keyring`은 Mac에만, NVIDIA 휠은 Windows에만(`pyproject.toml` 표시). uv 잠금 파일을 Windows와 macOS arm64 두 환경으로 풂. mlx 휠이 macOS 14부터라 최소 버전 14.0
- [x] **빌드**: `tauri.macos.conf.json`(`.app`, `.dmg`), 사이드카 이름(`lib.rs`의 `SIDECAR`), PyInstaller spec의 Mac 분기(mlx dylib·metallib, mlx_whisper 자산, torch 제외), `scripts/build_app.sh`. 두 빌드 스크립트 모두 묶은 백엔드에서 `jamak doctor --bundled`(음성 인식 엔진을 불러오는지)를 확인
- [x] **CI**: `.github/workflows/test.yml`. Windows·macos-14에서 ruff, pytest, `doctor --bundled`, UI `tsc`. 테스트는 Mac에서도 실제 키체인을 건드리지 않음(conftest)

**완료 기준**: 백엔드 단위 테스트 107개 통과(GPU 테스트 2개 제외). Mac 경로 테스트 17개(`tests/test_mac.py`)는 플랫폼을 바꿔 끼우고 가짜 mlx-whisper·키체인으로 Windows에서 실행. Windows 동작은 그대로(`/setup` 목록·추천 모델 동일). `tsc`, `cargo check` 통과.

> 아직 확인하지 못한 것: 실제 Mac에서 한 번도 돌려 보지 않았고, CI도 아직 돌리지 않음(push 전). PyInstaller Mac 묶음과 `.dmg`도 만들어 보지 않음.

## Phase 5 — 배포 (필요할 때)

- [x] 백엔드를 PyInstaller로 묶기(onedir, `backend/packaging/jamak-backend.spec`). 264MB. CUDA DLL은 넣지 않음(아래)
- [x] 모델은 처음 실행할 때 내려받기(진행률 표시), 저장 위치는 `%LOCALAPPDATA%`
- [x] ~~llama-server와 CUDA DLL을 앱에 함께 배포~~ → **처음 실행할 때 내려받기로 변경**. 함께 넣으면 설치 파일이 2GB를 넘어(cuBLAS+cuDNN 1.25GB, llama-server 0.7GB) NSIS 한도에 걸리고, NVIDIA·FFmpeg 바이너리를 우리가 재배포하게 됨. 배포처(GitHub 릴리스, PyPI, Hugging Face)가 공개한 SHA-256으로 확인하고, 끊기면 이어 받음(`backend/src/jamak/installer.py`)
- [x] FFmpeg: 재배포하지 않고 처음 실행 때 BtbN의 **LGPL** 공유 빌드를 받음. PATH에 FFmpeg가 이미 있으면 그대로 씀
- [x] Tauri 번들러로 NSIS 설치 파일 생성(사용자 단위 설치, 관리자 권한 불필요). `scripts/build_app.ps1` 한 번으로 백엔드와 앱을 함께 빌드
- [ ] (선택) GPU가 없을 때 CPU로 대체 실행. 경고 문구는 표시하지만 GPU 없는 PC에서 실제로 돌려 보지는 않음

**완료 기준**: 개발 도구가 없는 Windows PC에서 설치 후 영상 1편 처리 성공.

> 결과 (2026-11-24): 설치 파일 77.7MB(`jamak_0.1.0_x64-setup.exe`). 이 PC에 조용히 설치(`/S`)·덮어 설치 모두 정상, 설치 위치 `%LOCALAPPDATA%\jamak`. 설치한 앱이 묶인 백엔드를 띄우고 처음 실행 화면에서 빠진 항목(CUDA 라이브러리)을 정확히 표시함. 내려받기 기능은 실제 배포처에서 전부 확인: llama-server 0.7GB, cuBLAS·cuDNN 1.25GB, FFmpeg 78MB, Whisper·GGUF(작은 파일로 대신 확인). 설치된 백엔드 exe가 내려받은 FFmpeg·CUDA DLL만으로 GPU 작업을 끝까지 처리(STT 6.3s, 번역 8.7s, 8줄 모두 번역). 이어 받기·해시 불일치·취소는 단위 테스트로 확인.
> 남은 확인: **개발 도구와 CUDA가 없는 다른 PC**에서 설치부터 처음 실행 내려받기(약 12GB)까지 사람이 직접 해 봐야 함. 이 PC에는 드라이버·Python·FFmpeg가 이미 있어 완전히 깨끗한 환경은 아님. 앱을 지워도 내려받은 파일(`%LOCALAPPDATA%\jamak_converter`)은 남음.

## Phase 6 — 샘플 영상 검증 (가장 마지막)

실제 애니·드라마 영상으로 품질을 측정하고 기본값을 확정합니다. 결과는 `docs/benchmark.md`에 기록합니다.

- [ ] **기준 샘플 세트** 만들기 (`samples/`는 git에서 제외)
  - 애니 2편, 드라마 1편에서 각 3~5분짜리 구간: 대사가 많은 장면, 배경음악이 큰 장면, 오프닝곡 포함
  - 가능하면 공식 일본어 자막을 정답으로 확보해 인식 오류율(CER) 계산에 씀
  - 처리 시간 측정용으로 전체 길이 영상(24분 1편, 2시간 1편)도 준비
- [ ] STT 비교: `large-v3`, `large-v3-turbo`, `kotoba-whisper-v2.0`의 CER과 처리 속도
- [ ] 보컬 분리 효과: 배경음악이 큰 구간에서 Demucs 켜기 / 끄기에 따른 CER 차이
- [ ] 번역 비교: GGUF 후보 2~3개와 클라우드 모델(Claude Opus·Sonnet·Haiku 5.5, Gemini)을 직접 읽어 보고 오역, 말투, 이름 표기, 처리 속도, 한 화당 비용, 거절 빈도를 점검. 클라우드 기본 모델과 `effort` 확정
- [ ] 병렬 번역: 순차와 병렬의 품질 차이와 속도
- [ ] 분할 규칙(길이, 글자 수, 쉼)과 환각 필터 임계값 조정
- [ ] 전체 처리 시간 측정(24분, 2시간) → 실측값을 README에 추가
- [ ] 측정 결과로 기본값 확정하고 설정 파일에 반영

**완료 기준**: `docs/benchmark.md`에 측정 결과와 확정된 기본값이 정리되고, 기준 샘플 세트를 기본값으로 처리했을 때 검수에서 고칠 줄이 납득할 수준임.

---

## 이후 고려 (백로그)

- 화자 구분(pyannote): ASS에서 화자별로 색을 다르게 하고, 화자별 말투를 번역에 반영
- 여러 파일 / 폴더 단위 일괄 처리
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
| API 키 유출 | 요금 피해 | DPAPI로 암호화 저장, 화면에는 끝 4자리만, 키를 로그·`meta.json`·캐시 해시·API 응답에 넣지 않음(테스트로 확인). 서버는 루프백 + 토큰. 같은 Windows 사용자로 도는 프로그램은 복호화할 수 있다는 한계는 README에 적음 |
| 클라우드 거절·요금 한도 | 일부 줄 번역 빠짐, 작업 지연 | 거절된 줄만 표시하고 로컬 모델로 다시 번역(기본), 429·5xx는 SDK 재시도, 인증 오류는 바로 멈춤. 검수 화면의 "번역 빠짐·거절" 필터로 확인 |
| 외부 전송에 대한 사용자 인지 | 모르고 대사를 외부로 보냄 | 기본은 로컬. 클라우드 프로필을 처음 쓸 때 안내 확인, 옵션·설정 화면에 전송 범위(일본어 대사 텍스트만) 상시 표시 |
