# TODO

> 끝난 항목은 [PLAN.md](PLAN.md)의 해당 Phase로 옮기고 이 문서에서 지웁니다. 2026-11-25에 1~5장의 끝난 항목을 Phase 4.5로 옮겼습니다.
> 전체 검토 결과와 결정할 것은 [docs/REVIEW-2026-11-25.md](docs/REVIEW-2026-11-25.md)에 있습니다.

## 1. 음성 인식

- [ ] **노래 가사가 사라지는 문제**: large-v3가 가사 구간을 환각 문구로 바꾸고 환각 필터가 지움. 노래 구간(사용자가 지정한 시간 범위)만 Qwen3-ASR로 다시 인식하는 방법을 검토. Qwen을 받아 둔 경우에만 가능하고, 작업 프로세스를 한 번 더 띄우는 비용(모델 올리기 약 10초)이 듦
- [ ] **anime-whisper는 보류**: 변환본을 직접 배포하지 않기로 함. 다른 사람이 올린 변환본(`flyfront/anime-whisper-faster`, 내용 미확인)이나 사용자 PC 변환(PyTorch 필요)뿐이라 지금은 추가하지 않음. 쓰게 되면 인식 힌트는 넣지 말 것
- [ ] **인식 후 교정**: Qwen3-ASR은 힌트 형식을 바꿔도 風間을 クゼマ로 들음(2026-11-25 시험, PLAN Phase 4.5). 소리가 비슷한 이름은 힌트로 고칠 수 없으므로, 용어집 항목에 "잘못 들린 표기"(예: クゼマ → 風間)를 적어 두면 인식 직후 바꿔 주는 기능을 검토. 바꾼 줄은 검수 화면에 표시
- [ ] Phase 6에서 정답 자막(직접 받아 적은 3~5분 구간)으로 모델별 CER 측정. 정답 자막이 있어야 해서 지금은 할 수 없음

## 2. 클라우드 번역

- [ ] 새 서비스 7곳(OpenAI, DeepSeek, xAI, Mistral, OpenRouter, DeepL, Papago)을 실제 키로 연결 테스트. 지금은 가짜 응답으로만 확인함
- [ ] DeepL 용어집 기능(일본어→한국어)이 되면 치환 방식 대신 쓰는 것을 검토
- [ ] OpenRouter는 모델마다 JSON 스키마 지원이 달라, 지원하지 않는 모델이면 `json_object`로 자동 전환할지 결정

## 3. 내려받기

- [ ] 모델 관리 화면에서 PyTorch 실행 환경을 받는 동안 진행률이 올라가는지 눈으로 확인(설치 자체는 같은 코드로 빈 폴더에서 확인함)
- [ ] 모델 관리에 추가한 번역 모델 4개(Qwen3.5 9B, Gemma 4 26B A4B, Mistral Small 3.2 24B, Qwen3.5 35B A3B)를 실제로 받아 보기: 지금 llama-server(b12675)가 불러오는지, GPU 메모리 실측(지금 값은 파일 크기 + 1.5GB 추정)

## 4. Mac 지원 (Apple Silicon)

코드는 들어갔습니다(PLAN.md Phase 4.6). 단위 테스트는 가짜 mlx-whisper·키체인으로, 음성 인식 연결부는 CI의 Mac 러너에서 진짜 mlx-whisper(whisper-tiny)로 확인했습니다. **아래는 전부 실제 Mac이 있어야 합니다**(CI 러너는 가상 머신이라 속도·전력·Neural Engine은 잴 수 없음). Intel Mac은 대상에서 뺍니다.

### 처음 확인 (Mac 한 대, M 시리즈 16GB 이상)

- [ ] `pnpm tauri dev`로 처음 실행 → FFmpeg·llama-server·Whisper 받기 → 영화 한 편 처리까지
  - FFmpeg: martin-riedl.de zip을 풀고 실행 권한을 주는 부분, Gatekeeper가 막지 않는지
  - llama-server: tar.gz의 dylib 심볼릭 링크가 살아 있는지, `-ngl 99`로 Metal에 올라가는지
