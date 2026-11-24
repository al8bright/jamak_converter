import { useEffect, useState } from "react";
import { api, ApiError, basename, inTauri, type Options, parseTc, type Probe, type SystemInfo, tc } from "./api";

const VIDEO_EXT = ["mkv", "mp4", "avi", "mov", "webm", "m2ts", "ts", "wmv", "flv"];

const STT_LABELS: Record<string, string> = {
  "large-v3": "large-v3 (정확도 우선)",
  "large-v3-turbo": "large-v3-turbo (빠름)",
  "kotoba-whisper-v2.0": "kotoba-whisper v2.0 (일본어 특화)",
};

export function NewJob({ sys, initialPath, onStarted }: {
  sys: SystemInfo | null; initialPath?: string; onStarted: (key: string) => void;
}) {
  const [path, setPath] = useState(initialPath ?? "");
  const [probe, setProbe] = useState<Probe | null>(null);
  const [opts, setOpts] = useState<Options | null>(null);
  const [glossary, setGlossary] = useState("");
  const [songs, setSongs] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [glossaryNames, setGlossaryNames] = useState<string[]>([]);
  const [glossaryName, setGlossaryName] = useState("");
  const [savedNote, setSavedNote] = useState<string | null>(null);

  useEffect(() => {
    api.get<Options>("/options/default").then(setOpts);
    api.get<string[]>("/glossaries").then(setGlossaryNames);
  }, []);

  async function loadGlossary(name: string) {
    if (!name) return;
    const r = await api.get<{ entries: { ja: string; ko: string }[] }>(`/glossaries/${encodeURIComponent(name)}`);
    setGlossary(r.entries.map((e) => `${e.ja}=${e.ko}`).join("\n"));
    setGlossaryName(name);
  }

  async function saveGlossary() {
    const name = glossaryName.trim();
    if (!name) return;
    await api.put(`/glossaries/${encodeURIComponent(name)}`, { entries: parseGlossary(glossary) });
    setGlossaryNames((n) => (n.includes(name) ? n : [...n, name].sort()));
    setSavedNote(`"${name}"(으)로 저장했습니다.`);
    setTimeout(() => setSavedNote(null), 2500);
  }

  useEffect(() => {
    if (!path) return;
    setProbe(null);
    setError(null);
    api.post<Probe>("/media/probe", { path })
      .then((p) => {
        setProbe(p);
        setOpts((o) => (o ? { ...o, audio: { track: p.picked } } : o));
      })
      .catch((e) => setError(e instanceof ApiError && e.status === 404 ? "파일을 찾을 수 없습니다." : `영상을 읽지 못했습니다: ${e.message}`));
  }, [path]);

  async function pick() {
    if (!inTauri) return;
    const { open } = await import("@tauri-apps/plugin-dialog");
    const p = await open({ multiple: false, filters: [{ name: "동영상", extensions: VIDEO_EXT }] });
    if (typeof p === "string") setPath(p);
  }

  async function pickOutDir() {
    if (!inTauri || !opts) return;
    const { open } = await import("@tauri-apps/plugin-dialog");
    const p = await open({ directory: true });
    if (typeof p === "string") setOpts({ ...opts, output: { ...opts.output, out_dir: p } });
  }

  async function start() {
    if (!opts || !path) return;
    const g = parseGlossary(glossary);
    const s = parseSongs(songs);
    if (s === null) {
      setError("노래 구간은 0:00-1:30 처럼 한 줄에 하나씩 적어 주세요.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const body = { video_path: path, options: { ...opts, glossary: g, songs: { ...opts.songs, ranges: s } } };
      const st = await api.post<{ key: string }>("/jobs", body);
      onStarted(st.key);
    } catch (e) {
      setError(`작업을 시작하지 못했습니다: ${(e as Error).message}`);
      setBusy(false);
    }
  }

  if (!path || (!probe && !error)) {
    return (
      <section className="page">
        <div className="drop" onClick={pick} role={inTauri ? "button" : undefined} tabIndex={inTauri ? 0 : -1}
          onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && pick()}>
          <FrameIllustration />
          <h1>자막을 만들 영상을 끌어다 놓으세요</h1>
          <p className="quiet">
            {inTauri ? "또는 여기를 눌러 파일을 고릅니다. " : ""}
            일본어 대사를 받아 적고 한국어로 옮겨 SRT·ASS 파일로 저장합니다. 모든 처리는 이 PC에서 합니다.
          </p>
          {path && <p className="quiet">영상을 읽는 중…</p>}
        </div>
        {!inTauri && (
          <form className="path-form" onSubmit={(e) => {
            e.preventDefault();
            const v = new FormData(e.currentTarget).get("p");
            if (typeof v === "string" && v.trim()) setPath(v.trim().replace(/^"|"$/g, ""));
          }}>
            <label htmlFor="p">브라우저에서는 영상 경로를 직접 입력합니다</label>
            <div className="row">
              <input id="p" name="p" placeholder="C:\Videos\ep01.mkv" autoComplete="off" />
              <button className="btn">열기</button>
            </div>
          </form>
        )}
      </section>
    );
  }

  return (
    <section className="page">
      <header className="page-head">
        <h1>{probe ? probe.name : basename(path)}</h1>
        {probe && (
          <p className="quiet">
            {tc(probe.duration, false)} 분량, {(probe.size / 1e9).toFixed(2)}GB
          </p>
        )}
        <button className="btn ghost" onClick={() => { setPath(""); setProbe(null); setError(null); }}>
          다른 영상 고르기
        </button>
      </header>

      {error && <p className="notice bad" role="alert">{error}</p>}

      {probe && opts && (
        <div className="form">
          <Field label="오디오 트랙" hint="일본어 트랙을 자동으로 골랐습니다.">
            <select value={opts.audio.track ?? 0}
              onChange={(e) => setOpts({ ...opts, audio: { track: Number(e.target.value) } })}>
              {probe.tracks.map((t) => (
                <option key={t.index} value={t.index}>
                  {t.index + 1}. {t.language ?? "언어 정보 없음"} {t.title ? `“${t.title}”` : ""} ({t.codec}, {t.channels}ch)
                </option>
              ))}
            </select>
          </Field>

          <Field label="음성 인식 모델">
            <select value={opts.stt.model} onChange={(e) => setOpts({ ...opts, stt: { ...opts.stt, model: e.target.value } })}>
              {(sys?.stt_models ?? ["large-v3"]).map((m) => (
                <option key={m} value={m}>{STT_LABELS[m] ?? m}</option>
              ))}
            </select>
          </Field>

          <Field label="번역 모델" hint="models 폴더의 GGUF 파일 가운데 고릅니다.">
            <select value={opts.translate.model_path ?? sys?.default_llm ?? ""}
              onChange={(e) => setOpts({ ...opts, translate: { ...opts.translate, model_path: e.target.value || null } })}>
              {(sys?.llm_models ?? []).map((m) => (
                <option key={m.path} value={m.path}>{m.name} ({m.size_gb}GB)</option>
              ))}
              {!sys?.llm_models.length && <option value="">모델 파일이 없습니다</option>}
            </select>
          </Field>

          <Field label="배경음 제거" hint={sys?.separation_available
            ? "음악·효과음이 큰 장면에서 인식이 좋아집니다. 처리 시간이 늘어납니다."
            : "보컬 분리 모듈이 설치되지 않았습니다 (uv sync --extra separation)."}>
            <label className="check">
              <input type="checkbox" disabled={!sys?.separation_available} checked={opts.separate.enabled}
                onChange={(e) => setOpts({ ...opts, separate: { ...opts.separate, enabled: e.target.checked } })} />
              대사만 남기고 인식하기
            </label>
          </Field>

          <Field label="용어집" hint="한 줄에 하나씩 일본어=한국어. 인물 이름이 틀리지 않게 하고, 인식에도 힌트로 씁니다.">
            <div className="stack">
              <textarea rows={4} value={glossary} onChange={(e) => setGlossary(e.target.value)}
                placeholder={"ユウキ=유키\n先輩=선배"} spellCheck={false} />
              <div className="row wrap">
                {glossaryNames.length > 0 && (
                  <select className="compact" value="" onChange={(e) => loadGlossary(e.target.value)} aria-label="저장한 용어집 불러오기">
                    <option value="">저장한 용어집 불러오기</option>
                    {glossaryNames.map((n) => <option key={n} value={n}>{n}</option>)}
                  </select>
                )}
                <input className="compact" value={glossaryName} onChange={(e) => setGlossaryName(e.target.value)}
                  placeholder="작품 이름" aria-label="용어집 이름" />
                <button className="btn ghost" onClick={saveGlossary} disabled={!glossaryName.trim() || !glossary.trim()}>
                  용어집 저장
                </button>
                {savedNote && <span className="quiet small" role="status">{savedNote}</span>}
              </div>
            </div>
          </Field>

          <Field label="노래 구간" hint="오프닝·엔딩처럼 가사가 나오는 구간. 한 줄에 하나씩 시작-끝.">
            <div className="stack">
              <textarea rows={2} value={songs} onChange={(e) => setSongs(e.target.value)} placeholder={"0:00-1:30\n22:10-23:40"}
                spellCheck={false} />
              <div className="row">
                <label className="check">
                  <input type="radio" name="songmode" checked={opts.songs.mode === "translate"}
                    onChange={() => setOpts({ ...opts, songs: { ...opts.songs, mode: "translate" } })} />
                  가사도 번역 (♪ 표시)
                </label>
                <label className="check">
                  <input type="radio" name="songmode" checked={opts.songs.mode === "skip"}
                    onChange={() => setOpts({ ...opts, songs: { ...opts.songs, mode: "skip" } })} />
                  자막 넣지 않기
                </label>
              </div>
            </div>
          </Field>

          <Field label="저장할 파일">
            <div className="row wrap">
              {(["srt", "ass", "vtt"] as const).map((f) => (
                <label className="check" key={f}>
                  <input type="checkbox" checked={opts.output.formats.includes(f)}
                    onChange={(e) => {
                      const formats = e.target.checked ? [...opts.output.formats, f] : opts.output.formats.filter((x) => x !== f);
                      setOpts({ ...opts, output: { ...opts.output, formats } });
                    }} />
                  {f.toUpperCase()}
                </label>
              ))}
              <label className="check">
                <input type="checkbox" checked={opts.output.include_ja}
                  onChange={(e) => setOpts({ ...opts, output: { ...opts.output, include_ja: e.target.checked } })} />
                일본어 자막도
              </label>
              <label className="check">
                <input type="checkbox" checked={opts.output.dual_ass}
                  onChange={(e) => setOpts({ ...opts, output: { ...opts.output, dual_ass: e.target.checked } })} />
                한·일 이중 자막(ASS)
              </label>
            </div>
          </Field>

          <Field label="저장 위치">
            <div className="row">
              <span className="path">{opts.output.out_dir ?? "영상과 같은 폴더"}</span>
              {inTauri && <button className="btn ghost" onClick={pickOutDir}>바꾸기</button>}
              {opts.output.out_dir && (
                <button className="btn ghost" onClick={() => setOpts({ ...opts, output: { ...opts.output, out_dir: null } })}>
                  영상 폴더로
                </button>
              )}
            </div>
          </Field>

          <div className="actions">
            <button className="btn primary big" onClick={start} disabled={busy || opts.output.formats.length === 0}>
              {busy ? "시작하는 중…" : "자막 만들기"}
            </button>
            <p className="quiet small">
              2시간 영상은 RTX 4080 기준 대략 10~20분 걸립니다. 한 번 처리한 단계는 저장해 두어 다시 하지 않습니다.
            </p>
          </div>
        </div>
      )}
    </section>
  );
}

