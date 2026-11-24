"""End-to-end run on the synthetic dialogue video (CUDA, Whisper, llama-server).

uv run pytest -m gpu -s
"""

import json
import re

import pytest

from jamak.config import PipelineOptions, default_llm_path, paths
from jamak.core.pipeline import Pipeline

pytestmark = pytest.mark.gpu


def cer(ref: str, hyp: str) -> float:
    norm = lambda s: re.sub(r"[\s、。！？!?「」]", "", s)  # noqa: E731
    r, h = norm(ref), norm(hyp)
    prev = list(range(len(h) + 1))
    for i, rc in enumerate(r, 1):
        cur = [i]
        for j, hc in enumerate(h, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (rc != hc)))
        prev = cur
    return prev[-1] / max(1, len(r))


def test_end_to_end(dialogue_video, tmp_path, real_home):
    if not paths.llama_server_exe.exists() or default_llm_path() is None:
        pytest.skip("llama-server or translation model missing")
    video, truth_path = dialogue_video
    truth = json.loads(truth_path.read_text(encoding="utf-8"))
    opts = PipelineOptions()
    opts.output.out_dir = str(tmp_path / "out")
    opts.output.formats = ["srt"]
    opts.output.dual_ass = True
    events = []
    pipe = Pipeline(video, opts, on_event=events.append, jobs_root=tmp_path / "jobs")
    res = pipe.run()
    print("timings", res.timings, res.stats)

    lines = pipe.load_lines().lines
    assert lines, "no subtitle lines"
    hyp = "".join(ln.ja for ln in lines)
    ref = "".join(t["ja"] for t in truth)
    score = cer(ref, hyp)
    print(f"CER {score:.3f}\nREF {ref}\nHYP {hyp}")
    for ln in lines:
        print(f"{ln.start:6.2f}-{ln.end:6.2f}  {ln.ja}  =>  {ln.ko}")
    assert score < 0.25
    assert all(ln.ko for ln in lines), "every line should be translated"
    assert all(a.end <= b.start for a, b in zip(lines, lines[1:]))
    # each truth line's midpoint should fall inside some subtitle (sync sanity)
    for t in truth:
        mid = (t["start"] + t["end"]) / 2
        assert any(ln.start - 0.5 <= mid <= ln.end + 0.5 for ln in lines), t
    assert {p.name for p in res.outputs} == {"dialogue.ko.srt", "dialogue.ja-ko.ass"}
    assert events[-1]["stage"] == "export" and events[-1]["status"] == "done"

    # second run: everything up to translate comes from cache
    res2 = Pipeline(video, opts, jobs_root=tmp_path / "jobs").run()
    assert set(res2.skipped) >= {"audio", "stt", "segment", "translate"}
