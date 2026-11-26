# Third-party notices

이 저장소의 소스 코드는 [MIT 라이선스](LICENSE)입니다.
배포하는 **설치 파일**에는 아래의 다른 프로젝트가 함께 들어 있고, 각각 자기 라이선스를 따릅니다.
설치 폴더의 `backend\licenses\`에 각 라이선스 전문이 들어 있습니다.

The source code in this repository is licensed under the [MIT License](LICENSE).
The **installer** also ships the third-party software below, each under its own license.
Full license texts are installed in `backend\licenses\` next to the app.

## 처음 실행할 때 받는 것 · Downloaded on first run

설치 파일에는 들어 있지 않습니다. 사용자가 앱의 설정 화면에서 각 배포처로부터 직접 받고(SHA-256 확인),
받은 폴더에 각자의 라이선스 파일이 함께 저장됩니다. 이 프로젝트가 재배포하지 않습니다.

| 구성 요소 | 라이선스 | 배포처 |
| :--- | :--- | :--- |
| FFmpeg (Windows) | LGPL-2.1-or-later (BtbN `lgpl` 빌드) | https://github.com/BtbN/FFmpeg-Builds |
| FFmpeg (macOS) | GPL-3.0-or-later (martin-riedl.de 정적 빌드) | https://ffmpeg.martin-riedl.de |
| NVIDIA cuBLAS · cuDNN (Windows, GPU 사용 시) | NVIDIA 소프트웨어 라이선스 | PyPI (`nvidia-cublas-cu12`, `nvidia-cudnn-cu12`) |
| llama.cpp (`llama-server`) | MIT | https://github.com/ggml-org/llama.cpp |
| 음성 인식·번역 모델 | 모델마다 다름(화면의 모델 목록에 표시) | Hugging Face |

## 백엔드 · Backend (`jamak-backend`, PyInstaller)

| 구성 요소 | 라이선스 |
| :--- | :--- |
| Python 3.12 | PSF-2.0 |
| faster-whisper · CTranslate2 · onnxruntime | MIT |
| **PyAV** (`av`) | BSD-3-Clause. 휠에 든 **FFmpeg 라이브러리는 LGPL-2.1-or-later** |
| FastAPI · Starlette · uvicorn · pydantic · websockets 등 | MIT · BSD-3-Clause |
| anthropic · google-genai · requests · huggingface_hub · tokenizers | Apache-2.0 |
| certifi · tqdm | MPL-2.0 (tqdm: MPL-2.0 AND MIT) |
| numpy | BSD-3-Clause 외 |
| mlx · mlx-whisper · keyring (macOS만) | MIT |
| PyInstaller 부트로더 | GPL-2.0 with bootloader exception (만든 프로그램의 라이선스에 제약 없음) |

전체 목록과 전문: `backend\licenses\python-packages.txt` (빌드 때 `scripts/collect_licenses.py`가 만듭니다).

PyAV의 FFmpeg 라이브러리는 별도 DLL로 들어 있으며(LGPL 조건: 사용자가 같은 이름의 다른 빌드로 바꿀 수 있음),
해당 소스는 https://ffmpeg.org 와 https://github.com/PyAV-Org/pyav-ffmpeg 에서 받을 수 있습니다.

## 앱 · App window

| 구성 요소 | 라이선스 |
| :--- | :--- |
| Tauri와 Rust 크레이트 | 대부분 MIT OR Apache-2.0 (일부 BSD-3-Clause · MPL-2.0 · Unicode-3.0 · Zlib) |
| React · React DOM | MIT |
| @tauri-apps/api · plugin-dialog | MIT OR Apache-2.0 |
| Noto Sans KR · Noto Sans JP · Chakra Petch · JetBrains Mono (`@fontsource`) | SIL Open Font License 1.1 |

전체 목록과 전문: `backend\licenses\rust-crates.txt`, `backend\licenses\js-packages.txt`.
