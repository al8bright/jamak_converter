# 공통 구조 — 네 앱이 같이 쓰는 뼈대

jamak_converter, youtube_down, video_converter, audio_converter 네 저장소를 같은 구조로 유지하기 위한 기준 문서입니다.
이 문서가 기준이고, 나머지 세 저장소의 문서는 이 문서를 가리킵니다. 구조를 바꿀 때는 여기를 먼저 고칩니다.

- 작성: 2026-11-26
- 결정: 두 변환기는 합치지 않고 저장소를 따로 둡니다. 공통 코드는 패키지로 빼지 않고 **같은 파일을 네 곳에 둡니다**.

---

## 1. 대상

| | jamak_converter | youtube_down | video_converter | audio_converter |
|---|---|---|---|---|
| 하는 일 | 일본어 영상 → 한국어 자막 | 유튜브 음악 받기 | NVENC 영상 일괄 변환 | 오디오 형식·음질 변환 |
| 상태 | 이 구조 | 이 구조 (2.0.0) | CustomTkinter, 전환 예정 | CustomTkinter, 전환 예정 |
| 백엔드 패키지 | `jamak` | `utube` | `videoconverter` | `audioconverter` |
| 환경 변수 접두사 | `JAMAK_` | `UTUBE_` | `VIDEOCONVERTER_` | `AUDIOCONVERTER_` |
| 백엔드 포트(개발) | 8765 | 8766 | 8767 | 8768 |
| Vite 포트 | 5173 | 5174 | 5175 | 5176 |
| 설치 이름 | jamak | YoutubeDownloader | VideoConverter | AudioConverter |
| 데이터 폴더 | `%LOCALAPPDATA%\jamak_converter` | `%APPDATA%\YoutubeDownloader` | `%APPDATA%\VideoConverter` | `%APPDATA%\AudioConverter` |
| 플랫폼 | Windows, Mac(코드만) | Windows | Windows | Windows |
| FFmpeg | 처음 실행 때 내려받기 | 설치 파일에 포함 | 설치 파일에 포함 | 설치 파일에 포함 |

포트를 앱마다 다르게 두는 이유는 두 앱을 동시에 개발 실행할 수 있게 하기 위해서입니다.

---

## 2. 폴더 구조

```
<repo>/
├─ app/                       # 화면과 창
│  ├─ src/                    # React 19 + TypeScript
│  │  ├─ main.tsx  App.tsx    # 진입점, 틀(사이드바·하단 상태 줄·닫기 확인)
│  │  ├─ api.ts               # 백엔드 연결(connect·api·events)과 응답 타입
│  │  ├─ ctx.ts               # 앱 전체 상태(Context)
│  │  ├─ ui.tsx               # 공통 부품(Icon, Modal, Bar …)
│  │  ├─ index.css            # 디자인 토큰(:root)과 클래스
│  │  └─ <Screen>.tsx         # 화면 하나에 파일 하나
│  ├─ src-tauri/              # Tauri 2 셸
│  │  ├─ src/lib.rs  main.rs  # 백엔드 실행·토큰·종료
│  │  ├─ build.rs             # 빈 backend 폴더를 만들어 개발 빌드가 되게 함
│  │  ├─ capabilities/default.json
│  │  └─ tauri.conf.json
│  ├─ package.json  vite.config.ts  tsconfig*.json  .oxlintrc.json
├─ backend/
│  ├─ src/<pkg>/
│  │  ├─ __init__.py  cli.py  config.py
│  │  ├─ api/server.py        # HTTP·WebSocket (화면이 쓰는 유일한 통로)
│  │  └─ core/                # 실제 일. proc.py(프로세스 수명)는 공통
│  ├─ tests/
│  ├─ packaging/entry.py  <pkg>-backend.spec
│  ├─ pyproject.toml  uv.lock  .python-version
├─ scripts/build_app.ps1      # 설치 파일 빌드
├─ scripts/collect_licenses.py
├─ .github/workflows/ci.yml
├─ README.md  DESIGN.md  PLAN.md  LICENSE  THIRD_PARTY_NOTICES.md
└─ .gitignore  .gitattributes
```

---

## 3. 파일 세 종류

### (가) 네 곳이 같은 파일

앱 이름(패키지, 환경 변수, 포트)만 바꾸면 내용이 같아야 하는 파일입니다. 한 곳에서 고치면 나머지 세 곳에도 옮깁니다.

