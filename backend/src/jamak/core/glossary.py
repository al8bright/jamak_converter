"""Named glossaries (one JSON file each under <home>/glossaries) and the helpers that use them.

File format (v2): {"name", "aliases": [...], "entries": [{ja, ko, note, hint}], "updated"}.
Files written by older versions are a bare list of entries and are still read.

- `merge()` builds a job's glossary from several named ones plus job-only entries.
- `hint_terms()` picks the terms used as a speech-recognition hint (entries marked `hint`,
  or the first 40 when nothing is marked, as before).
- `relevant()` keeps only entries that occur in the given texts (per-batch prompts).
- `suggest()` picks glossaries whose name or alias occurs in a video file name.
"""

from __future__ import annotations

import csv
import io
import json
import re
import unicodedata
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field

from jamak.config import GlossaryEntry, paths

NAME_RE = re.compile(r"[\w\-. ]{1,64}")
MAX_HINTS = 40  # the Whisper prompt holds about 224 tokens
FORMATS = ("json", "csv", "txt")


class GlossaryError(ValueError):
    pass


class Glossary(BaseModel):
    name: str
    aliases: list[str] = Field(default_factory=list)
    entries: list[GlossaryEntry] = Field(default_factory=list)
    updated: str = ""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def check_name(name: str) -> str:
    name = name.strip()
    if not NAME_RE.fullmatch(name) or name.startswith("."):
        raise GlossaryError("용어집 이름에는 글자, 숫자, 공백, - _ . 만 쓸 수 있습니다 (64자까지)")
    return name


def file_for(name: str) -> Path:
    return paths.glossaries / f"{check_name(name)}.json"


