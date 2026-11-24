"""Command line entry point: `jamak transcribe`, `jamak doctor`, `jamak serve`."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import threading
import time
from pathlib import Path

from jamak import __version__
from jamak.config import GlossaryEntry, PipelineOptions, default_llm_path, paths


def load_glossary(path: Path) -> list[GlossaryEntry]:
    """JSON list of {ja, ko, note?} or a text file with one `ja=ko` per line."""
    text = path.read_text(encoding="utf-8-sig")
    if path.suffix.lower() == ".json":
        data = json.loads(text)
        if isinstance(data, dict):
            data = data.get("entries", [])
        return [GlossaryEntry.model_validate(d) for d in data]
    out = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        ja, ko = line.split("=", 1)
        out.append(GlossaryEntry(ja=ja.strip(), ko=ko.strip()))
    return out


def build_options(args: argparse.Namespace) -> PipelineOptions:
    if args.options:
        opts = PipelineOptions.model_validate_json(Path(args.options).read_text(encoding="utf-8"))
    else:
        opts = PipelineOptions()
    if args.track is not None:
        opts.audio.track = args.track
    if args.separate is not None:
        opts.separate.enabled = args.separate
    if args.stt_model:
        opts.stt.model = args.stt_model
    if args.cpu:
        opts.stt.device = "cpu"
    if args.llm_model:
        opts.translate.model_path = args.llm_model
    if args.llm_url:
        opts.translate.mode = "external"
        opts.translate.base_url = args.llm_url
        if args.llm_name:
            opts.translate.model_name = args.llm_name
    if args.parallel:
        opts.translate.parallel = args.parallel
    if args.batch_size:
        opts.translate.batch_size = args.batch_size
    if args.formats:
        opts.output.formats = [f.strip() for f in args.formats.split(",") if f.strip()]
    if args.ja:
        opts.output.include_ja = True
    if args.dual:
        opts.output.dual_ass = True
    if args.out:
        opts.output.out_dir = args.out
    if args.glossary:
        opts.glossary = load_glossary(Path(args.glossary))
    return opts


class ConsoleProgress:
    def __init__(self) -> None:
        self.last = 0.0
        self.t0 = time.perf_counter()

    def __call__(self, e: dict) -> None:
        st, stage = e["status"], e["stage"]
        now = time.perf_counter()
        if st == "progress" and now - self.last < 0.5:
            return
        self.last = now
        pct = f"{e['overall'] * 100:5.1f}%"
        msg = e.get("message") or ""
        if st == "progress":
            line = f"[{pct}] {stage:<9} {e['progress'] * 100:5.1f}%  {msg}"
        else:
            line = f"[{pct}] {stage:<9} {st}  {msg}"
        print(line, flush=True)


def cmd_transcribe(args: argparse.Namespace) -> int:
    from jamak.core.pipeline import Cancelled, Pipeline, save_options

    video = Path(args.video)
    if not video.exists():
        print(f"error: {video} not found", file=sys.stderr)
        return 2
    opts = build_options(args)
    paths.ensure()
    cancel = threading.Event()
    pipe = Pipeline(video, opts, on_event=ConsoleProgress(), cancel=cancel)
    save_options(pipe.job, opts)
    print(f"job {pipe.job.key}  ({pipe.job.dir})")
    try:
        res = pipe.run(force_from=args.force)
    except (KeyboardInterrupt, Cancelled):
        cancel.set()
        print("cancelled", file=sys.stderr)
        return 130
    print("\ntimings: " + ", ".join(f"{k} {v:.1f}s" for k, v in res.timings.items()))
    if res.skipped:
        print("cached:  " + ", ".join(res.skipped))
    if "translate" in res.stats:
        s = res.stats["translate"]
        print(f"translate: {s['requests']} requests, {s['completion_tokens']} tokens, "
              f"{s['retries']} retries, {s['splits']} splits, {s['failed_lines']} failed lines")
    for p in res.outputs:
        print(f"wrote {p}")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    from jamak.core import cuda

    ok = True

    def row(name: str, good: bool, detail: str) -> None:
        nonlocal ok
        ok &= good
        print(f"  [{'OK' if good else '!!'}] {name:<16} {detail}")

    print(f"jamak {__version__}   home: {paths.home}")
    for tool in ("ffmpeg", "ffprobe"):
        p = shutil.which(tool)
        row(tool, bool(p), p or "not found on PATH")
    n = cuda.cuda_device_count()
    row("CUDA (STT)", n > 0, f"{n} device(s)" if n else "no CUDA device for CTranslate2 (STT will use CPU)")
    exe = paths.llama_server_exe
    row("llama-server", exe.exists(), str(exe) if exe.exists() else "missing - run scripts/setup_llama.ps1")
    m = default_llm_path()
    row("translation LLM", m is not None, str(m) if m else f"no *.gguf in {paths.models}")
    return 0 if ok else 1


def cmd_serve(args: argparse.Namespace) -> int:
    from jamak.api.server import serve

    serve(host=args.host, port=args.port, parent_pid=args.parent_pid)
    return 0


def main(argv: list[str] | None = None) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(prog="jamak", description="Japanese -> Korean subtitle generator")
    ap.add_argument("--version", action="version", version=__version__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    t = sub.add_parser("transcribe", help="make Korean subtitles for a video")
    t.add_argument("video")
    t.add_argument("--options", help="PipelineOptions JSON file")
    t.add_argument("--track", type=int, help="audio track index (default: Japanese track)")
    sep = t.add_mutually_exclusive_group()
    sep.add_argument("--separate", dest="separate", action="store_true", default=None, help="vocal separation (Demucs)")
    sep.add_argument("--no-separate", dest="separate", action="store_false")
    t.add_argument("--stt-model", help="Whisper model (default large-v3)")
    t.add_argument("--cpu", action="store_true", help="run STT on CPU")
    t.add_argument("--llm-model", help="GGUF path for the managed llama-server")
    t.add_argument("--llm-url", help="external OpenAI-compatible base URL (e.g. http://127.0.0.1:11434/v1)")
    t.add_argument("--llm-name", help="model name for the external server")
    t.add_argument("--parallel", type=int, help="concurrent translation batches")
    t.add_argument("--batch-size", type=int)
    t.add_argument("--glossary", help="glossary .json or .txt (ja=ko per line)")
    t.add_argument("--formats", help="comma list of srt,ass,vtt (default srt)")
    t.add_argument("--ja", action="store_true", help="also write Japanese subtitles")
    t.add_argument("--dual", action="store_true", help="also write ja-ko dual ASS")
    t.add_argument("--out", help="output folder (default: next to the video)")
    t.add_argument("--force", choices=["audio", "separate", "stt", "segment", "translate"],
                   help="re-run from this stage even if cached")
    t.set_defaults(fn=cmd_transcribe)

    d = sub.add_parser("doctor", help="check ffmpeg, CUDA, llama-server, models")
    d.set_defaults(fn=cmd_doctor)

    s = sub.add_parser("serve", help="run the local API server")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--parent-pid", type=int, help="exit when this process exits (Tauri)")
    s.set_defaults(fn=cmd_serve)

    args = ap.parse_args(argv)
    sys.exit(args.fn(args))