| 파일 | 내용 |
|---|---|
| `app/src-tauri/src/lib.rs`, `main.rs`, `build.rs` | 백엔드 찾기(환경 변수 → 묶음 → `uv run`), 임의 토큰, `--port 0 --parent-pid`, `<PKG>_LISTENING` 읽기, 종료 |
| `app/src-tauri/capabilities/default.json` | `core:default`, `core:window:allow-destroy`, 필요한 대화 상자 권한 |
| `app/src/api.ts`의 연결부 | `inTauri`, `connect`, `ApiError`, `api.get/post/put/patch/del` |
| `app/src/ui.tsx`의 기본 부품 | `Icon`, `Modal`, `Bar`, `Chip`, `Check`, `Empty`, `Ellipsis` |
| `app/src/index.css`의 토큰과 기본 규칙 | `:root` 팔레트·서체·크기, 버튼·입력·모달·배지·진행 바 |
| `app/.oxlintrc.json`, `tsconfig*.json`, `vite.config.ts` | 포트만 다름. `woff2Only` 플러그인 포함 |
| `backend/src/<pkg>/core/proc.py` | Job Object, 부모 감시, 빈 포트, 하위 프로세스 종료 |
| `backend/src/<pkg>/api/server.py`의 뼈대 | 허용 Host·Origin, 토큰 검사, `/health`, `serve()` |
| `backend/src/<pkg>/cli.py`의 뼈대 | `serve`, `doctor [--bundled]`, `--version` |
| `backend/packaging/entry.py` | PyInstaller 진입점 |
| `backend/pyproject.toml`의 도구 설정 | ruff·pytest 설정, `uv_build`, dev 의존성 |
| `.github/workflows/ci.yml` | 백엔드(uv sync → ruff → pytest), 화면(lint → build → `cargo check`) |
| `scripts/collect_licenses.py`, `.gitattributes`, `LICENSE` | |

`api.ts`·`ui.tsx`·`index.css`·`server.py`·`cli.py`는 파일 안에 "여기부터 앱 고유" 경계를 주석 한 줄로 표시하고, 그 위쪽만 같게 유지합니다.

### (나) 모양은 같고 내용은 앱마다 다른 파일

| 파일 | 같게 두는 것 |
|---|---|
| `App.tsx` | 사이드바(펼침 200px / 접힘 68px), 하단 상태 줄, 시작 화면, 닫기 확인 창 |
| `tauri.conf.json` | NSIS, 현재 사용자 설치, 한국어·영어, `resources`에 `backend/` |
| `scripts/build_app.ps1` | 순서: 백엔드 묶기 → 도구 넣기 → `doctor --bundled` → Tauri 빌드 |
| `<pkg>-backend.spec` | onedir, 콘솔 없음 |
| `README.md` | 빠른 시작 → 기능 → 화면 → 기술 스택 → 소스로 실행 → 파일 구성 → 라이선스 |
| `DESIGN.md` | 원칙 → 화면 구성 → 토큰 → 부품 |
| `tests/conftest.py` | 임시 데이터 폴더 fixture |

### (다) 앱 고유

`core/`의 나머지, `api/server.py`의 경로들, 화면 파일, `config.py`의 설정 항목.

---

## 4. 공통 규칙

### 4.1 화면과 백엔드

- 화면은 HTTP와 WebSocket으로만 백엔드와 통신합니다. Tauri 명령은 `backend_info` 하나입니다.
- 백엔드는 `127.0.0.1`에만 열고, Host 헤더와 토큰(`X-<Pkg>-Token`, WebSocket은 `?token=`)을 검사합니다.
- **상태 전달은 두 방식**이 있고, 새 앱은 두 번째를 씁니다.
  - 작업 폴더 방식(jamak): 영상마다 폴더와 단계별 캐시가 있고, 작업별 이벤트를 받습니다. 한 작업이 길고 단계가 여럿일 때 맞습니다.
  - 상태 스냅숏 방식(youtube_down): `Service` 하나가 대기열 전체를 메모리에 들고, 바뀔 때마다 버전을 올립니다. `/events`가 0.1초 간격으로 바뀐 스냅숏만 보냅니다. 항목이 많고 각 항목이 짧은 변환기에 맞습니다.
- 사용자에게 보이는 문구(오류 포함)는 한국어로 씁니다.

### 4.2 프로세스와 종료

- 백엔드는 `--parent-pid`로 앱을 지켜보다 앱이 사라지면 스스로 끝납니다.
- 백엔드가 띄운 프로세스(FFmpeg, llama-server)는 Windows Job Object로 묶어 백엔드와 함께 끝나게 합니다.
- **작업 중에 창을 닫으면 앱 안 확인 창을 띄웁니다.** 기본 선택은 "계속 작업"입니다.
- **중간에 끝난 작업은 이어서 하지 않습니다.** 만들다 만 출력은 다음 시작 때 지웁니다(jamak: 작업 폴더, 변환기: 임시 출력 파일). 원본은 건드리지 않습니다.

### 4.3 외부 도구

- FFmpeg는 설치 파일에 넣습니다(jamak만 처음 실행 때 내려받습니다. 모델과 함께 받는 구조가 이미 있어서입니다).
- 빌드 스크립트는 **정해 둔 빌드를 주소와 SHA-256으로 받아** `backend\.tools`에 두고 씁니다. 빌드하는 PC에 깔린 FFmpeg에 따라 결과가 달라지지 않게 하기 위해서입니다.
- 함께 배포하는 것의 라이선스는 `THIRD_PARTY_NOTICES.md`와 설치 폴더의 `licenses`에 넣습니다.