- [ ] mlx-whisper 결과 확인(large-v3·turbo·kotoba, 실제 영화): 단어 시간이 faster-whisper와 비슷한지, 빔 서치가 없어 대사가 얼마나 빠지는지. 단어 시간·VAD 구간·진행률·취소가 동작하는 것 자체는 CI에서 whisper-tiny로 확인함
- [ ] 인코더 재사용 효과 측정: 같은 영화를 재사용을 켜고 끈 채로 돌려 시간 비교(로그의 `encoder: N calls, M reused`). 예상은 turbo·kotoba 40% 안팎, large-v3 15~20% 단축이지만 추정값임
- [ ] 키체인: 키 등록 → 앱 다시 열기 → 번역. 새 빌드에서 macOS가 키체인 접근을 묻는지
- [ ] 하위 프로세스: 앱을 강제 종료했을 때 llama-server가 남는지(Mac에는 Job Object가 없어 정상 종료 때만 정리함)
- [ ] 통합 메모리 70% 기준(`hw.METAL_SHARE`)으로 모델 경고가 맞는지. 16GB Mac에서 Whisper와 Gemma 12B를 차례로 쓸 때 메모리 압박

### 결정할 것

- [ ] kotoba-whisper MLX 변환본: 지금은 `kaiinui/kotoba-whisper-v2.0-mlx`(다른 사람 변환, 내용 미확인). 같은 영화 구간에서 결과를 보고 그대로 둘지, 직접 변환본을 만들지
- [ ] **Mac 빔 서치**: mlx-whisper는 빔 서치를 막아 둠(`decoding.py`의 `NotImplementedError`). Windows에서 같은 영화를 빔 5와 빔 1로 비교하니 빔 1이 24% 빠르지만 대사를 3곳(약 350자, 전체의 3%) 통째로 놓치고 반복 루프가 5곳(빔 5는 1곳·3곳)이었음(PLAN Phase 4.6). Mac에서 실제로 이 정도로 빠지면 openai-whisper(MIT)의 `BeamSearchDecoder`를 MLX로 옮겨 구현. Lightning-SimulWhisper에 MLX 구현이 있으나 PolyForm Noncommercial이라 코드를 가져올 수 없음
- [ ] **CoreML 인코더(Neural Engine)**: 인코더만 CoreML로 돌리고 디코더는 MLX 그대로 두는 방식. 미리 변환된 인코더가 whisper.cpp의 Hugging Face 저장소에 있음(`ggml-large-v3-encoder.mlmodelc.zip` 1.18GB, turbo 1.17GB, MIT). kotoba-whisper v2.0은 인코더가 large-v3와 같아(가중치 견본 5개 일치) large-v3용을 그대로 쓸 수 있음. 끼울 자리는 `model.encoder` 한 곳(`EncoderOnce`와 같은 방식), 의존성은 coremltools(BSD, torch 불필요). 비용은 모델당 1.2GB 추가, 첫 실행 컴파일 시간. 이득은 미확인: 공개된 "3배"는 CPU 대비이고 Metal GPU 대비 수치는 없음. 속도와 전력(발열·배터리)을 재 보고 결정
- [ ] 대안 비교: WhisperKit(Core ML), macOS 26 SpeechAnalyzer, whisper.cpp(Metal+CoreML, 빔 서치 있음. 단 릴리스에 macOS 실행 파일이 없어 직접 빌드·서명해야 함). mlx-whisper 결과가 나쁘면 같은 구간으로 속도와 품질 비교
- [ ] Qwen3-ASR: PyTorch MPS에서 qwen-asr와 ForcedAligner가 도는지. 되면 Mac용 `qwen-requirements.lock`(PyPI)과 `bin/python` 경로로 PyTorch 실행 환경을 다시 켬. 지금은 Mac 목록에서 숨김
- [ ] 개발 환경에 torch가 딸려 옴: mlx-whisper가 변환 스크립트 때문에 torch를 의존성으로 요구함(앱 묶음에서는 뺌). 신경 쓰이면 `--no-deps`로 따로 설치하는 방법 검토

### 배포

- [ ] **서명과 공증**: Apple Developer ID로 서명하고 공증하지 않으면 Gatekeeper가 실행을 막음(`tauri.macos.conf.json`의 `signingIdentity`). PyInstaller 묶음 안의 dylib도 함께 서명해야 함
- [ ] 받은 실행 파일(llama-server, ffmpeg)의 격리 속성(quarantine): 앱이 직접 받으면 붙지 않는 것으로 알고 있으나 확인
- [ ] **글꼴**: Noto Sans KR/JP가 없는 Mac에서도 화면이 맞는지(DESIGN.md 4.2)
- [ ] 사용량 표시: Mac에는 GPU 사용률(nvidia-smi)이 없어 화면 아래 모니터에 CPU·RAM만 나옴. 필요하면 `powermetrics` 없이 얻는 방법 검토
