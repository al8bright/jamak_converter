"""PyInstaller 진입점: `uv run jamak`과 같은 명령줄(앱은 `jamak-backend serve ...`로 띄운다). (공통 파일)"""

import sys

from jamak import main

if __name__ == "__main__":
    sys.exit(main())
