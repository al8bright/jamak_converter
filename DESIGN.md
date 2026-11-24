# DESIGN — jamak_converter 브랜드·UI 가이드

일본어 음성을 이 PC에서 한국어 자막으로 바꾸는 데스크톱 앱의 시각 규칙을 정리한 문서다.
화면 구성은 `docs/mockups/`, 로고·파비콘 원본은 `docs/brand/`에 있다.

- 디자인 방향: **사이버펑크 네온**. 어두운 바탕 위에 네온 두 색(주·보조)만 빛나게 하고, 나머지는 차분하게 둔다.
- **브랜드 색**(로고, 파비콘, 앱 아이콘)은 핫핑크 `#FF2E97`와 시안 `#00E5FF`로 고정한다. 앱 테마를 바꿔도 바뀌지 않는다.
- **테마 팔레트**는 앱 화면 색만 바꾸는 사용자 옵션이다. 기본은 **서울 나이트** (`seoul`)이고, 4종 모두 같은 토큰 구조를 쓴다.
- 원본 디자인 캔버스(비공개): https://claude.ai/artifact/D2EvWNUPnCait3vrPJ4KMw

---

## 1. 로고

### 1.1 구성

| 요소 | 의미 | 모양 |
|---|---|---|
| `「 」` 꺾쇠 | 일본어 인용 괄호. 대사를 감싸는 틀 | 왼쪽 위·오른쪽 아래 두 모서리. 주 네온 선, 글로우 |
| `字` → `자` | 일본어 자막(字幕)이 한국어 자막(자막)이 됨 | 가운데 글자. Noto Sans JP 900 / Noto Sans KR 900 |
| 자막 막대 | 화면 아래 자막 한 줄 | 글자 아래 가로 막대. 보조 네온, 글로우 |

- 워드마크: `JAMAK` — Chakra Petch 700, 대문자, 자간 `0.14em`(사이드바) / `0.08em`(대형).
- 태그라인: `字幕 → 자막` (字幕은 Noto Sans JP, 화살표는 주 네온 색).
- 한 줄 설명: "일본어 음성을 이 PC에서 한국어 자막으로 바꿉니다"

### 1.2 기하 (32 × 32 기준 좌표)

```
꺾쇠 path : M3 11V3h8  M29 21v8h-8     stroke = 주 네온, stroke-linecap = square
선 두께   : 2.0 (200px) · 2.4 (40~64px) · 2.6 (30px 사이드바)
글자      : 가로 가운데, 세로는 아래 13% 를 막대 자리로 비운 영역의 가운데
           글자 크기 ≈ 마크 크기의 52% (200px → 104px, 30px → 16px)
자막 막대 : 가로 = 마크의 42% (좌우 29%씩 여백), 높이 = 마크의 4~7%
           아래 여백 ≈ 마크의 15% (200px → bottom 30px, 30px → bottom 5px)
```

| 크기 | 쓰는 곳 | 글자 | 막대 (좌우 여백 / 아래 / 높이) | 꺾쇠 두께 |
|---|---|---|---|---|
| 200px | 브랜드 화면 히어로 | 104px | 58 / 30 / 8 px | 2.0 |
| 64px | 큰 아이콘 미리보기 | 34px | 19 / 10 / 3 px | 2.4 |
| 40px | 중간 | 21px | 12 / 6 / 2 px | 2.4 |
| 30px | 사이드바 로고 | 16px | 9 / 5 / 2 px | 2.6 |

### 1.3 애니메이션 (6초 반복)

| 키프레임 | 대상 | 타임라인 |
|---|---|---|
| `jm-ja` | 字 | 0–44% 보임 → 47–94% 숨김 → 97–100% 보임 |
| `jm-ko` | 자 | `jm-ja`의 반대 |
| `jm-gh` | 글리치 잔상 2겹 (주 네온 자, 보조 네온 字) | 44–48%, 94–98%에만 나타남. `clip-path: inset()`으로 가로 띠를 잘라 좌우로 3–6% 흔듦 |
| `jm-bar` | 자막 막대 | 0% 0 → 14–42% 꽉 참 → 47% 0 → 61–92% 꽉 참 → 97% 0. `transform: scaleX`, 기준점 왼쪽 |
| `jm-flick` | 마크 전체 | 전환 순간(44–46%, 94–96%)에 opacity 0.35~0.7로 깜빡임 |