def norm(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold()


# ---------------------------------------------------------------------- files


def load(name: str) -> Glossary:
    f = file_for(name)
    if not f.exists():
        raise FileNotFoundError(name)
    data = json.loads(f.read_text(encoding="utf-8"))
    if isinstance(data, list):  # v1: bare list of entries
        mtime = datetime.fromtimestamp(f.stat().st_mtime, UTC).isoformat(timespec="seconds")
        return Glossary(name=name, entries=[GlossaryEntry.model_validate(e) for e in data], updated=mtime)
    g = Glossary.model_validate(data)
    g.name = name
    return g


def save(g: Glossary) -> Glossary:
    from jamak.core.cache import write_json_atomic

    f = file_for(g.name)
    f.parent.mkdir(parents=True, exist_ok=True)
    g.entries = clean(g.entries)
    g.aliases = [a.strip() for a in g.aliases if a.strip()]
    g.updated = _now()
    write_json_atomic(f, json.loads(g.model_dump_json()))
    return g


def exists(name: str) -> bool:
    return file_for(name).exists()


def list_all() -> list[dict]:
    if not paths.glossaries.exists():
        return []
    out = []
    for f in sorted(paths.glossaries.glob("*.json"), key=lambda p: p.stem.casefold()):
        try:
            g = load(f.stem)
        except (GlossaryError, ValueError, OSError):
            continue
        out.append({"name": g.name, "count": len(g.entries), "hints": sum(1 for e in g.entries if e.hint),
                    "aliases": g.aliases, "updated": g.updated})
    return out


def delete(name: str) -> None:
    file_for(name).unlink(missing_ok=True)


def rename(old: str, new: str) -> Glossary:
    g = load(old)
    new = check_name(new)
    if new != old and exists(new):
        raise GlossaryError(f"'{new}' 용어집이 이미 있습니다")
    g.name = new
    save(g)
    if new != old:
        delete(old)
    return g


def duplicate(name: str, new: str) -> Glossary:
    g = load(name)
    new = check_name(new)
    if exists(new):
        raise GlossaryError(f"'{new}' 용어집이 이미 있습니다")
    g.name = new
    return save(g)


def add_entries(name: str, entries: list[GlossaryEntry], create: bool = True) -> Glossary:
    """Append entries (an entry with the same Japanese replaces the old one)."""
    try:
        g = load(name)
    except FileNotFoundError:
        if not create:
            raise
        g = Glossary(name=check_name(name))
    by_ja = {e.ja: i for i, e in enumerate(g.entries)}
    for e in entries:
        if e.ja in by_ja:
            g.entries[by_ja[e.ja]] = e
        else:
            by_ja[e.ja] = len(g.entries)
            g.entries.append(e)
    return save(g)


# ---------------------------------------------------------------------- import / export


def clean(entries: list[GlossaryEntry]) -> list[GlossaryEntry]:
    out = []
    for e in entries:
        ja, ko = e.ja.strip(), e.ko.strip()
        if ja and ko:
            out.append(GlossaryEntry(ja=ja, ko=ko, note=e.note.strip(), hint=e.hint))
    return out


def parse_text(text: str) -> list[GlossaryEntry]:
    """One entry per line: `ja=ko`, `ja=ko # note` or `ja<TAB>ko[<TAB>note]`. A leading `*` marks a hint."""
    out = []
    for raw in text.splitlines():
        line = raw.strip().lstrip("﻿")
        if not line or line.startswith("#"):
            continue
        hint = line.startswith("*")
        line = line.lstrip("*").strip()
        if "\t" in line:
            parts = [p.strip() for p in line.split("\t")]
            ja, ko, note = (parts + ["", ""])[:3]
        elif "=" in line:
            ja, rest = line.split("=", 1)
            ko, _, note = rest.partition(" #")
        else:
            continue
        out.append(GlossaryEntry(ja=ja.strip(), ko=ko.strip(), note=note.strip(), hint=hint))
    return clean(out)


def parse_csv(text: str) -> list[GlossaryEntry]:
    rows = list(csv.reader(io.StringIO(text.lstrip("﻿"))))
    if rows and [c.strip().lower() for c in rows[0][:2]] == ["ja", "ko"]:
        rows = rows[1:]
    out = []
    for r in rows:
        if len(r) < 2:
            continue
        hint = len(r) > 3 and r[3].strip().lower() in ("1", "true", "yes", "y", "o", "예")
        out.append(GlossaryEntry(ja=r[0], ko=r[1], note=r[2] if len(r) > 2 else "", hint=hint))
    return clean(out)


def parse_file(path: Path) -> tuple[list[GlossaryEntry], list[str]]:
    """Entries and aliases from a .json / .csv / .txt file."""
    text = path.read_text(encoding="utf-8-sig")
    ext = path.suffix.lower().lstrip(".")
    if ext == "json":
        data = json.loads(text)
        if isinstance(data, list):
            return clean([GlossaryEntry.model_validate(d) for d in data]), []
        if isinstance(data, dict) and isinstance(data.get("entries"), list):
            return (clean([GlossaryEntry.model_validate(d) for d in data["entries"]]),
                    [str(a) for a in data.get("aliases") or []])
        raise GlossaryError("JSON 용어집 형식이 아닙니다")
    if ext == "csv":
        return parse_csv(text), []
    return parse_text(text), []


def render(g: Glossary, fmt: str) -> str:
    if fmt == "json":
        return g.model_dump_json(indent=1)
    if fmt == "csv":
        buf = io.StringIO()
        w = csv.writer(buf, lineterminator="\n")
        w.writerow(["ja", "ko", "note", "hint"])
        for e in g.entries:
            w.writerow([e.ja, e.ko, e.note, "1" if e.hint else ""])
        return "﻿" + buf.getvalue()  # BOM: Excel opens UTF-8 correctly
    lines = [f"# {g.name}"] + [f"{'*' if e.hint else ''}{e.ja}={e.ko}{f' # {e.note}' if e.note else ''}"
                               for e in g.entries]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------- use in a job


def merge(names: list[str], extra: list[GlossaryEntry] | None = None) -> tuple[list[GlossaryEntry], list[str]]:
    """Entries of the named glossaries in order, then `extra`; a later entry with the same
    Japanese replaces an earlier one. Returns (entries, missing names)."""
    by_ja: dict[str, GlossaryEntry] = {}
    missing = []
    for n in names:
        try:
            g = load(n)
        except (FileNotFoundError, GlossaryError):
            missing.append(n)
            continue
        for e in g.entries:
            by_ja[e.ja] = e
    for e in clean(extra or []):
        by_ja[e.ja] = e
    return list(by_ja.values()), missing


def hint_terms(entries: list[GlossaryEntry]) -> list[str]:
    marked = [e.ja for e in entries if e.hint and e.ja.strip()]
    if marked:
        return marked[:MAX_HINTS]
    return [e.ja for e in entries if e.ja.strip()][:MAX_HINTS]


def relevant(entries: list[GlossaryEntry], texts: list[str]) -> list[GlossaryEntry]:
    blob = norm("\n".join(texts))
    return [e for e in entries if norm(e.ja) in blob]


def content_key(entries: list[GlossaryEntry]) -> list[dict]:
    """What matters for translation (the hint flag does not)."""
    return [{"ja": e.ja, "ko": e.ko, "note": e.note} for e in entries]


def suggest(filename: str) -> list[str]:
    """Glossaries whose name or an alias appears in the file name (case and width ignored)."""
    stem = norm(Path(filename).stem)
    stem_loose = re.sub(r"[\s._\-\[\]()]+", "", stem)
    out = []
    for item in list_all():
        keys = [item["name"], *item["aliases"]]
        for k in keys:
            nk = norm(k)
            loose = re.sub(r"[\s._\-\[\]()]+", "", nk)
            if len(loose) >= 2 and (nk in stem or loose in stem_loose):
                out.append(item["name"])
                break
    return out
