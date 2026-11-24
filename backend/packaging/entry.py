"""PyInstaller entry point: the same CLI as `uv run jamak` (the app runs `jamak-backend serve ...`)."""

import sys

from jamak import main

if __name__ == "__main__":
    sys.exit(main())