```css
@keyframes jm-ja{0%,44%{opacity:1}47%,94%{opacity:0}97%,100%{opacity:1}}
@keyframes jm-ko{0%,44%{opacity:0}47%,94%{opacity:1}97%,100%{opacity:0}}
@keyframes jm-gh{0%,43.5%{opacity:0}44%{opacity:1;clip-path:inset(12% 0 58% 0);transform:translateX(-6%)}45.5%{opacity:1;clip-path:inset(56% 0 14% 0);transform:translateX(5%)}47%{opacity:1;clip-path:inset(32% 0 38% 0);transform:translateX(-3%)}48%,93.5%{opacity:0}94%{opacity:1;clip-path:inset(60% 0 10% 0);transform:translateX(6%)}95.5%{opacity:1;clip-path:inset(8% 0 62% 0);transform:translateX(-5%)}97%{opacity:1;clip-path:inset(40% 0 30% 0);transform:translateX(3%)}98%,100%{opacity:0}}
@keyframes jm-bar{0%{transform:scaleX(0)}14%,42%{transform:scaleX(1)}47%{transform:scaleX(0)}61%,92%{transform:scaleX(1)}97%,100%{transform:scaleX(0)}}
@keyframes jm-flick{0%,43%,48%,93%,98%,100%{opacity:1}44%{opacity:.35}45%{opacity:1}46%{opacity:.6}94%{opacity:.4}95%{opacity:1}96%{opacity:.7}}
@media (prefers-reduced-motion: reduce){[data-anim]{animation:none !important}}
```

- **정지 상태**: 애니메이션이 없을 때의 기본값은 `字` opacity 0, `자` opacity 1, 잔상 opacity 0, 막대 꽉 참. 그래서 움직임 줄이기 환경에서는 '자' + 막대가 보인다.
- 움직임은 로고에만 쓴다. 본문 UI에는 상태 점의 느린 깜빡임(`jm-pulse`, 2초)만 있다.

### 1.4 로고 사용 규칙

- 바탕은 팔레트의 `bg` 또는 `side` 같은 어두운 색 위에만 둔다. 밝은 바탕에는 파비콘처럼 어두운 사각 판을 깐다.
- 로고 색은 **브랜드 색으로 고정**한다(3.1절). 꺾쇠는 `brand.pink`, 막대는 `brand.cyan`, 글자는 `brand.ink`이다. 사이드바 로고와 워드마크 글로우도 테마 팔레트를 따르지 않는다.
- 글로우는 꺾쇠와 막대에만 준다.
- `logo-animated.svg`의 글자는 폰트에 의존한다(Noto Sans JP/KR 900, 없으면 Yu Gothic / Malgun Gothic). 앱 아이콘이나 인쇄물처럼 폰트를 보장할 수 없는 곳에서는 글자를 윤곽선으로 바꾸거나 파비콘(글자 없음)을 쓴다.

---

## 2. 파비콘 · 앱 아이콘

글자 없이 **꺾쇠 + 자막 두 줄**로만 만든 정적 SVG다. 폰트가 없어도 16px까지 같은 모양으로 보인다.

```svg
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
  <rect width="64" height="64" rx="14" fill="#07050F"/>
  <path d="M12 27V12h15M52 37v15H37" fill="none" stroke="#FF2E97" stroke-width="7" stroke-linecap="square"/>
  <rect x="19" y="27" width="26" height="5.5" rx="1" fill="#EEEAFF"/>
  <rect x="24" y="36" width="16" height="5.5" rx="1" fill="#00E5FF"/>
</svg>
```

| 요소 | 값 |
|---|---|
| 판 | 64 × 64, 모서리 반경 14, `brand.night` |
| 꺾쇠 | 두께 7, `brand.pink` |
| 첫 줄 (원문 자리) | x19 y27, 26 × 5.5, `brand.ink` |
| 둘째 줄 (번역 자리) | x24 y36, 16 × 5.5, `brand.cyan` |
| 확인한 크기 | 16 · 32 · 48 · 96px, 어두운·밝은 브라우저 탭 |

