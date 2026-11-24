"""Start/stop a local llama-server for the duration of the translation stage."""

from __future__ import annotations

import subprocess
import threading
import time
from pathlib import Path

import httpx

from jamak.config import TranslateOptions, paths
from jamak.core.proc import CREATE_NO_WINDOW, bind_to_lifetime, free_port


class LlamaServerError(RuntimeError):
    pass


class LlamaServer:
    def __init__(self, model: Path, opts: TranslateOptions, log_path: Path | None = None,
                 exe: Path | None = None):
        self.model = model
        self.opts = opts
        self.exe = exe or paths.llama_server_exe
        self.log_path = log_path
        self.proc: subprocess.Popen | None = None
        self.port: int | None = None

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1"

    def command(self) -> list[str]:
        slots = max(1, self.opts.parallel)
        return [
            str(self.exe),
            "-m", str(self.model),
            "-ngl", str(self.opts.gpu_layers),
            "-c", str(self.opts.ctx_size * slots),
            "-np", str(slots),
            "--host", "127.0.0.1",
            "--port", str(self.port),
            "--no-webui",
            "--reasoning", "off",
            "-a", "local",
        ]

    def start(self, timeout: float = 300.0, cancel: threading.Event | None = None) -> str:
        if not self.exe.exists():
            raise LlamaServerError(
                f"llama-server not found at {self.exe}. Run scripts/setup_llama.ps1 first."
            )
        if not self.model.exists():
            raise LlamaServerError(f"translation model not found: {self.model}")
        self.port = free_port()
        log = open(self.log_path, "w", encoding="utf-8") if self.log_path else subprocess.DEVNULL
        try:
            self.proc = subprocess.Popen(
                self.command(), stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                creationflags=CREATE_NO_WINDOW,
            )
        finally:
            if log is not subprocess.DEVNULL:
                log.close()  # the child keeps its own handle
        bind_to_lifetime(self.proc)
        deadline = time.monotonic() + timeout
        url = f"http://127.0.0.1:{self.port}/health"
        with httpx.Client(timeout=2.0) as client:
            while time.monotonic() < deadline:
                if self.proc.poll() is not None:
                    raise LlamaServerError(
                        f"llama-server exited with code {self.proc.returncode}"
                        + (f"; see {self.log_path}" if self.log_path else "")
                    )
                if cancel is not None and cancel.is_set():
                    self.stop()
                    raise LlamaServerError("cancelled")
                try:
                    r = client.get(url)
                    if r.status_code == 200:
                        return self.base_url
                except httpx.HTTPError:
                    pass
                time.sleep(0.5)
        self.stop()
        raise LlamaServerError(f"llama-server did not become ready within {timeout:.0f}s")

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=5)
        self.proc = None

    def __enter__(self) -> LlamaServer:
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()
