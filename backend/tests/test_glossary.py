"""Named glossaries: file format, merge, hints, per-batch filtering, suggestions, job snapshot."""

from __future__ import annotations

import json

from jamak.config import GlossaryEntry, PipelineOptions
from jamak.core import glossary as gl
from jamak.core.pipeline import initial_prompt

pytest_plugins = ["tests.test_api"]  # the `client` fixture


def E(ja: str, ko: str, note: str = "", hint: bool = False) -> GlossaryEntry:
    return GlossaryEntry(ja=ja, ko=ko, note=note, hint=hint)


def test_reads_v1_list_and_writes_v2(jamak_home):
    from jamak.config import paths

    paths.glossaries.mkdir(parents=True)
    (paths.glossaries / "old.json").write_text(json.dumps([{"ja": "ユウキ", "ko": "유키"}]), encoding="utf-8")
    g = gl.load("old")
    assert g.entries[0].ko == "유키" and g.aliases == []
    g.aliases = ["bleach", " "]
    gl.save(g)
    data = json.loads((paths.glossaries / "old.json").read_text(encoding="utf-8"))
    assert data["aliases"] == ["bleach"] and data["entries"][0]["hint"] is False and data["updated"]


def test_parse_text_csv_and_render_roundtrip(tmp_path):
    entries = gl.parse_text("# comment\n*ユウキ=유키 # 주인공\n先輩\t선배\t호칭\nbad line\n空=\n")
    assert [(e.ja, e.ko, e.note, e.hint) for e in entries] == [("ユウキ", "유키", "주인공", True), ("先輩", "선배", "호칭", False)]
    g = gl.Glossary(name="x", entries=entries)
    for fmt in gl.FORMATS:
        f = tmp_path / f"x.{fmt}"
        f.write_text(gl.render(g, fmt), encoding="utf-8")
        back, _ = gl.parse_file(f)
        assert [(e.ja, e.ko, e.note, e.hint) for e in back] == [(e.ja, e.ko, e.note, e.hint) for e in entries], fmt


def test_merge_later_wins_and_reports_missing(jamak_home):
    gl.save(gl.Glossary(name="공통", entries=[E("先輩", "선배"), E("様", "님")]))
    gl.save(gl.Glossary(name="블리치", entries=[E("先輩", "센빠이"), E("一護", "이치고", hint=True)]))
    merged, missing = gl.merge(["공통", "블리치", "없음"], [E("様", "씨")])
    assert {e.ja: e.ko for e in merged} == {"先輩": "센빠이", "様": "씨", "一護": "이치고"}
    assert missing == ["없음"]


def test_hint_terms_prefer_marked_entries():
    many = [E(f"名{i}", f"이름{i}") for i in range(60)]
    assert len(gl.hint_terms(many)) == gl.MAX_HINTS  # nothing marked: first 40, as before
    many[50].hint = True
    assert gl.hint_terms(many) == ["名50"]
    opts = PipelineOptions(glossary=many)
    assert initial_prompt(opts) == "登場人物・用語：名50。"


def test_hint_flag_and_ko_edits_do_not_change_stt_input():
    a = PipelineOptions(glossary=[E("一護", "이치고")])
    b = PipelineOptions(glossary=[E("一護", "이치고 (주인공)")])
    assert initial_prompt(a) == initial_prompt(b)  # translation-only change: STT cache stays valid


def test_relevant_filters_by_occurrence():
    entries = [E("一護", "이치고"), E("ルキア", "루키아"), E("ＡＢＣ", "에이비시")]
    assert [e.ja for e in gl.relevant(entries, ["一護、行くぞ", "abc です"])] == ["一護", "ＡＢＣ"]


def test_suggest_by_name_and_alias(jamak_home):
    gl.save(gl.Glossary(name="블리치", aliases=["bleach", "ブリーチ"], entries=[E("一護", "이치고")]))
    gl.save(gl.Glossary(name="나루토", aliases=["naruto"], entries=[E("ナルト", "나루토")]))
    assert gl.suggest("[Sub] Bleach - 03.mkv") == ["블리치"]
    assert gl.suggest("NARUTO.S01E01.1080p.mkv") == ["나루토"]
    assert gl.suggest("unrelated.mkv") == []


def test_rename_duplicate_add(jamak_home):
    gl.save(gl.Glossary(name="a", entries=[E("一", "일")]))
    gl.duplicate("a", "b")
    gl.rename("a", "c")
    assert not gl.exists("a") and gl.exists("b") and gl.exists("c")
    g = gl.add_entries("c", [E("一", "하나"), E("二", "둘")])
    assert [(e.ja, e.ko) for e in g.entries] == [("一", "하나"), ("二", "둘")]


def test_rebuild_keeps_job_only_edits(jamak_home):
    from jamak.api.server import glossary_status, rebuild_glossary

    gl.save(gl.Glossary(name="블리치", entries=[E("一護", "이치고"), E("ルキア", "루키아"), E("恋次", "렌지")]))
    base, _ = gl.merge(["블리치"])
    # this job changed ルキア, removed 恋次 and added 織姫
    job = [E("一護", "이치고"), E("ルキア", "루키아 (사신)"), E("織姫", "오리히메")]
    opts = PipelineOptions(glossary=job, glossary_names=["블리치"], glossary_base=base)
    assert glossary_status(opts)["changed"] is False
    gl.add_entries("블리치", [E("一護", "이치고 (주인공)"), E("雨竜", "우류")])
    assert glossary_status(opts)["changed"] is True
    new, new_base = rebuild_glossary(opts)
    assert {e.ja: e.ko for e in new} == {"一護": "이치고 (주인공)", "ルキア": "루키아 (사신)", "雨竜": "우류", "織姫": "오리히메"}
    opts.glossary, opts.glossary_base = new, new_base
    assert glossary_status(opts)["changed"] is False


def test_glossary_api_import_export(client, tmp_path):
    src = tmp_path / "블리치.txt"
    src.write_text("一護=이치고\nルキア=루키아\n", encoding="utf-8")
    r = client.post("/glossaries/import", json={"path": str(src)})
    assert r.status_code == 201 and r.json()["count"] == 2
    assert client.post("/glossaries/import", json={"path": str(src)}).status_code == 409
    out = tmp_path / "out.csv"
    assert client.post("/glossaries/블리치/export", json={"path": str(out)}).status_code == 200
    assert "一護,이치고" in out.read_text(encoding="utf-8-sig")
    assert client.post("/glossaries/블리치/rename", json={"name": "bleach"}).status_code == 200
    assert client.get("/glossaries/블리치").status_code == 404
    assert client.get("/glossaries/suggest", params={"filename": "Bleach 01.mkv"}).json()["names"] == ["bleach"]