- 원본: `docs/brand/favicon.svg`
- Tauri 앱 아이콘(`app/src-tauri/icons`)은 이 SVG를 1024px PNG로 내보낸 뒤 `pnpm tauri icon <png>`로 만든다. 디자인을 바꾸면 같은 방법으로 다시 만든다.

---

## 3. 색

### 3.1 브랜드 색 (고정)

로고, 파비콘, 앱 아이콘, 설치 화면, README처럼 **앱 밖에서 jamak을 알아보게 하는 곳**에 쓴다. 앱 안의 사이드바 로고와 워드마크도 이 색을 쓴다.

| 토큰 | 값 | 용도 |
|---|---|---|
| `brand.pink` | `#FF2E97` | 꺾쇠, 워드마크 글로우 |
| `brand.cyan` | `#00E5FF` | 자막 막대, 파비콘 둘째 줄 |
| `brand.ink` | `#EEEAFF` | 로고 글자, 파비콘 첫 줄 |
| `brand.night` | `#07050F` | 파비콘·앱 아이콘 판 |

- 테마 팔레트(3.2절)는 앱 화면에만 적용한다. 브랜드 색을 팔레트 값으로 바꿔 그리지 않는다.
- 서울 나이트 팔레트의 `p`, `s`, `text`, `bg`가 브랜드 색과 같은 값인 것은 의도한 것이다. 기본 테마에서 로고와 화면이 한 벌로 보이게 했다.

### 3.2 테마 팔레트 기본 토큰 (팔레트마다 다른 값)

| 토큰 | 용도 | 서울 나이트 `seoul` | 애시드 `acid` | 아이스 그리드 `ice` | 블레이드 `blade` |
|---|---|---|---|---|---|
| `bg` | 앱 바탕, 격자 배경 | `#07050F` | `#040705` | `#02060C` | `#09080A` |
| `side` | 사이드바 | `#0B0817` | `#070C08` | `#040A13` | `#0D0B0D` |
| `panel` | 본문 인셋 영역 | `#0E0A1C` | `#0A100B` | `#06101C` | `#110E10` |
| `card` | 카드 | `#141026` | `#0F1711` | `#0A1626` | `#181416` |
| `field` | 입력칸, 표 머리, 토글 | `#1A1530` | `#142017` | `#0F1D31` | `#201A1C` |
| `line` | 기본 테두리, 구분선 | `#2A2246` | `#1F3023` | `#18304B` | `#332A2B` |
| `lineHi` | 입력칸·보조 버튼 테두리 | `#3D3363` | `#2E4734` | `#244667` | `#4A3C3C` |
| `text` | 본문 글자 | `#EEEAFF` | `#E8F6E6` | `#E2F3FF` | `#F6ECE6` |
| `muted` | 보조 글자, 라벨 | `#A49CC8` | `#93AC97` | `#8EA9C4` | `#B3A39C` |
| `p` | **주 네온** (주요 버튼, 선택, 진행) | `#FF2E97` 핫핑크 | `#C6FF1A` 라임 | `#2DF0FF` 아이스 시안 | `#FF7A1A` 앰버 |
| `s` | **보조 네온** (링크, 클라우드, 라벨) | `#00E5FF` 시안 | `#B26BFF` 바이올렛 | `#FF4FD8` 마젠타 | `#19F0D0` 틸 |
| `pInk` | 주 네온 위의 글자 | `#14000A` | `#0D1400` | `#001418` | `#1A0A00` |

### 3.3 상태 색 (팔레트마다 정함)

상태 색이 주요 버튼이나 보조 네온과 같은 색으로 보이면, "처리 시작" 버튼과 "완료" 배지, 또는 주요 버튼과 "주의" 배지를 구분할 수 없다. 그래서 상태 색은 팔레트마다 따로 정하고, 다음 규칙을 지킨다.

- **규칙 1**: `ok`, `warn`, `err`는 그 팔레트의 `p`, `s`와 OKLCH 색상각(hue)이 **40° 이상** 떨어진다.
- **규칙 2**: `ok`, `warn`, `err`끼리도 서로 40° 이상 떨어진다.
- **규칙 3**: `card` 바탕에서 대비 4.5:1 이상이다.
- **규칙 4**: 상태는 색만으로 알리지 않는다. 배지에는 항상 "완료", "주의", "오류" 같은 글자를 함께 쓴다.

