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
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from jamak import __version__
from jamak.api.jobs import JobManager, list_job_dirs, load_options, purge_interrupted, purge_job
from jamak.config import GlossaryEntry, OutputOptions, PipelineOptions, default_llm_path, paths
from jamak.core.cache import Job, write_json_atomic
from jamak.core.pipeline import save_options
from jamak.core.proc import CREATE_NO_WINDOW
from jamak.core.store import Profile, ProfileInput, StoreError, store
from jamak.core.translate.base import EngineError
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
    profile_id: int | None = None  # None: the default profile


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
    profile_id: int | None = None  # None: the profile the job was made with


class ExportRequest(BaseModel):
    output: OutputOptions | None = None


class PathBody(BaseModel):
    path: str


class GlossaryBody(BaseModel):
    entries: list[GlossaryEntry]
    aliases: list[str] | None = None  # None: keep the current aliases


class GlossaryImport(BaseModel):
    path: str
    name: str | None = None  # default: the file name
    mode: Literal["new", "replace", "merge"] = "new"


class NameBody(BaseModel):
    name: str = Field(min_length=1, max_length=64)


class InstallBody(BaseModel):
    ids: list[str] | None = None


class RemoveBody(BaseModel):
    id: str


class KeyBody(BaseModel):
    provider: str = Field(min_length=1, max_length=40)  # core/providers.py id, or openai_compat
    label: str = Field("", max_length=60)
    secret: str = Field(min_length=1, max_length=1000, repr=False)  # Papago: "<client id>\n<secret>"


class DefaultProfileBody(BaseModel):
    profile_id: int


class UiSettings(BaseModel):
    palette: Literal["seoul", "acid", "ice", "blade"] = "seoul"
    sidebar: Literal["open", "hidden"] = "open"
    cloud_notice_seen: bool = False


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


def _entry_map(entries: list[GlossaryEntry]) -> dict[str, tuple[str, str]]:
    return {e.ja: (e.ko, e.note) for e in entries}


def rebuild_glossary(opts: PipelineOptions) -> tuple[list[GlossaryEntry], list[GlossaryEntry]]:
    """(new job glossary, new base): current named glossaries + the edits this job made to its base."""
    from jamak.core import glossary as gl

    base_now, _ = gl.merge(opts.glossary_names)
    old_base = _entry_map(opts.glossary_base)
    mine = _entry_map(opts.glossary)
    removed = {ja for ja in old_base if ja not in mine}
    edits = [e for e in opts.glossary if old_base.get(e.ja) != (e.ko, e.note)]
    merged, _ = gl.merge([], [e for e in base_now if e.ja not in removed] + edits)
    return merged, base_now


def glossary_status(opts: PipelineOptions) -> dict[str, Any]:
    from jamak.core import glossary as gl

    if not opts.glossary_names:
        return {"names": [], "changed": False, "missing": [], "count": len(opts.glossary)}
    now, missing = gl.merge(opts.glossary_names)
    def key(entries: list[GlossaryEntry]) -> list[tuple[str, str, str, bool]]:
        return [(e.ja, e.ko, e.note, e.hint) for e in entries]

    changed = key(now) != key(opts.glossary_base)
    return {"names": opts.glossary_names, "changed": changed, "missing": missing, "count": len(opts.glossary)}


def _gpu_info() -> list[dict[str, Any]]:
    from jamak import hw

    return hw.gpus()


def _ffmpeg_path() -> str | None:
    from jamak.core.audio import FfmpegError, ffmpeg_exe

    try:
        return ffmpeg_exe()
    except FfmpegError:
        return None