### 4.4 디자인

- jamak의 네온 디자인(서울 나이트 팔레트)을 같이 씁니다. 토큰은 `index.css`의 `:root`에만 정의합니다.
- 컴포넌트 코드에는 색·서체·간격 값을 쓰지 않습니다.
- 브라우저 기본 대화 상자(`alert`, `confirm`)와 운영체제 확인 창은 쓰지 않고 `Modal`을 씁니다. 파일·폴더 선택만 운영체제 창을 씁니다.

### 4.5 검사

- 백엔드: `uv run ruff check`, `uv run pytest`. 외부 도구(FFmpeg, yt-dlp, GPU)는 가짜로 바꿔 몇 초 안에 끝나게 합니다. 진짜 도구를 쓰는 테스트는 표시(marker)를 붙여 따로 돌립니다.
- 화면: `pnpm lint`(oxlint), `pnpm build`(tsc + vite), `cargo check --locked`.
- CI는 push와 PR마다 위 검사를 돌립니다. 설치 파일은 CI에서 만들지 않습니다.

---

## 5. 지금의 차이 (2026-11-26)

이미 전환된 두 앱도 공통 파일이 서로 달라져 있습니다. 앱 이름을 같게 치환한 뒤 비교한 줄 수입니다.

| 파일 | jamak | youtube_down | 다른 줄 | 주된 차이 |
|---|---|---|---|---|
| `lib.rs` | 206 | 189 | 41 | 오류 문구 언어, Mac 종료 절차(jamak만), `VIRTUAL_ENV` 제거(youtube만) |
| `ui.tsx` | 141 | 110 | 147 | 부품 구성이 다름(`Page`·`Note` / `Screen`·`Chip`·`Bar`) |
| `index.css` | 444 | 365 | 671 | 토큰 이름과 클래스가 다름 |
| `api.ts` 연결부 | 84 | 89 | 약 20 | 주석 언어, `patch`, 오류 문구 |
| `proc.py` | 143 | 207 | 258 | jamak은 하위 프로세스를 묶고, youtube는 자신을 묶고 FFmpeg를 찾아 끊음 |
| `build_app.ps1` | 42 | 151 | 135 | youtube는 FFmpeg·Deno를 넣고 라이선스를 모음 |
| CI | `test.yml` | `ci.yml` | — | jamak은 Mac 러너 포함, `cargo check`·lint 없음. youtube는 Windows만 |

한쪽에만 있는 것:

- jamak에만: Mac 지원, `.gitattributes`, `tests/__init__.py`, 중단된 작업 삭제(`purge_interrupted`), 테마 4종.
- youtube_down에만: `THIRD_PARTY_NOTICES.md`·`collect_licenses.py`, `woff2Only`, `package.json`의 `tauri` 스크립트, 고정된 Vite 포트, 한국어 셸 오류 문구.

---

## 6. 진행 순서

1. **공통 파일 확정** — (가) 목록의 파일을 jamak과 youtube_down에서 하나로 맞춥니다. 각 파일마다 두 쪽의 좋은 점을 합친 판을 만들고 두 저장소에 넣습니다. 두 앱 모두 동작이 바뀌지 않아야 하므로 실제 앱으로 확인합니다.
2. **video_converter 전환** — 1번에서 확정한 파일로 시작합니다. 계획은 video_converter의 `PLAN.md`.
3. **audio_converter 전환** — video_converter의 FFmpeg 실행기와 대기열을 가져다 씁니다. 계획은 audio_converter의 `PLAN.md`.
4. **점검 스크립트** — `scripts/check_structure.py`: 옆 폴더의 세 저장소와 (가) 파일을 비교해 달라진 곳을 알려 줍니다. 네 저장소에 같은 스크립트를 둡니다.

1번을 먼저 하는 이유: 공통 파일이 정해지지 않은 채 두 앱을 더 만들면 맞춰야 할 판이 네 개로 늘어납니다.

---

## 7. 정한 것 (2026-11-26)

| | 결정 |
|---|---|
| 주석 언어 | (가) 파일은 한국어로 통일. 앱 고유 코드는 저장소마다 지금 언어를 유지 |
| 커밋 메시지 | 저장소마다 지금 방식 유지 (jamak은 영어 한 줄, youtube_down은 `[Structural]`/`[Behavioral]` + 한국어) |
| `ui.tsx`·`index.css` 기준 | youtube_down 쪽을 기준으로 하고 jamak에만 있는 부품(`Page`, `Note`, `SettingsTabs`, 토스트)을 더함 |
| jamak의 FFmpeg | 처음 실행 때 내려받는 방식 유지 |
| Mac | (가) 파일에는 Mac 분기를 그대로 두고, 나머지 세 앱은 Windows만 빌드 |
| 오디오 동시 변환 | audio_converter 전환 때 정함 |