| 토큰 | 용도 | `seoul` | `acid` | `ice` | `blade` |
|---|---|---|---|---|---|
| `ok` | 완료, 정상, 연결됨 | `#3DFF9A` 녹색 | `#2AF9D1` 민트 | `#3DFF9A` 녹색 | `#9AFC63` 연두 |
| `warn` | 주의, 길이 초과, 메모리 85% 이상 | `#FFD43B` 노랑 | `#FDB34C` 호박색 | `#FFD43B` 노랑 | `#FFDD44` 레몬 |
| `err` | 오류, 실패, 삭제 | `#FF773D` 주홍 | `#FF5468` 빨강 | `#FF674C` 주홍 | `#FF4989` 진분홍 |

색상각 거리 (OKLCH, 가장 가까운 쌍):

| 팔레트 | p / s 색상각 | 상태 ↔ p·s 최소 | 상태끼리 최소 | 바꾼 이유 |
|---|---|---|---|---|
| seoul | 357° / 209° | 45° (err ↔ p) | 50° | 빨강 오류가 핫핑크 주요 버튼과 21°라 주홍으로 옮김 |
| acid | 125° / 303° | 50° (ok ↔ p) | 54° | 녹색 완료(30°)와 노랑 주의(32°)가 라임과 겹쳐 민트·호박색으로 옮김 |
| ice | 203° / 338° | 49° (ok ↔ p) | 60° | 빨강 오류가 마젠타 보조 네온과 40°로 경계라 주홍으로 옮김 |
| blade | 49° / 178° | 42° (ok ↔ s) | 40° | 녹색 완료가 틸(24°)과, 빨강 오류가 앰버(31°)와 겹쳐 연두·진분홍으로 옮김 |

새 팔레트를 만들 때도 이 표를 다시 계산해서 규칙 1~3을 확인한다.

### 3.4 파생 토큰 (기본 토큰에서 계산)

| 토큰 | 계산 | 용도 |
|---|---|---|
| `pSoft` | `p` 12% | 선택된 항목 바탕 |
| `pLine` | `p` 50% | 선택된 항목 테두리, 점선 드롭존 |
| `pHalo` | `p` 7% | 본문 패널·카드의 은은한 빛 |
| `grid` | `p` 4.5% | 배경 격자선 (28px 간격) |
| `pGlow` | `0 0 0 1px p/60%, 0 0 18px p/45%` | 주요 버튼 글로우 |
| `pText` | `0 0 12px p/55%` | 워드마크·큰 숫자 글로우 |
| `sSoft` / `sLine` | `s` 12% / 45% | 클라우드 칩, 안내 박스 |
| `okSoft` / `okLine` | `ok` 10% / 35% | 상태 배지 |
| `warnSoft` / `warnLine` | `warn` 10% / 40% | 상태 배지 |
| `errSoft` / `errLine` | `err` 10% / 45% | 상태 배지 |
| `iconFx` | `drop-shadow(0 0 4px p/80%)` | 선택된 아이콘 |

### 3.5 CSS 변수로 옮길 때