function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div className="field">
      <div className="field-label">
        <span>{label}</span>
        {hint && <small className="quiet">{hint}</small>}
      </div>
      <div className="field-body">{children}</div>
    </div>
  );
}

function parseGlossary(text: string) {
  return text
    .split(/\r?\n/)
    .map((l) => l.trim())
    .filter((l) => l && !l.startsWith("#") && l.includes("="))
    .map((l) => {
      const [ja, ...rest] = l.split("=");
      return { ja: ja.trim(), ko: rest.join("=").trim(), note: "" };
    })
    .filter((e) => e.ja && e.ko);
}

function parseSongs(text: string): [number, number][] | null {
  const out: [number, number][] = [];
  for (const raw of text.split(/\r?\n/)) {
    const l = raw.trim();
    if (!l) continue;
    const [a, b] = l.split("-");
    const s = a !== undefined ? parseTc(a) : null;
    const e = b !== undefined ? parseTc(b) : null;
    if (s === null || e === null || e <= s) return null;
    out.push([s, e]);
  }
  return out;
}

function FrameIllustration() {
  // a 16:9 frame with two subtitle lines: the thing this app makes
  return (
    <svg className="frame-ill" viewBox="0 0 320 180" aria-hidden>
      <rect x="1" y="1" width="318" height="178" rx="6" className="ill-frame" />
      <rect x="92" y="122" width="136" height="12" rx="2" className="ill-ja" />
      <rect x="70" y="142" width="180" height="16" rx="2" className="ill-ko" />
    </svg>
  );
}