def system_info() -> dict[str, Any]:
    from jamak import hw, models
    from jamak.core import cuda, separate
    from jamak.installer import present

    llama = {"path": str(paths.llama_server_exe), "exists": paths.llama_server_exe.exists()}
    vfile = paths.llama_dir / "version.json"
    if vfile.exists():
        try:
            llama["build"] = json.loads(vfile.read_text(encoding="utf-8-sig")).get("build")
        except json.JSONDecodeError:
            pass
    known = {s.gguf[1]: s.name for s in models.SPECS if s.kind == "llm"}
    ggufs = []
    if paths.models.exists():
        for p in sorted(paths.models.glob("*.gguf")):
            if not p.name.startswith("mmproj"):
                ggufs.append({"name": known.get(p.name, p.name), "path": str(p),
                              "size_gb": round(p.stat().st_size / 1e9, 2)})
    default = default_llm_path()
    from jamak.api.checks import cpu_name, short_cpu

    return {
        "cpu_name": short_cpu(cpu_name()),
        "version": __version__,
        "home": str(paths.home),
        "ffmpeg": _ffmpeg_path(),
        "platform": hw.current(),
        "accelerator": hw.accelerator(),
        "cuda_devices": cuda.cuda_device_count(),
        "gpus": _gpu_info(),
        "llama_server": llama,
        "llm_models": ggufs,
        "default_llm": str(default) if default else None,
        "stt_models": models.STT_IDS,
        "stt_installed": [m for m in models.STT_IDS if present(m)],
        "separation_available": separate.available(),
    }


def profile_public(p: Profile) -> dict[str, Any]:
    from jamak.api.checks import profile_status

    d = p.model_dump()
    d["cloud"] = p.cloud
    d["key"] = store.key_info(p.api_key_id)
    d["status"] = profile_status(p)
    d["is_default"] = store.default_profile_id() == p.id
    return d