```css
:root {
  /* 브랜드 색: 팔레트와 관계없이 고정 */
  --brand-pink:#FF2E97; --brand-cyan:#00E5FF; --brand-ink:#EEEAFF; --brand-night:#07050F;
}
:root, [data-palette="seoul"] {
  color-scheme: dark;
  --bg:#07050F; --side:#0B0817; --panel:#0E0A1C; --card:#141026; --field:#1A1530;
  --line:#2A2246; --line-hi:#3D3363; --text:#EEEAFF; --muted:#A49CC8;
  --p:#FF2E97; --s:#00E5FF; --p-ink:#14000A;
  --ok:#3DFF9A; --warn:#FFD43B; --err:#FF773D;
}
[data-palette="acid"]  { --bg:#040705; --side:#070C08; --panel:#0A100B; --card:#0F1711; --field:#142017; --line:#1F3023; --line-hi:#2E4734; --text:#E8F6E6; --muted:#93AC97; --p:#C6FF1A; --s:#B26BFF; --p-ink:#0D1400; --ok:#2AF9D1; --warn:#FDB34C; --err:#FF5468; }
[data-palette="ice"]   { --bg:#02060C; --side:#040A13; --panel:#06101C; --card:#0A1626; --field:#0F1D31; --line:#18304B; --line-hi:#244667; --text:#E2F3FF; --muted:#8EA9C4; --p:#2DF0FF; --s:#FF4FD8; --p-ink:#001418; --ok:#3DFF9A; --warn:#FFD43B; --err:#FF674C; }
[data-palette="blade"] { --bg:#09080A; --side:#0D0B0D; --panel:#110E10; --card:#181416; --field:#201A1C; --line:#332A2B; --line-hi:#4A3C3C; --text:#F6ECE6; --muted:#B3A39C; --p:#FF7A1A; --s:#19F0D0; --p-ink:#1A0A00; --ok:#9AFC63; --warn:#FFDD44; --err:#FF4989; }
:root {
  --p-soft: color-mix(in srgb, var(--p) 12%, transparent);
  --p-line: color-mix(in srgb, var(--p) 50%, transparent);
  --p-halo: color-mix(in srgb, var(--p) 7%, transparent);
  --grid:   color-mix(in srgb, var(--p) 4.5%, transparent);
  --s-soft: color-mix(in srgb, var(--s) 12%, transparent);
  --s-line: color-mix(in srgb, var(--s) 45%, transparent);
  --p-glow: 0 0 0 1px color-mix(in srgb, var(--p) 60%, transparent), 0 0 18px color-mix(in srgb, var(--p) 45%, transparent);
  --ok-soft:   color-mix(in srgb, var(--ok) 10%, transparent);
  --warn-soft: color-mix(in srgb, var(--warn) 10%, transparent);
  --err-soft:  color-mix(in srgb, var(--err) 10%, transparent);
}
```

### 3.6 대비 (WCAG, `card` 바탕 기준)

모든 글자 조합이 4.5:1 이상이다.

| 팔레트 | text | muted | p | s | pInk / p | ok | warn | err |
|---|---|---|---|---|---|---|---|---|
| seoul | 15.8 | 7.2 | 5.4 | 12.1 | 5.9 | 14.1 | 13.0 | 7.0 |
| acid | 16.3 | 7.5 | 15.4 | 5.6 | 15.9 | 13.5 | 10.2 | 5.8 |
| ice | 16.0 | 7.5 | 13.0 | 6.4 | 13.5 | 13.8 | 12.7 | 6.3 |
| blade | 15.7 | 7.5 | 7.0 | 12.5 | 7.4 | 14.3 | 13.6 | 5.7 |

---

## 4. 글꼴

| 역할 | 글꼴 | 굵기 | 쓰는 곳 |
|---|---|---|---|
| 본문 (한국어) | Noto Sans KR | 400 · 500 · 700 · 900 | 모든 UI 글자. 기본 13px, 행간 1.45 |
| 일본어 원문 | Noto Sans JP | 500 · 900 | 원문 열(`lang="ja"`), 로고의 字 |
| 숫자·경로·로그 | JetBrains Mono | 400 · 500 | 시간 코드, 파일 경로, 키, 로그, 푸터 계측 숫자 |
| 영문 라벨 | Chakra Petch | 500 · 600 · 700 | 워드마크, 헤더의 영문 eyebrow(`HOME`, `SETTINGS`), 푸터 계측 라벨 |

### 4.1 글꼴은 앱에 넣어서 배포한다

이 앱은 "이 PC에서 처리하고 외부로 보내지 않는다"고 약속하는 오프라인 데스크톱 앱이다. 실행할 때마다 Google Fonts 같은 외부 서버에서 글꼴을 받으면 그 약속과 어긋나고, 인터넷이 없으면 다른 글꼴로 바뀐다. 그래서 **글꼴 파일은 앱 번들에 넣는다.**

- 프런트엔드(`app/`, Vite)에서는 Fontsource 패키지로 woff2 파일을 번들에 포함한다.

  ```
  pnpm add @fontsource/noto-sans-kr @fontsource/noto-sans-jp @fontsource/jetbrains-mono @fontsource/chakra-petch
  ```

  ```ts
  // app/src/main.tsx
  import '@fontsource/noto-sans-kr/400.css';
  import '@fontsource/noto-sans-kr/500.css';
  import '@fontsource/noto-sans-kr/700.css';
  import '@fontsource/noto-sans-kr/900.css';
  import '@fontsource/noto-sans-jp/500.css';
  import '@fontsource/noto-sans-jp/900.css';
  import '@fontsource/jetbrains-mono/400.css';
  import '@fontsource/jetbrains-mono/500.css';
  import '@fontsource/chakra-petch/600.css';
  import '@fontsource/chakra-petch/700.css';
  ```

