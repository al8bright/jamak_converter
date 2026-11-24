"""Write SRT / ASS / VTT files with pysubs2."""

from __future__ import annotations

from pathlib import Path

import pysubs2

from jamak.config import OutputOptions
from jamak.core.types import Line


def wrap_ko(text: str, max_chars: int) -> str:
    """Wrap a Korean line into two lines at the space nearest the middle."""
    text = " ".join(text.split())
    if len(text) <= max_chars or "\n" in text:
        return text
    mid = len(text) // 2
    spaces = [i for i, c in enumerate(text) if c == " "]
    if spaces:
        cut = min(spaces, key=lambda i: abs(i - mid))
        return text[:cut] + "\n" + text[cut + 1 :]
    return text[:mid] + "\n" + text[mid:]


def _ms(t: float) -> int:
    return int(round(t * 1000))


def _to_ass_text(text: str) -> str:
    return text.replace("\n", "\\N")


def build(lines: list[Line], lang: str, ko_max: int) -> pysubs2.SSAFile:
    subs = pysubs2.SSAFile()
    for ln in lines:
        text = ln.ko if lang == "ko" else ln.ja
        if lang == "ko":
            text = wrap_ko(text, ko_max)
        if not text.strip():
            continue
        subs.append(pysubs2.SSAEvent(start=_ms(ln.start), end=_ms(ln.end), text=_to_ass_text(text)))
    return subs


def build_dual_ass(lines: list[Line], ko_max: int) -> pysubs2.SSAFile:
    subs = pysubs2.SSAFile()
    subs.info["PlayResX"] = "1920"
    subs.info["PlayResY"] = "1080"
    base = dict(fontname="Malgun Gothic", bold=False, outline=3.0, shadow=0.0, marginv=60)
    subs.styles["KO"] = pysubs2.SSAStyle(fontsize=64, alignment=pysubs2.Alignment.BOTTOM_CENTER, **base)
    ja = dict(base, fontname="Yu Gothic UI", marginv=40)
    subs.styles["JA"] = pysubs2.SSAStyle(
        fontsize=46, alignment=pysubs2.Alignment.TOP_CENTER, primarycolor=pysubs2.Color(220, 220, 220), **ja
    )
    for ln in lines:
        if ln.ko.strip():
            subs.append(pysubs2.SSAEvent(start=_ms(ln.start), end=_ms(ln.end), style="KO",
                                         text=_to_ass_text(wrap_ko(ln.ko, ko_max))))
        if ln.ja.strip():
            subs.append(pysubs2.SSAEvent(start=_ms(ln.start), end=_ms(ln.end), style="JA",
                                         text=_to_ass_text(ln.ja)))
    return subs


def export(lines: list[Line], video: Path, opts: OutputOptions) -> list[Path]:
    out_dir = Path(opts.out_dir) if opts.out_dir else video.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = video.stem
    written: list[Path] = []
    for fmt in opts.formats:
        ko = build(lines, "ko", opts.ko_max_line_chars)
        p = out_dir / f"{stem}.ko.{fmt}"
        ko.save(str(p), format_=fmt, encoding="utf-8")
        written.append(p)
        if opts.include_ja:
            ja = build(lines, "ja", opts.ko_max_line_chars)
            p = out_dir / f"{stem}.ja.{fmt}"
            ja.save(str(p), format_=fmt, encoding="utf-8")
            written.append(p)
    if opts.dual_ass:
        p = out_dir / f"{stem}.ja-ko.ass"
        build_dual_ass(lines, opts.ko_max_line_chars).save(str(p), format_="ass", encoding="utf-8")
        written.append(p)
    return written