def _store_call(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except StoreError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:
        if type(e).__name__ == "SecretError":
            raise HTTPException(400, str(e)) from e
        raise


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
        # the app was closed in the middle of these jobs: they are deleted, not resumed
        gone = await asyncio.to_thread(purge_interrupted, mgr.busy)
        if gone:
            print(f"deleted {len(gone)} interrupted job(s): {', '.join(gone)}", file=sys.stderr)
        if not setup.running():
            from jamak.installer import cleanup_leftovers

            await asyncio.to_thread(cleanup_leftovers)
        yield
        # Normal shutdown (SIGTERM from the app on macOS, Ctrl+C). Stop llama-server here and not only
        # in atexit: Python waits for worker threads before atexit, and the app kills us after 3 s.
        from jamak.core.proc import kill_children

        mgr.close()
        kill_children()

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

    @app.get("/system/usage")
    def system_usage():
        from jamak.api.checks import usage

        return usage()

    @app.get("/system/checks")
    def system_checks():
        from datetime import datetime

        from jamak.api.checks import run_checks

        return {"checked_at": datetime.now().isoformat(timespec="seconds"),
                "checks": run_checks(system_info(), setup.status())}

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
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", str(p)])  # Finder, with the file selected
        return {"ok": True}

    class UrlBody(BaseModel):
        url: str

    @app.post("/system/open-url")
    def open_url(body: UrlBody):
        """Open a provider's key or pricing page in the default browser (only those known URLs)."""
        from jamak.core import providers

        allowed = {u for pv in providers.PROVIDERS for u in (pv.key_url, pv.pricing_url) if u}
        if body.url not in allowed:
            raise HTTPException(400, "열 수 없는 주소입니다")
        import webbrowser

        webbrowser.open(body.url)
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
        from jamak.models import ModelMissing, require_stt

        if body.force_from not in ("segment", "translate"):  # those reuse the cached transcript
            try:
                require_stt(opts.stt.model)
            except ModelMissing as e:
                raise HTTPException(409, str(e)) from e
        if opts.glossary_names and not opts.glossary_base:  # a new job (a rerun keeps its own snapshot)
            from jamak.core import glossary as gl

            opts.glossary_base, missing = gl.merge(opts.glossary_names)
            if missing:
                raise HTTPException(400, f"용어집이 없습니다: {', '.join(missing)}")
            if not opts.glossary:
                opts.glossary = list(opts.glossary_base)
        try:
            st = mgr.submit_run(p, opts, body.force_from, profile_id=body.profile_id)
        except EngineError as e:
            raise HTTPException(400, str(e)) from e
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
            st = mgr.submit_retranslate(key, body.ids, profile_id=body.profile_id)
        except EngineError as e:
            raise HTTPException(400, str(e)) from e
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
    def setup_status(sizes: bool = False):
        from jamak import hw
        from jamak.installer import recommended_stt

        gpus = _gpu_info()
        vram = round(gpus[0]["memory_total_mb"] / 1024, 1) if gpus else None
        return {"running": setup.running(), "components": setup.status(sizes=sizes), "ready": setup.ready(),
                "vram_gb": vram, "recommended_stt": recommended_stt(vram),
                "platform": hw.current(), "accelerator": hw.accelerator(),
                "free_bytes": shutil.disk_usage(paths.home if paths.home.exists() else paths.home.anchor).free}

    @app.post("/setup/install", status_code=202)
    def setup_install(body: InstallBody):
        try:
            queued = setup.install(body.ids)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        except RuntimeError as e:
            raise HTTPException(409, str(e)) from e
        return {"running": True, "queued": queued}

    @app.post("/setup/remove")
    def setup_remove(body: RemoveBody):
        if any(mgr.busy(k) for k in list(mgr.states)):
            raise HTTPException(409, "작업이 끝난 뒤 지우세요")
        try:
            setup.remove(body.id)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        except RuntimeError as e:
            raise HTTPException(409, str(e)) from e
        return {"ok": True}

    @app.post("/setup/cancel")
    def setup_cancel():
        setup.cancel()
        return {"ok": True}

    # -------------------------------------------------------------- settings (SQLite)
    @app.get("/settings/api-keys")
    def list_keys():
        return store.list_keys()  # id, provider, label, last4, created_at: never the key itself

    @app.post("/settings/api-keys", status_code=201)
    def add_key(body: KeyBody):
        return _store_call(store.add_key, body.provider, body.label, body.secret)

    @app.delete("/settings/api-keys/{key_id}")
    def remove_key(key_id: int):
        if not store.remove_key(key_id):
            raise HTTPException(404, "API 키가 없습니다")
        return {"ok": True}

    @app.get("/settings/translation-profiles")
    def list_profiles():
        return [profile_public(p) for p in store.list_profiles()]

    @app.post("/settings/translation-profiles", status_code=201)
    def add_profile(body: ProfileInput):
        from jamak.api.checks import profile_status

        p = _store_call(store.add_profile, body)
        default = store.default_profile()
        if p.cloud and (default is None or not profile_status(default)["ready"]):
            store.set_default(p.id)  # the local engine is not installed: the first cloud profile becomes the default
        return profile_public(p)

    @app.put("/settings/translation-profiles/{profile_id}")
    def update_profile(profile_id: int, body: ProfileInput):
        return profile_public(_store_call(store.update_profile, profile_id, body))

    @app.delete("/settings/translation-profiles/{profile_id}")
    def remove_profile(profile_id: int):
        if len(store.list_profiles()) <= 1:
            raise HTTPException(400, "프로필이 하나는 있어야 합니다")
        if not store.remove_profile(profile_id):
            raise HTTPException(404, "프로필이 없습니다")
        return {"ok": True}

    @app.post("/settings/translation-profiles/{profile_id}/test")
    def test_profile(profile_id: int):
        from jamak.core.translate.factory import test_profile as run_test

        p = store.get_profile(profile_id)
        if p is None:
            raise HTTPException(404, "프로필이 없습니다")
        if p.kind == "local_managed" and any(mgr.busy(k) for k in list(mgr.states)):
            raise HTTPException(409, "작업이 GPU를 쓰는 중입니다. 작업이 끝난 뒤 테스트하세요")
        res = run_test(p)
        return {**res, "profile": profile_public(store.get_profile(profile_id))}

    @app.put("/settings/default-profile")
    def set_default_profile(body: DefaultProfileBody):
        _store_call(store.set_default, body.profile_id)
        return {"ok": True}

    @app.get("/settings/providers")
    def list_providers():
        from jamak.core import providers

        return [providers.public(p) for p in providers.PROVIDERS]

    def _secret_for(kind: str, key_id: int | None) -> str:
        from jamak.core import providers

        info = providers.get(kind)
        try:
            secret = store.key_secret(key_id) if key_id is not None else next(
                (os.environ[n] for n in (info.env if info else ()) if os.environ.get(n)), None)
        except StoreError as e:
            raise HTTPException(400, str(e)) from e
        if not secret:
            raise HTTPException(400, f"{info.name if info else kind} 키를 먼저 고르세요")
        return secret

    @app.get("/settings/models")
    def list_models(kind: str, key_id: int | None = None):
        from jamak.core import providers

        if kind == "local_managed":
            return {"models": [m["path"] for m in system_info()["llm_models"]]}
        if kind == "anthropic":
            from jamak.core.translate.anthropic_api import MODELS

            return {"models": MODELS}
        info = providers.get(kind)
        if info is None:
            raise HTTPException(400, "알 수 없는 제공자입니다")
        if not info.lists_models:
            return {"models": list(info.models)}
        secret = _secret_for(kind, key_id)
        try:
            if info.family == "gemini":
                from jamak.core.translate.gemini_api import list_models as gemini_models

                return {"models": gemini_models(secret)}
            from jamak.core.translate.openai_compat import list_models as compat_models

            return {"models": compat_models(info.base_url or "", secret)}
        except Exception as e:  # never echo the key: only the exception type
            raise HTTPException(502, f"모델 목록을 가져오지 못했습니다 ({type(e).__name__})") from e
        finally:
            del secret

    @app.post("/settings/api-keys/{key_id}/check")
    def check_key(key_id: int):
        """Ask the service whether the key works (lists models, or the usage for DeepL)."""
        from jamak.core import providers

        k = store.key_info(key_id)
        if k is None:
            raise HTTPException(404, "API 키가 없습니다")
        info = providers.get(k["provider"])
        if info is None:
            return {"ok": True, "message": "외부 서버 키는 프로필 연결 테스트로 확인하세요"}
        secret = _secret_for(info.id, key_id)
        try:
            if info.family == "anthropic":
                import anthropic

                n = len(anthropic.Anthropic(api_key=secret, max_retries=0, timeout=20).models.list(limit=20).data)
                msg = f"키 확인됨 · 모델 {n}개"
            elif info.family == "gemini":
                from jamak.core.translate.gemini_api import list_models as gemini_models

                msg = f"키 확인됨 · 모델 {len(gemini_models(secret))}개"
            elif info.family == "openai":
                from jamak.core.translate.openai_compat import list_models as compat_models

                msg = f"키 확인됨 · 모델 {len(compat_models(info.base_url or '', secret))}개"
            elif info.family == "deepl":
                from jamak.core.translate.mt_api import deepl_usage

                u = deepl_usage(secret)
                msg = f"키 확인됨 · 이번 달 {u['characters']:,} / {u['limit']:,}자 사용" if u["limit"] else "키 확인됨"
            else:
                from jamak.config import TranslateOptions
                from jamak.core.translate.mt_api import PapagoTranslator

                eng = PapagoTranslator(TranslateOptions(request_timeout=20), secret)
                try:
                    msg = f"키 확인됨 · “テスト” → “{eng._one('テスト')}”"
                finally:
                    eng.close()
            return {"ok": True, "message": msg}
        except Exception as e:
            text = str(e)
            for part in secret.split("\n"):  # defensive: an error message must never carry the key
                if part and part in text:
                    text = text.replace(part, "••••")
            return {"ok": False, "message": f"{type(e).__name__}: {text[:200]}"}
        finally:
            del secret

    @app.get("/settings/ui")
    def get_ui():
        raw = store.get_setting("ui")
        return UiSettings.model_validate_json(raw) if raw else UiSettings()

    @app.put("/settings/ui")
    def put_ui(body: UiSettings):
        store.set_setting("ui", body.model_dump_json())
        return body

    # -------------------------------------------------------------- glossaries
    from jamak.core import glossary as gl

    def _gl(fn, *a, **kw):
        try:
            return fn(*a, **kw)
        except FileNotFoundError as e:
            raise HTTPException(404, "용어집이 없습니다") from e
        except gl.GlossaryError as e:
            raise HTTPException(400, str(e)) from e

    @app.get("/glossaries")
    def glossaries():
        return gl.list_all()

    @app.get("/glossaries/suggest")
    def suggest_glossaries(filename: str):
        return {"names": gl.suggest(filename)}

    @app.post("/glossaries/import", status_code=201)
    def import_glossary(body: GlossaryImport):
        p = Path(body.path)
        if not p.is_file():
            raise HTTPException(404, "파일이 없습니다")
        try:
            entries, aliases = gl.parse_file(p)
        except (gl.GlossaryError, ValueError, UnicodeDecodeError) as e:
            raise HTTPException(422, f"용어집 파일을 읽지 못했습니다: {e}") from e
        name = body.name or p.stem
        if body.mode == "new" and gl.exists(name):
            raise HTTPException(409, f"'{name}' 용어집이 이미 있습니다")
        if body.mode == "merge":
            g = _gl(gl.add_entries, name, entries)
        else:
            g = _gl(gl.save, gl.Glossary(name=_gl(gl.check_name, name), aliases=aliases, entries=entries))
        return {"name": g.name, "count": len(g.entries), "imported": len(entries)}

    @app.get("/glossaries/{name}")
    def get_glossary(name: str):
        return _gl(gl.load, name)

    @app.put("/glossaries/{name}")
    def put_glossary(name: str, body: GlossaryBody):
        g = gl.Glossary(name=_gl(gl.check_name, name), entries=body.entries, aliases=body.aliases or [])
        if body.aliases is None and gl.exists(name):
            g.aliases = _gl(gl.load, name).aliases
        return _gl(gl.save, g)

    @app.post("/glossaries/{name}/entries")
    def add_glossary_entries(name: str, body: GlossaryBody):
        return _gl(gl.add_entries, name, body.entries)

    @app.post("/glossaries/{name}/rename")
    def rename_glossary(name: str, body: NameBody):
        return _gl(gl.rename, name, body.name)

    @app.post("/glossaries/{name}/duplicate", status_code=201)
    def duplicate_glossary(name: str, body: NameBody):
        return _gl(gl.duplicate, name, body.name)

    @app.post("/glossaries/{name}/export")
    def export_glossary(name: str, body: PathBody):
        g = _gl(gl.load, name)
        out = Path(body.path)
        fmt = out.suffix.lower().lstrip(".")
        if fmt not in gl.FORMATS:
            raise HTTPException(400, "저장 형식은 .json, .csv, .txt 중 하나입니다")
        out.write_text(gl.render(g, fmt), encoding="utf-8")
        return {"written": str(out)}

    @app.delete("/glossaries/{name}")
    def delete_glossary(name: str):
        _gl(gl.delete, name)
        return {"ok": True}

    @app.get("/jobs/{key}/glossary")
    def job_glossary(key: str):
        return glossary_status(load_options(_job_or_404(key)))

    @app.post("/jobs/{key}/glossary/attach")
    def attach_job_glossary(key: str, body: NameBody):
        """Link one more named glossary to the job; the change shows up as "glossary changed"."""
        job = _job_or_404(key)
        if mgr.busy(key):
            raise HTTPException(409, "job is busy")
        _gl(gl.load, body.name)
        opts = load_options(job)
        if body.name not in opts.glossary_names:
            opts.glossary_names.append(body.name)
            save_options(job, opts)
        return glossary_status(opts)

    @app.post("/jobs/{key}/glossary/refresh", status_code=202)
    def refresh_job_glossary(key: str):
        """Rebuild the job's glossary from its named glossaries (keeping edits made for this job)
        and run again: only the stages the change affects run (translation, or STT if hints changed)."""
        job = _job_or_404(key)
        opts = load_options(job)
        if not opts.glossary_names:
            raise HTTPException(400, "이 작업은 이름 붙은 용어집을 쓰지 않습니다")
        opts.glossary, opts.glossary_base = rebuild_glossary(opts)
        try:
            st = mgr.submit_run(job.video, opts, None, profile_id=opts.translate.profile_id)
        except EngineError as e:
            raise HTTPException(400, str(e)) from e
        except RuntimeError as e:
            raise HTTPException(409, str(e)) from e
        return st.public()

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
    # 2 s: on SIGTERM, do not wait for an open WebSocket forever; the app kills us 3 s after asking
    uvicorn.run(create_app(), host=host, port=port, log_level="warning", timeout_graceful_shutdown=2)