- `index.html`과 CSS에 `fonts.googleapis.com` 링크를 넣지 않는다. Tauri CSP에도 외부 글꼴 주소를 열지 않는다.
- 글꼴 라이선스는 모두 SIL Open Font License라 앱에 포함해 배포할 수 있다.
- `docs/mockups/`와 디자인 캔버스는 문서용이라 Google Fonts에서 받는다. 앱에는 이 방식을 쓰지 않는다.

### 4.2 한글에는 모노 글꼴과 자간을 쓰지 않는다

JetBrains Mono와 Chakra Petch에는 한글 글리프가 없다. 한글이 들어간 글자에 이 글꼴만 지정하면 시스템의 다른 글꼴로 바뀌면서 글자 사이가 벌어진다(예: "백 엔 드 연 결 됨").

- **모노 글꼴은 숫자, 시간 코드, 경로, 키, 모델 ID에만 쓴다.** 백엔드 상태 문구처럼 한국어 문장은 Noto Sans KR로 쓴다.
- 모노나 Chakra Petch를 지정할 때는 한글이 섞일 수 있으므로 항상 한글 글꼴을 다음 순위로 둔다.

  ```css
  font-family: 'JetBrains Mono', 'Noto Sans KR', monospace;
  font-family: 'Chakra Petch', 'Noto Sans KR', sans-serif;
  ```

- **자간(`letter-spacing`)은 라틴 대문자에만 준다.** 한글과 라틴이 섞인 줄은 라틴 부분만 감싸서 자간을 준다.

  ```html
  <span class="eyebrow"><span class="tracked">REVIEW</span> · 21:37 완료 · 352줄</span>
  ```

- 사이드바 그룹명(작업, 설정)처럼 한글만 있는 라벨에는 자간을 주지 않는다.

### 4.3 크기

글자 크기 단계: 10 (접힌 사이드바 라벨) · 11 (라벨, 보조, 모노) · 12 (버튼, 작은 본문) · 13 (본문) · 14 (카드 제목) · 15–16 (페이지 제목 h1) · 26 (진행률 숫자).
영문 eyebrow는 11px, 600, 보조 네온 색이고, 라틴 부분에만 자간 `0.14em`을 준다.

---

## 5. 형태 · 레이아웃

### 5.1 형태

- **모서리**: 버튼·입력칸·배지 3px, 메뉴 항목·토글 4px, 카드·패널 6px. 둥근 알약 모양은 상태 점에만 쓴다.
- **글로우는 강조에만**: 주요 버튼, 선택된 메뉴 항목·아이콘, 진행 막대, 푸터 계측 막대, 로고, 상태 점. 본문 글자와 일반 카드에는 글로우를 주지 않는다.
- **배경**: `bg` 위에 `grid` 색 1px 선을 28px 간격 격자로 깐다.
- **포커스**: `:focus-visible { outline: 2px solid #FFFFFF; outline-offset: 2px }` — 네온 색과 겹치지 않게 흰색.

### 5.2 화면 골격 (1280 × 860 기준)

| 영역 | 값 |
|---|---|
| 사이드바 (펼침) | 폭 200px, 패딩 14/10, 메뉴 높이 34px, 글자 13px, 아이콘 16px |
| 사이드바 (접힘) | 폭 68px, 아이콘 18px 아래 10px 라벨, 메뉴 높이 48px. 그룹명(작업·설정)은 그대로 표시 |
| 헤더 | 최소 높이 50px, 패딩 6/16. 왼쪽 eyebrow + h1, 오른쪽 작업 버튼 |
| 본문 인셋 | 좌우 여백 10px, 테두리 `lineHi`, 반경 6px, 바탕 `panel`, `pHalo` 빛. 내부 최대 폭 1120–1200px, 패딩 14/18/20 |
| 푸터 모니터 | 최소 높이 30px, JetBrains Mono 11px. GPU · VRAM · CPU · RAM 막대(48 × 4px) + 백엔드 상태 + 하드웨어 이름 |

### 5.3 구성 요소 색

