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
│  │  ├─ appInfo.ts           # 앱 고유 값: 이름, 개발용 포트
│  │  ├─ backend.ts           # 백엔드 연결(connect·api·socket). 공통
│  │  ├─ api.ts               # 이벤트와 응답 타입(앱 고유)
│  │  ├─ ctx.ts               # 앱 전체 상태(Context)
│  │  ├─ common.tsx           # 공통 부품(Icon, Page, Screen, Modal, Bar …). 공통
│  │  ├─ base.css             # 디자인 토큰(:root)과 공통 클래스. 공통
│  │  ├─ ui.tsx               # 앱 부품(로고, 아이콘 모음 …). common.tsx를 다시 내보냄
│  │  ├─ index.css            # 앱 클래스. 맨 앞에서 base.css를 불러옴
│  │  └─ <Screen>.tsx         # 화면 하나에 파일 하나
│  ├─ src-tauri/              # Tauri 2 셸
│  │  ├─ src/lib.rs  main.rs  # 백엔드 실행·토큰·종료. 공통
│  │  ├─ src/app_info.rs      # 앱 고유 값: 환경 변수 접두사, 명령 이름, 시작 대기 시간
│  │  ├─ build.rs             # 빈 backend 폴더를 만들어 개발 빌드가 되게 함. 공통
│  │  ├─ capabilities/default.json
│  │  └─ tauri.conf.json
│  ├─ package.json  vite.config.ts  tsconfig*.json  .oxlintrc.json
├─ backend/
│  ├─ src/<pkg>/
│  │  ├─ __init__.py  cli.py  config.py
│  │  ├─ app_info.py          # 앱 고유 값: 이름, 환경 변수 접두사, 포트
│  │  ├─ api/base.py          # 보안(Host·토큰·CORS), /health, serve. 공통
│  │  ├─ api/server.py        # HTTP·WebSocket 경로 (화면이 쓰는 유일한 통로)
│  │  └─ core/                # 실제 일. proc.py(프로세스 수명)는 공통
│  ├─ tests/                  # test_common.py는 공통
│  ├─ packaging/entry.py  <pkg>-backend.spec
│  ├─ pyproject.toml  uv.lock  .python-version
├─ scripts/build_app.ps1      # 설치 파일 빌드
├─ scripts/collect_licenses.py  check_structure.py
├─ .github/workflows/ci.yml   # 공통 검사. 앱 고유 검사는 extra.yml
├─ README.md  DESIGN.md  PLAN.md  LICENSE  THIRD_PARTY_NOTICES.md
└─ .gitignore  .gitattributes
```

---

## 3. 파일 세 종류

### (가) 네 곳이 같은 파일

네 저장소에서 **글자 하나까지 같은** 파일입니다(줄 끝만 무시). 앱마다 다른 이름·포트는 파일에 쓰지 않고
층마다 하나씩 있는 앱 고유 파일에서 읽습니다.

| 층 | 앱 고유 값 | 읽는 공통 파일 |
|---|---|---|
| 백엔드 | `backend/src/<pkg>/app_info.py` (`NAME`, `ENV`, `PORT`, `VITE_PORT`) | `api/base.py`, `tests/test_common.py` |
| Tauri | `app/src-tauri/src/app_info.rs` (`ENV`, `CLI`, `SIDECAR`, `STARTUP_SECS`) | `lib.rs` |
| 화면 | `app/src/appInfo.ts` (`NAME`, `DEV_BACKEND_PORT`, `VITE_PORT`) | `backend.ts`, `vite.config.ts` |

| 파일 | 내용 |
|---|---|
| `app/src-tauri/src/lib.rs`, `main.rs`, `build.rs` | 백엔드 찾기(환경 변수 → 묶음 → `uv run`), 임의 토큰, `--port 0 --parent-pid`, `<ENV>_LISTENING` 읽기, Mac은 SIGTERM 후 3초 기다려 종료. Cargo의 `[lib] name`은 네 앱 모두 `app_lib` |
| `app/src/common.tsx` | `Icon`(굵기는 `--icon-stroke`), `tracked`, 화면 뼈대 `Page`(통째로 스크롤)·`Screen`(목록만 스크롤 + 하단 동작 바), `Chip`, `Check`, `Bar`, `Empty`, `Note`, `Ellipsis`, `Modal`, `useToast` |
| `app/src/base.css` | `:root` 토큰(서울 나이트 팔레트·서체·크기), 틀·사이드바, 머리글·패널, 카드, 버튼(`primary`·`cyan`·`danger`·`sm`), 칩, 배지, 진행 바, 알림 줄, 빈 상태, 토스트, 모달, 시작 화면 |
| `app/src/backend.ts` | `inTauri`, `connect`, `ApiError`, `api.get/post/put/patch/del`, `socket(경로, 받기, 연결 상태)`. 브라우저 개발 때 주소는 `VITE_BACKEND_URL` |
| `app/.oxlintrc.json`, `tsconfig*.json`, `vite.config.ts`, `.gitignore`, `src-tauri/.gitignore` | `woff2Only` 플러그인, 고정 Vite 포트, `src-tauri` 감시 제외 |
| `backend/src/<pkg>/api/base.py` | 허용 Host·Origin, 토큰 검사(`X-<Name>-Token`, WebSocket `?token=`), `/health`, `serve` 명령과 `serve()` |
| `backend/src/<pkg>/core/proc.py` | Job Object(자신 묶기 / 자식 하나 묶기), 부모 감시, 빈 포트, 하위 프로세스·FFmpeg 종료, 기본 프로그램으로 열기 |
| `backend/tests/test_common.py` | 위 두 파일의 테스트 |
| `backend/packaging/entry.py` | PyInstaller 진입점. 앱 이름이 들어 있는 유일한 공통 파일(점검 스크립트가 이름을 바꿔 비교) |
| `.github/workflows/ci.yml` | 백엔드(uv sync → ruff → pytest), 화면(lint → build → `cargo check`). Windows만 |
| `scripts/collect_licenses.py`, `scripts/check_structure.py`, `.gitattributes`, `LICENSE` | |

목록은 `scripts/check_structure.py`의 `SHARED`가 기준입니다. 공통 파일을 고치면 그 스크립트의 `--copy-from`으로
다른 저장소에 옮기고, 각 저장소에서 스크립트를 돌려 "모두 같음"을 확인합니다.

화면 쪽은 `ui.tsx`가 `export * from "./common"`으로 공통 부품을 다시 내보내므로 화면 파일은 `./ui`에서만 가져옵니다.
`index.css`는 첫 줄에서 `base.css`를 불러오고, 앱에서 값을 바꿀 때는 같은 선택자를 뒤에 다시 씁니다
(예: youtube의 `--icon-stroke: 1.8`, jamak의 작은 버튼 좌우 여백).
`.field`·`.check`·`.row`·`.cols`는 두 앱에서 뜻이 달라 공통에 넣지 않았습니다. 새 앱은 youtube_down의 뜻을 따릅니다.

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
| `app/src-tauri/capabilities/default.json` | `core:default`, `core:window:allow-destroy`, 그 앱에 필요한 대화 상자 권한만 |
| `api/server.py`, `cli.py` | 보안·`serve`는 `base.py`에 맡긴다(`base.secure`, `base.ws_allowed`, `add_serve_command`) |
| `.github/workflows/extra.yml` | 있으면 그 앱만의 검사(jamak: macOS 러너, `doctor --bundled`) |
| `THIRD_PARTY_NOTICES.md` | 처음 실행 때 받는 것 → 백엔드 → 앱 창 순서의 표 |

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

- jamak의 네온 디자인(서울 나이트 팔레트)을 같이 씁니다. 토큰은 `base.css`의 `:root`에 정의하고, 앱에서 바꿀 토큰만 `index.css`의 `:root`에 다시 씁니다. jamak은 팔레트 4종을 `theme.ts`가 `<html>`에 직접 넣습니다.
- 컴포넌트 코드에는 색·서체·간격 값을 쓰지 않습니다.
- 브라우저 기본 대화 상자(`alert`, `confirm`)와 운영체제 확인 창은 쓰지 않고 `Modal`을 씁니다. 파일·폴더 선택만 운영체제 창을 씁니다.

### 4.5 검사

- 백엔드: `uv run ruff check`, `uv run pytest`. 외부 도구(FFmpeg, yt-dlp, GPU)는 가짜로 바꿔 몇 초 안에 끝나게 합니다. 진짜 도구를 쓰는 테스트는 표시(marker)를 붙여 따로 돌립니다.
- 화면: `pnpm lint`(oxlint), `pnpm build`(tsc + vite), `cargo check --locked`.
- CI는 push와 PR마다 위 검사를 돌립니다. 설치 파일은 CI에서 만들지 않습니다.

---

## 5. 공통 파일을 맞출 때 합친 것 (2026-11-26)

jamak과 youtube_down의 판을 합치며 두 쪽의 좋은 점을 골랐습니다.

| 파일 | 고른 것 |
|---|---|
| `lib.rs` | 한국어 오류 문구·`VIRTUAL_ENV` 제거(youtube), Mac SIGTERM 종료(jamak). 시작 대기는 앱마다(jamak 120초, youtube 60초) |
| `proc.py` | 자신을 Job에 묶기(youtube)를 기본으로, 자식 하나 묶기(jamak)는 대비책과 Mac 종료용으로 |
| `backend.ts` | `patch`(jamak), 한국어 주석·오류 문구(youtube). WebSocket 다시 붙기는 `socket()` 하나로 |
| `vite.config.ts`, `package.json` | `woff2Only`·고정 포트·`tauri` 스크립트·`license`(youtube) |
| CI | Windows 공통 검사 + `cargo check`·lint(youtube), jamak의 Mac 검사는 `extra.yml`로 |
| `collect_licenses.py` | 앱 이름을 `pyproject.toml`에서, Rust 대상을 빌드하는 PC에서 읽게 바꿔 jamak에도 넣음(Windows·Mac 빌드 모두). jamak에 `THIRD_PARTY_NOTICES.md` 추가 |
| `.gitattributes` | jamak 것을 youtube에 넣음 |
| `common.tsx`·`base.css` | youtube의 부품·규칙이 기준. jamak에서 `Page`, `Note`, `tracked`, `useToast`를 더함. `Screen`의 머리글은 `Page`와 같은 `eyebrow` 방식으로. 진행 바는 기본 주 네온(jamak 방식), `.s`로 보조 네온. jamak 로고의 `.bar`는 `.line`으로 이름을 바꿔 진행 바와 겹치지 않게 함 |

두 앱의 모든 화면(jamak 10장, youtube 5장)을 바꾸기 전후로 찍어 픽셀 단위로 비교했습니다. 바뀐 것은 jamak에서 꺼진 주 버튼의 빛 번짐이 없어진 것 하나입니다(youtube 규칙을 따름).
앱마다 다르게 두는 것: `build_app.ps1`(youtube는 FFmpeg·Deno를 넣음), `seg`·목록·표·단계 표시 같은 화면 고유 규칙.

---

## 6. 진행 순서

1. **공통 파일 확정**
   1. 백엔드·Tauri 셸·연결 코드·설정·CI·스크립트 — 끝남(2026-11-26). 두 앱을 개발 모드로 띄워 백엔드 연결과 종료를 확인했습니다.
   2. 화면 부품(`common.tsx`)과 토큰·기본 규칙(`base.css`) — 끝남(2026-11-26). 두 앱의 모든 화면을 전후로 찍어 비교했습니다.
2. **video_converter 전환** — 1번에서 확정한 파일로 시작합니다(`check_structure.py --copy-from jamak_converter`). 계획은 video_converter의 `PLAN.md`.
3. **audio_converter 전환** — video_converter의 FFmpeg 실행기와 대기열을 가져다 씁니다. 계획은 audio_converter의 `PLAN.md`.
4. **점검 스크립트** — `scripts/check_structure.py`: 끝남. 있어야 할 파일, 앱 고유 값끼리 맞는지, 옆 폴더 저장소와 공통 파일이 같은지 봅니다. 문제가 있으면 종료 코드 1.

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
