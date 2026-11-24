"""Prompt and JSON schema for batch subtitle translation."""

from __future__ import annotations

import json

from jamak.config import GlossaryEntry

# bump when the prompt or the response format changes: it is part of the translate cache key
PROMPT_VERSION = 2  # 2: local engines get only the glossary entries that occur in the batch

SYSTEM_PROMPT = """\
You are a professional Japanese-to-Korean subtitle translator for anime and TV drama.

Rules:
- Translate every line in "lines" into natural, spoken Korean as it would appear in Korean subtitles.
- Keep each line separate: exactly one Korean translation per id. Never merge, split, skip, or reorder lines.
- Keep the speech level of the original: casual speech (タメ口) -> 반말, polite speech (です/ます) -> 존댓말.
  Keep the same speech level for the same speaker pair across lines.
- Keep it short enough to read quickly (about 2 subtitle lines at most). Prefer natural phrasing over literal word order.
- Japanese often omits the subject. Use the context lines to infer it, but do not add information that is not implied.
- Use the glossary spellings for names and terms exactly. For other names, use the standard Korean transliteration.
- Translate interjections and sound words naturally (e.g. えっ -> 어?, うん -> 응, はぁ -> 하아). Song lyrics are translated too.
- If a line is only noise or cannot be understood, return an empty string for that id.
- Output only the JSON object. No notes, no romaji, no explanations.
"""


def build_user_message(
    lines: list[dict],
    context: list[dict],
    glossary: list[GlossaryEntry],
) -> str:
    payload: dict = {}
    if glossary:
        payload["glossary"] = [
            {"ja": g.ja, "ko": g.ko, **({"note": g.note} if g.note else {})} for g in glossary
        ]
    if context:
        payload["context"] = context  # previous lines, for reference only
    payload["lines"] = lines
    return (
        "Translate the Japanese subtitle lines in \"lines\" into Korean. "
        "\"context\" (if present) is the preceding dialogue, for reference only - do not translate it.\n"
        "Return a JSON object whose keys are the line ids (as strings) and whose values are the Korean text.\n\n"
        + json.dumps(payload, ensure_ascii=False, indent=1)
    )


def response_schema(ids: list[int]) -> dict:
    """Object with exactly these id keys: the grammar forces every id once, in order."""
    return {
        "type": "object",
        "properties": {str(i): {"type": "string"} for i in ids},
        "required": [str(i) for i in ids],
        "additionalProperties": False,
    }


def system_with_glossary(glossary: list[GlossaryEntry]) -> str:
    """System prompt with the glossary appended: a stable prefix that cloud APIs can cache."""
    if not glossary:
        return SYSTEM_PROMPT
    entries = [{"ja": g.ja, "ko": g.ko, **({"note": g.note} if g.note else {})} for g in glossary]
    return (SYSTEM_PROMPT + "\nGlossary (use these Korean spellings exactly):\n"
            + json.dumps(entries, ensure_ascii=False, indent=1) + "\n")