| 구성 요소 | 바탕 | 글자 | 테두리 | 글로우 |
|---|---|---|---|---|
| 주요 버튼 | `p` | `pInk` 700 | — | `pGlow` |
| 보조 버튼 | 투명 | `text` | `lineHi` | — |
| 보조 네온 버튼 (연결 테스트, 다시 확인) | 투명 또는 `sSoft` | `s` | `sLine` | — |
| 위험 버튼 (작업 취소, 삭제) | 투명 | `err` | `errLine` | — |
| 선택된 메뉴·라디오 카드 | `pSoft` | `text` 700 | `pLine` / `p` | 아이콘에 `iconFx` |
| 로컬 칩 | `field` | `muted` | `lineHi` | — |
| 클라우드 칩 | `sSoft` | `s` | `sLine` | — |
| 기본 배지 | `p` | `pInk` | — | — |
| 상태 배지 | `okSoft` / `warnSoft` / `errSoft` | `ok` / `warn` / `err` | 각 `*Line` | — |
| 진행 막대 | 트랙 `line` | 채움 `p` | — | `0 0 12px p` |
| 푸터 계측 | 트랙 `line` | GPU·CPU `p`, VRAM·RAM `s`, 85% 이상 `warn` | — | `0 0 6px` 같은 색 |
| 링크·시간 코드 | — | `s` | — | — |

---

## 6. 접근성

- 글자 대비는 3.5절 표대로 모두 4.5:1 이상이다.
- `prefers-reduced-motion: reduce`이면 로고·상태 점 애니메이션을 모두 끈다.
- 아이콘만 있는 버튼에는 `aria-label`을 붙이고(사이드바 접기 등), 장식용 SVG에는 `aria-hidden="true"`를 붙인다.
- 일본어 원문에는 `lang="ja"`를 붙여 읽기 프로그램과 글꼴이 맞게 처리되게 한다.

---

## 7. 목업

`docs/mockups/`에 화면 7개가 HTML과 PNG(2배 해상도)로 들어 있다.

| 화면 | HTML | PNG |
|---|---|---|
| 1 · 홈 / 새 작업 | `Main.html` | `png/main.png`, `png/main--sidebar-hidden.png` |
| 2 · 작업 옵션 | `Options.html` | `png/options.png` |
| 3 · 진행 | `Progress.html` | `png/progress.png` (로컬), `png/progress--cloud.png` |
| 4 · 검수·편집·저장 | `Review.html` | `png/review.png` |
| 5 · 설정 › 번역 엔진 | `Settings.html` | `png/settings.png` |
| 6 · 설정 › 시스템 상태 | `System.html` | `png/system.png` |
| 7 · 브랜드 | `Brand.html` | `png/brand.png` |
| 팔레트 비교 | — | `png/main--acid.png`, `png/main--ice.png`, `png/main--blade.png` |

- HTML은 서버 없이 브라우저로 바로 열린다(글꼴만 Google Fonts에서 받음). 같은 폴더의 `dc-lite.js`가 화면을 그린다.
- 화면 사이 링크, 사이드바 접기, 라디오·체크박스, 검수 줄 선택·편집, 번역 엔진 종류 전환이 실제로 동작한다.
- 주소 끝에 값을 붙여 화면 변형을 볼 수 있다.
  - `?palette=seoul|acid|ice|blade`
  - `?sidebar=open|hidden`
  - `Progress.html?engine=local|cloud`
  - `Review.html?maxChars=16~60`
  - 예: `Main.html?palette=ice&sidebar=hidden`
- 목업 데이터(파일명, 시간, 대사, 토큰 수, 하드웨어)는 모두 예시 값이다.

---

## 8. 파일

| 경로 | 내용 |
|---|---|
| `DESIGN.md` | 이 문서 |
| `docs/brand/favicon.svg` | 파비콘·앱 아이콘 원본 (정적, 글자 없음) |
| `docs/brand/logo-animated.svg` | 움직이는 로고 단독 SVG (서울 나이트 색, 폰트 의존) |
| `docs/brand/logo.png` | 로고 정지 화면 512 × 512 |
| `docs/mockups/*.html` | 화면 목업 7개 |
| `docs/mockups/dc-lite.js` | 목업 렌더러 |
| `docs/mockups/png/*.png` | 목업 캡처 12장 |
