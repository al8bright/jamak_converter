"""Command line entry point: `jamak transcribe`, `jamak doctor`, `jamak serve`."""

from __future__ import annotations

import argparse
import sys
import threading
import time
from pathlib import Path

from jamak import __version__
from jamak.config import GlossaryEntry, PipelineOptions, default_llm_path, paths
from jamak.core import providers


def load_glossary(path: Path) -> list[GlossaryEntry]:
    """A glossary file (.json, .csv, or .txt with one `ja=ko` per line), or the name of a saved glossary."""
    from jamak.core import glossary as gl

    if not path.exists() and gl.NAME_RE.fullmatch(str(path)):
        return gl.load(str(path)).entries
    return gl.parse_file(path)[0]


def parse_time(text: str) -> float:
    """'90', '1:30', '0:01:30', '1:30.5' -> seconds."""
    parts = [float(p) for p in text.strip().split(":")]
    sec = 0.0
    for p in parts:
        sec = sec * 60 + p
    return sec


def parse_range(text: str) -> tuple[float, float]:
    a, b = text.split("-", 1)
    start, end = parse_time(a), parse_time(b)
    if end <= start:
        raise argparse.ArgumentTypeError(f"bad range {text!r}")
    return start, end


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
    if args.song:
        opts.songs.ranges = [parse_range(r) for r in args.song]
    if args.song_mode:
        opts.songs.mode = args.song_mode
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
    explicit_engine = bool(args.llm_model or args.llm_url or (args.options and not args.profile))
    if args.profile or not explicit_engine:
        from jamak.core.store import store
        from jamak.core.translate.factory import resolve_options

        prof = store.find_profile(args.profile) if args.profile else store.default_profile()
        if args.profile and prof is None:
            print(f"error: no translation profile {args.profile!r} (see: jamak profiles list)", file=sys.stderr)
            return 2
        if prof is not None:
            parallel, batch = opts.translate.parallel, opts.translate.batch_size
            opts = resolve_options(opts, prof.id)
            if args.parallel:
                opts.translate.parallel = parallel
            if args.batch_size:
                opts.translate.batch_size = batch
            print(f"translation profile: {prof.name} ({prof.kind})")
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
        cost = f", ~${s['cost_usd']:.3f}" if s.get("cost_usd") else ""
        print(f"translate: {s['requests']} requests, {s['prompt_tokens']} in / {s['completion_tokens']} out tokens"
              f"{cost}, {s['retries']} retries, {s['splits']} splits, {s['failed_lines']} failed lines, "
              f"{s.get('refused_lines', 0)} refused")
    for p in res.outputs:
        print(f"wrote {p}")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    from jamak.core import audio, cuda

    ok = True

    def row(name: str, good: bool, detail: str) -> None:
        nonlocal ok
        ok &= good
        print(f"  [{'OK' if good else '!!'}] {name:<16} {detail}")

    print(f"jamak {__version__}   home: {paths.home}")
    for tool in ("ffmpeg", "ffprobe"):
        try:
            p = audio._tool(tool)
        except audio.FfmpegError:
            p = None
        row(tool, bool(p), p or "not found (run the first-time setup in the app)")
    n = cuda.cuda_device_count()
    row("CUDA (STT)", n > 0, f"{n} device(s)" if n else "no CUDA device for CTranslate2 (STT will use CPU)")
    exe = paths.llama_server_exe
    row("llama-server", exe.exists(), str(exe) if exe.exists() else "missing - run scripts/setup_llama.ps1")
    m = default_llm_path()
    row("translation LLM", m is not None, str(m) if m else f"no *.gguf in {paths.models}")
    return 0 if ok else 1


# ---------------------------------------------------------------------- keys / profiles


def cmd_keys(args: argparse.Namespace) -> int:
    import getpass

    from jamak.core.store import StoreError, store

    if args.action == "list":
        keys = store.list_keys()
        if not keys:
            print("no API keys")
        for k in keys:
            print(f"  {k['id']:>3}  {k['provider']:<13} {k['label']:<20} ••••{k['last4']}  {k['created_at'][:10]}")
        return 0
    if args.action == "add":
        secret = getpass.getpass(f"{args.provider} API key (hidden): ")
        try:
            k = store.add_key(args.provider, args.label or args.provider, secret)
        except StoreError as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
        print(f"saved key {k['id']} (••••{k['last4']}), encrypted with Windows DPAPI")
        return 0
    if not store.remove_key(args.id):
        print(f"error: no key {args.id}", file=sys.stderr)
        return 2
    print(f"removed key {args.id}")
    return 0


