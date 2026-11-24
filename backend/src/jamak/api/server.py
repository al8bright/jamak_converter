"""Local HTTP/WebSocket API used by the web UI (and Tauri).

Security model: binds to 127.0.0.1 only; the Host header must be 127.0.0.1/localhost
(blocks DNS-rebinding); when JAMAK_TOKEN is set (Tauri passes a random one), every request
must carry it in `X-Jamak-Token` (or `?token=` for WebSockets).
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from jamak import __version__
from jamak.api.jobs import JobManager, list_job_dirs, load_options, purge_job
from jamak.config import GlossaryEntry, OutputOptions, PipelineOptions, default_llm_path, paths
from jamak.core.cache import Job, write_json_atomic
from jamak.core.proc import CREATE_NO_WINDOW
from jamak.core.types import Line, Lines
from jamak.installer import SetupManager

ALLOWED_ORIGINS = [
    "http://localhost:5173", "http://127.0.0.1:5173",  # Vite dev server
    "tauri://localhost", "http://tauri.localhost", "https://tauri.localhost",  # Tauri webview
]
ALLOWED_HOSTS = {"127.0.0.1", "localhost", "testserver"}


# ---------------------------------------------------------------------- request bodies


class CreateJob(BaseModel):
    video_path: str
    options: PipelineOptions | None = None
    force_from: str | None = None


class SegmentEdit(BaseModel):
    id: int
    ko: str | None = None
    ja: str | None = None
    start: float | None = None
    end: float | None = None
    locked: bool | None = None


class PatchSegments(BaseModel):
    edits: list[SegmentEdit]


class Retranslate(BaseModel):
    ids: list[int] = Field(min_length=1)


class ExportRequest(BaseModel):
    output: OutputOptions | None = None


class PathBody(BaseModel):
    path: str


class GlossaryBody(BaseModel):
    entries: list[GlossaryEntry]


class InstallBody(BaseModel):
    ids: list[str] | None = None


# ---------------------------------------------------------------------- helpers


def _job_or_404(key: str) -> Job:
    if not re.fullmatch(r"[0-9a-f]{16}", key):
        raise HTTPException(400, "bad job key")
    if not (paths.jobs / key / "meta.json").exists():
        raise HTTPException(404, "job not found")
    return Job.open_key(key)


def _lines_path(job: Job) -> tuple[Path, str]:
    ko = job.path("segments.ko.json")
    if ko.exists():
        return ko, "ko"
    ja = job.path("segments.ja.json")
    if ja.exists():
        return ja, "ja"
    raise HTTPException(409, "no subtitles yet")


def _glossary_file(name: str) -> Path:
    if not re.fullmatch(r"[\w\-. ]{1,64}", name) or name.startswith("."):
        raise HTTPException(400, "bad glossary name")
    return paths.glossaries / f"{name}.json"


def _gpu_info() -> list[dict[str, Any]]:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return []
    try:
        res = subprocess.run([exe, "--query-gpu=name,memory.total,memory.used,driver_version",
                              "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=5,
                             creationflags=CREATE_NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired):
        return []
    gpus = []
    for line in res.stdout.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) == 4:
            gpus.append({"name": parts[0], "memory_total_mb": int(float(parts[1])),
                         "memory_used_mb": int(float(parts[2])), "driver": parts[3]})
    return gpus


def _ffmpeg_path() -> str | None:
    from jamak.core.audio import FfmpegError, ffmpeg_exe

    try:
        return ffmpeg_exe()
    except FfmpegError:
        return None


def system_info() -> dict[str, Any]:
    from jamak.core import cuda, separate
    from jamak.core.stt.faster_whisper import MODEL_CHOICES

    llama = {"path": str(paths.llama_server_exe), "exists": paths.llama_server_exe.exists()}
    vfile = paths.llama_dir / "version.json"
    if vfile.exists():
        try:
            llama["build"] = json.loads(vfile.read_text(encoding="utf-8-sig")).get("build")
        except json.JSONDecodeError:
            pass
    models = []
    if paths.models.exists():
        for p in sorted(paths.models.glob("*.gguf")):
            if not p.name.startswith("mmproj"):
                models.append({"name": p.name, "path": str(p), "size_gb": round(p.stat().st_size / 1e9, 2)})
    default = default_llm_path()
    return {
        "version": __version__,
        "home": str(paths.home),
        "ffmpeg": _ffmpeg_path(),
        "cuda_devices": cuda.cuda_device_count(),
        "gpus": _gpu_info(),
        "llama_server": llama,
        "llm_models": models,
        "default_llm": str(default) if default else None,
        "stt_models": MODEL_CHOICES,
        "separation_available": separate.available(),
    }


# ---------------------------------------------------------------------- app


def create_app(manager: JobManager | None = None, token: str | None = None,
               setup_manager: SetupManager | None = None) -> FastAPI:
    token = token if token is not None else os.environ.get("JAMAK_TOKEN") or None
    mgr = manager or JobManager()
    setup = setup_manager or SetupManager()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        mgr.attach_loop(asyncio.get_running_loop())
        paths.ensure()
        yield

    app = FastAPI(title="jamak", version=__version__, lifespan=lifespan)
    app.state.manager = mgr
    app.add_middleware(CORSMiddleware, allow_origins=ALLOWED_ORIGINS, allow_methods=["*"],
                       allow_headers=["*"])

    @app.middleware("http")
    async def guard(request: Request, call_next):
        host = (request.headers.get("host") or "").rsplit(":", 1)[0].strip("[]")
        if host not in ALLOWED_HOSTS:
            return JSONResponse({"detail": "forbidden host"}, status_code=403)
        if token and request.method != "OPTIONS" and request.url.path != "/health":
            if request.headers.get("x-jamak-token") != token:
                return JSONResponse({"detail": "bad token"}, status_code=401)
        return await call_next(request)

    # -------------------------------------------------------------- system
    @app.get("/health")
    def health():
        return {"status": "ok", "version": __version__}

    @app.get("/system")
    def system():
        return system_info()

    @app.get("/options/default")
    def default_options():
        return PipelineOptions()

    @app.post("/media/probe")
    def media_probe(body: PathBody):
        from jamak.core.audio import FfmpegError, pick_track, probe

        p = Path(body.path)
        if not p.is_file():
            raise HTTPException(404, "file not found")
        try:
            info = probe(p)
        except FfmpegError as e:
            raise HTTPException(422, str(e)) from e
        picked = pick_track(info).index if info.tracks else None
        return {"duration": info.duration, "tracks": [t.__dict__ for t in info.tracks], "picked": picked,
                "name": p.name, "size": p.stat().st_size}

    @app.post("/fs/reveal")
    def reveal(body: PathBody):
        p = Path(body.path)
        if not p.exists():
            raise HTTPException(404, "not found")
        if sys.platform == "win32":
            subprocess.Popen(["explorer", "/select,", str(p)], creationflags=CREATE_NO_WINDOW)
        return {"ok": True}

    # -------------------------------------------------------------- jobs
    @app.get("/jobs")
    def jobs():
        live = {k: s.public() for k, s in mgr.states.items()}
        out = []
        for j in list_job_dirs():
            j["live"] = live.pop(j["key"], None)
            out.append(j)
        for k, s in live.items():  # queued jobs whose folder has no meta yet
            out.insert(0, {"key": k, "video_path": s["video_path"], "live": s})
        return out

    @app.post("/jobs", status_code=202)
    def create_job(body: CreateJob):
        p = Path(body.video_path)
        if not p.is_file():
            raise HTTPException(404, "video file not found")
        if body.force_from and body.force_from not in ("audio", "separate", "stt", "segment", "translate"):
            raise HTTPException(400, "bad force_from")
        opts = body.options or PipelineOptions()
        try:
            st = mgr.submit_run(p, opts, body.force_from)
        except RuntimeError as e:
            raise HTTPException(409, str(e)) from e
        return st.public()

    @app.get("/jobs/{key}")
    def get_job(key: str):
        job = _job_or_404(key)
        live = mgr.states.get(key)
        return {"key": key, "meta": job.meta, "options": load_options(job),
                "live": live.public(with_events=True) if live else None}

    @app.post("/jobs/{key}/cancel")
    def cancel_job(key: str):
        if not mgr.cancel(key):
            raise HTTPException(404, "no running job")
        return {"ok": True}

    @app.delete("/jobs/{key}")
    def delete_job(key: str, purge: bool = False):
        mgr.cancel(key)
        if purge:
            if mgr.busy(key):
                raise HTTPException(409, "job is still running; cancel and retry")
            purge_job(key)
            mgr.states.pop(key, None)
        return {"ok": True}

    @app.get("/jobs/{key}/segments")
    def get_segments(key: str):
        job = _job_or_404(key)
        path, source = _lines_path(job)
        lines = Lines.model_validate_json(path.read_text(encoding="utf-8")).lines
        dropped = []
        fp = job.path("filtered.json")
        if fp.exists():
            dropped = json.loads(fp.read_text(encoding="utf-8"))
        return {"source": source, "lines": lines, "dropped": dropped}

    @app.patch("/jobs/{key}/segments")
    def patch_segments(key: str, body: PatchSegments):
        job = _job_or_404(key)
        if mgr.busy(key):
            raise HTTPException(409, "job is busy")
        path, _ = _lines_path(job)
        lines = Lines.model_validate_json(path.read_text(encoding="utf-8")).lines
        by_id: dict[int, Line] = {ln.id: ln for ln in lines}
        for e in body.edits:
            ln = by_id.get(e.id)
            if ln is None:
                raise HTTPException(404, f"line {e.id} not found")
            if e.ko is not None:
                ln.ko = e.ko
                ln.locked = True
                ln.flags = [f for f in ln.flags if f != "translate_failed"]
            if e.ja is not None:
                ln.ja = e.ja
            if e.start is not None:
                ln.start = e.start
            if e.end is not None:
                ln.end = e.end
            if e.locked is not None:
                ln.locked = e.locked
            if ln.end <= ln.start:
                raise HTTPException(422, f"line {e.id}: end must be after start")
        lines.sort(key=lambda ln: ln.start)
        write_json_atomic(job.path("segments.ko.json"), Lines(lines=lines))
        return {"ok": True, "lines": lines}

    @app.post("/jobs/{key}/retranslate", status_code=202)
    def retranslate(key: str, body: Retranslate):
        _job_or_404(key)
        try:
            st = mgr.submit_retranslate(key, body.ids)
        except RuntimeError as e:
            raise HTTPException(409, str(e)) from e
        return st.public()

    @app.post("/jobs/{key}/export")
    def export(key: str, body: ExportRequest):
        from jamak.core import subtitle

        job = _job_or_404(key)
        path, _ = _lines_path(job)
        lines = Lines.model_validate_json(path.read_text(encoding="utf-8")).lines
        out_opts = body.output or load_options(job).output
        written = subtitle.export(lines, job.video, out_opts)
        job.update_meta(last_export=[str(p) for p in written])
        return {"written": [str(p) for p in written]}

    @app.websocket("/jobs/{key}/events")
    async def events(ws: WebSocket, key: str):
        if token and ws.query_params.get("token") != token:
            await ws.close(code=4401)
            return
        await ws.accept()
        q = mgr.subscribe(key)
        try:
            st = mgr.states.get(key)
            await ws.send_json({"type": "snapshot", "job": key, "state": st.public(with_events=True) if st else None})
            while True:
                event = await q.get()
                await ws.send_json(event)
        except WebSocketDisconnect:
            pass
        finally:
            mgr.unsubscribe(key, q)

    # -------------------------------------------------------------- first-run setup
    @app.get("/setup")
    def setup_status():
        return {"running": setup.running(), "components": setup.status()}

    @app.post("/setup/install", status_code=202)
    def setup_install(body: InstallBody):
        try:
            setup.install(body.ids)
        except RuntimeError as e:
            raise HTTPException(409, str(e)) from e
        return {"running": True}

    @app.post("/setup/cancel")
    def setup_cancel():
        setup.cancel()
        return {"ok": True}

    # -------------------------------------------------------------- glossaries
    @app.get("/glossaries")
    def glossaries():
        if not paths.glossaries.exists():
            return []
        return sorted(p.stem for p in paths.glossaries.glob("*.json"))

    @app.get("/glossaries/{name}")
    def get_glossary(name: str):
        f = _glossary_file(name)
        if not f.exists():
            raise HTTPException(404, "not found")
        return {"name": name, "entries": json.loads(f.read_text(encoding="utf-8"))}

    @app.put("/glossaries/{name}")
    def put_glossary(name: str, body: GlossaryBody):
        f = _glossary_file(name)
        f.parent.mkdir(parents=True, exist_ok=True)
        write_json_atomic(f, [e.model_dump() for e in body.entries])
        return {"ok": True}

    @app.delete("/glossaries/{name}")
    def delete_glossary(name: str):
        _glossary_file(name).unlink(missing_ok=True)
        return {"ok": True}

    return app


def serve(host: str = "127.0.0.1", port: int = 8765, parent_pid: int | None = None) -> None:
    import uvicorn

    from jamak.core.proc import exit_when_parent_dies, free_port

    if host not in ("127.0.0.1", "localhost"):
        raise SystemExit("refusing to bind to a non-loopback address")
    if parent_pid:
        exit_when_parent_dies(parent_pid)
    if port == 0:
        port = free_port()
    print(f"JAMAK_LISTENING http://{host}:{port}", flush=True)
    uvicorn.run(create_app(), host=host, port=port, log_level="warning")