def cmd_profiles(args: argparse.Namespace) -> int:
    from jamak.core.store import ProfileInput, ProfileOptions, StoreError, store

    def find():
        p = store.find_profile(args.name)
        if p is None:
            print(f"error: no profile {args.name!r}", file=sys.stderr)
        return p

    if args.action == "list":
        default = store.default_profile_id()
        for p in store.list_profiles():
            key = store.key_info(p.api_key_id)
            extra = " · ".join(x for x in (p.model, p.base_url, key and f"key ••••{key['last4']}") if x)
            mark = "*" if p.id == default else " "
            print(f" {mark}{p.id:>3}  {p.name:<24} {p.kind:<15} {extra}")
        print("  (* = default)")
        return 0
    if args.action == "add":
        opts = ProfileOptions(**{k: v for k, v in (("parallel", args.parallel), ("batch_size", args.batch_size),
                                                    ("effort", args.effort)) if v is not None})
        try:
            p = store.add_profile(ProfileInput(name=args.name, kind=args.kind, model=args.model,
                                               base_url=args.url, api_key_id=args.key, options=opts))
        except (StoreError, ValueError) as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
        print(f"added profile {p.id}: {p.name}")
        return 0
    p = find()
    if p is None:
        return 2
    if args.action == "set-default":
        store.set_default(p.id)
        print(f"default profile: {p.name}")
    elif args.action == "remove":
        store.remove_profile(p.id)
        print(f"removed profile {p.name}")
    elif args.action == "test":
        from jamak.core.translate.factory import test_profile

        res = test_profile(p)
        print(("OK  " if res["ok"] else "FAIL ") + res["message"])
        return 0 if res["ok"] else 1
    return 0


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
    t.add_argument("--stt-model", help="large-v3 (default) | large-v3-turbo | kotoba-whisper-v2.0 | HF repo id")
    t.add_argument("--cpu", action="store_true", help="run STT on CPU")
    t.add_argument("--profile", help="translation profile name or id (default: the default profile)")
    t.add_argument("--llm-model", help="GGUF path for the managed llama-server")
    t.add_argument("--llm-url", help="external OpenAI-compatible base URL (e.g. http://127.0.0.1:11434/v1)")
    t.add_argument("--llm-name", help="model name for the external server")
    t.add_argument("--parallel", type=int, help="concurrent translation batches")
    t.add_argument("--batch-size", type=int)
    t.add_argument("--glossary", help="glossary .json or .txt (ja=ko per line)")
    t.add_argument("--song", action="append", metavar="START-END",
                   help="opening/ending song range, e.g. 0:00-1:30 (repeatable)")
    t.add_argument("--song-mode", choices=["translate", "skip"], help="song lines: mark with ♪ or drop")
    t.add_argument("--formats", help="comma list of srt,ass,vtt (default srt)")
    t.add_argument("--ja", action="store_true", help="also write Japanese subtitles")
    t.add_argument("--dual", action="store_true", help="also write ja-ko dual ASS")
    t.add_argument("--out", help="output folder (default: next to the video)")
    t.add_argument("--force", choices=["audio", "separate", "stt", "segment", "translate"],
                   help="re-run from this stage even if cached")
    t.set_defaults(fn=cmd_transcribe)

    k = sub.add_parser("keys", help="manage API keys (stored encrypted with Windows DPAPI)")
    ks = k.add_subparsers(dest="action", required=True)
    ks.add_parser("list")
    ka = ks.add_parser("add")
    ka.add_argument("provider", choices=[*providers.CLOUD_KINDS, "openai_compat"])
    ka.add_argument("--label")
    kr = ks.add_parser("remove")
    kr.add_argument("id", type=int)
    k.set_defaults(fn=cmd_keys)

    pr = sub.add_parser("profiles", help="manage translation profiles")
    ps = pr.add_subparsers(dest="action", required=True)
    ps.add_parser("list")
    pa = ps.add_parser("add")
    pa.add_argument("name")
    pa.add_argument("kind", choices=["local_managed", "local_external", *providers.CLOUD_KINDS])
    pa.add_argument("--model", help="GGUF path, external model name, or API model ID (e.g. claude-opus-5-5)")
    pa.add_argument("--url", help="local_external: OpenAI-compatible base URL")
    pa.add_argument("--key", type=int, help="API key id (see: jamak keys list)")
    pa.add_argument("--parallel", type=int)
    pa.add_argument("--batch-size", type=int)
    pa.add_argument("--effort", choices=["low", "medium", "high"])
    for action in ("set-default", "remove", "test"):
        ps.add_parser(action).add_argument("name", help="profile name or id")
    pr.set_defaults(fn=cmd_profiles)

    d = sub.add_parser("doctor", help="check ffmpeg, CUDA, llama-server, models")
    d.set_defaults(fn=cmd_doctor)

    s = sub.add_parser("serve", help="run the local API server")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--parent-pid", type=int, help="exit when this process exits (Tauri)")
    s.set_defaults(fn=cmd_serve)

    args = ap.parse_args(argv)
    sys.exit(args.fn(args))
