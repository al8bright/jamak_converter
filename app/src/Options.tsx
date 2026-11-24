import { useEffect, useState } from "react";
import {
  api, ApiError, basename, inTauri, kindBadge, type Options as Opts, parseTc, type Probe, type Profile,
  profileDetail, PROVIDER_NAME, tc,
} from "./api";
import { useApp } from "./ctx";
import { pickVideo } from "./Home";
import { Icon, ICONS, KindBadge, Modal, Note, Page } from "./ui";

const STT_LABELS: Record<string, string> = {
  "large-v3": "large-v3 (기본)",
  "large-v3-turbo": "large-v3-turbo (빠름)",
  "kotoba-whisper-v2.0": "kotoba-whisper-v2.0 (일본어 특화)",
};

function profileNote(p: Profile): string {
  if (!p.status.ready) {
    return p.cloud ? "설정 › 번역 엔진에서 키를 먼저 등록하세요." : `${p.status.text}. 시스템 상태에서 확인하세요.`;
  }
  if (p.kind === "local_managed") return "이 PC의 GPU에서 처리합니다. 외부로 아무것도 보내지 않습니다.";
  if (p.kind === "local_external") return `대사 텍스트를 ${p.base_url}로 보냅니다.`;
  return `대사 텍스트만 ${PROVIDER_NAME[p.kind]}으로 보냅니다.`;
}

export function Options({ path: initialPath }: { path: string }) {
  const { sys, profiles, ui, go, saveUi, refreshJobs } = useApp();
  const [path, setPath] = useState(initialPath);
  const [probe, setProbe] = useState<Probe | null>(null);
  const [opts, setOpts] = useState<Opts | null>(null);
  const [profileId, setProfileId] = useState<number | null>(null);
  const [glossaryNames, setGlossaryNames] = useState<string[]>([]);
  const [glossaryName, setGlossaryName] = useState("");
  const [glossaryText, setGlossaryText] = useState("");
  const [editGlossary, setEditGlossary] = useState(false);
  const [songs, setSongs] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirmCloud, setConfirmCloud] = useState(false);
  const [saved, setSaved] = useState<string | null>(null);

  useEffect(() => {
    api.get<Opts>("/options/default").then(setOpts);
    api.get<string[]>("/glossaries").then(setGlossaryNames);
  }, []);

  useEffect(() => {
    if (profileId !== null || !profiles.length) return;
    const def = profiles.find((p) => p.is_default && p.status.ready) ?? profiles.find((p) => p.status.ready) ?? profiles[0];
    setProfileId(def.id);
  }, [profiles, profileId]);

  useEffect(() => {
    setProbe(null);
    setError(null);
    api.post<Probe>("/media/probe", { path })
      .then((p) => {
        setProbe(p);
        setOpts((o) => (o ? { ...o, audio: { track: p.picked } } : o));
      })
      .catch((e) => setError(e instanceof ApiError && e.status === 404 ? "파일을 찾을 수 없습니다." : `영상을 읽지 못했습니다: ${e.message}`));
  }, [path]);

  const profile = profiles.find((p) => p.id === profileId) ?? null;

  async function loadGlossary(name: string) {
    setGlossaryName(name);
    if (!name) { setGlossaryText(""); return; }
    const r = await api.get<{ entries: { ja: string; ko: string }[] }>(`/glossaries/${encodeURIComponent(name)}`);
    setGlossaryText(r.entries.map((e) => `${e.ja}=${e.ko}`).join("\n"));
  }

  async function saveGlossary(name: string) {
    const n = name.trim();
    if (!n) return;
    await api.put(`/glossaries/${encodeURIComponent(n)}`, { entries: parseGlossary(glossaryText) });
    setGlossaryNames((x) => (x.includes(n) ? x : [...x, n].sort()));
    setGlossaryName(n);
    setSaved(`"${n}" 용어집을 저장했습니다.`);
    setTimeout(() => setSaved(null), 2500);
  }

  async function pickOutDir() {
    if (!inTauri || !opts) return;
    const { open } = await import("@tauri-apps/plugin-dialog");
    const p = await open({ directory: true });
    if (typeof p === "string") setOpts({ ...opts, output: { ...opts.output, out_dir: p } });
  }

  function requestStart() {
    if (profile?.cloud && !ui.cloud_notice_seen) setConfirmCloud(true);
    else start();
  }

  async function start() {
    if (!opts || !profile) return;
    const ranges = parseSongs(songs);
    if (ranges === null) {
      setError("노래 구간은 0:00-1:30 처럼 쉼표나 줄바꿈으로 나눠 적어 주세요.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const body = {
        video_path: path, profile_id: profile.id,
        options: { ...opts, glossary: parseGlossary(glossaryText), songs: { ...opts.songs, ranges } },
      };
      const st = await api.post<{ key: string }>("/jobs", body);
      await refreshJobs();
      go({ kind: "progress", key: st.key });
    } catch (e) {
      setError(`작업을 시작하지 못했습니다: ${(e as Error).message}`);
      setBusy(false);
    }
  }

  const name = probe?.name ?? basename(path);
  const stem = name.replace(/\.[^.]+$/, "");
  const firstFmt = opts?.output.formats[0] ?? "srt";
  const glossaryCount = parseGlossary(glossaryText).length;

  return (
    <Page
      eyebrow={<><button onClick={() => go({ kind: "home" })}>NEW JOB</button> › OPTIONS</>}
      title="작업 옵션"
      actions={<>
        <span className="small muted" style={{ fontSize: 12 }}>번역: <b style={{ color: "var(--text)" }}>{profile?.name ?? "—"}</b></span>
        <button className="btn" onClick={() => go({ kind: "home" })}>취소</button>
        <button className="btn primary" onClick={requestStart}
          disabled={busy || !probe || !opts || !profile?.status.ready || !opts.output.formats.length && !opts.output.dual_ass}>
          {busy ? "시작하는 중…" : "처리 시작"}
        </button>
      </>}
    >
      <section aria-label="선택한 영상" className="card video-card">
        <div className="icon-tile sm" aria-hidden><Icon d={ICONS.video} size={18} /></div>
        <div className="info">
          <b>{name}</b>
          <span className="mono small muted wrap-any">
            {path}{probe ? ` · ${tc(probe.duration, false)} · ${(probe.size / 1e9).toFixed(2)} GB` : ""}
          </span>
        </div>
        {probe && opts && (
          <label className="field row">
            오디오 트랙
            <select className="input" value={opts.audio.track ?? 0}
              onChange={(e) => setOpts({ ...opts, audio: { track: Number(e.target.value) } })}>
              {probe.tracks.map((t) => (
                <option key={t.index} value={t.index}>
                  #{t.index + 1} {t.language ?? "언어 정보 없음"} · {t.codec.toUpperCase()} {t.channels}ch
                  {t.index === probe.picked ? " (자동 선택)" : ""}
                </option>
              ))}
            </select>
          </label>
        )}
        {inTauri && <button className="btn sm" onClick={async () => { const p = await pickVideo(); if (p) setPath(p); }}>다른 영상</button>}
      </section>

      {error && <Note tone="err">{error}</Note>}
      {!probe && !error && <p className="muted">영상을 읽는 중…</p>}

      {probe && opts && (
        <div className="cols">
          <section aria-labelledby="tr-h" className="card" style={{ flex: "1 1 420px" }}>
            <div className="card-head">
              <h2 id="tr-h">번역</h2>
              <button className="link" onClick={() => go({ kind: "engine" })}>프로필 관리</button>
            </div>
            <fieldset style={{ margin: 0, padding: 0, border: 0, display: "flex", flexDirection: "column", gap: 6 }}>
              <legend className="small muted" style={{ padding: 0, marginBottom: 6, fontSize: 12 }}>번역 프로필</legend>
              {profiles.map((p) => {
                const kb = kindBadge(p.kind);
                const on = p.id === profileId;
                return (
                  <label key={p.id} className={`profile-opt${on ? " on" : ""}${p.status.ready ? "" : " off"}`}>
                    <input type="radio" name="profile" checked={on} disabled={!p.status.ready} onChange={() => setProfileId(p.id)} />
                    <span className="p-body">
                      <span className="p-name">
                        <b>{p.name}</b>
                        <KindBadge cloud={kb.cloud} text={kb.text} />
                        {p.is_default && <span className="badge default">기본</span>}
                      </span>
                      <span className="p-detail">{profileDetail(p, sys?.default_llm ? basename(sys.default_llm) : null)}</span>
                      <span className="p-note">{profileNote(p)}</span>
                    </span>
                  </label>
                );
              })}
            </fieldset>
            {profile?.cloud && (
              <Note>
                번역할 <b>일본어 대사 텍스트</b>가 {PROVIDER_NAME[profile.kind]}으로 전송됩니다. 영상과 음성은 전송되지 않습니다.
                사용한 토큰과 예상 비용은 진행 화면에 표시됩니다.
              </Note>
            )}
            <div className="field">
              <label htmlFor="opt-glossary">용어집</label>
              <span style={{ display: "flex", gap: 6 }}>
                <select id="opt-glossary" className="input" value={glossaryName} onChange={(e) => loadGlossary(e.target.value)}>
                  <option value="">{glossaryText.trim() && !glossaryName ? `직접 입력 · ${glossaryCount}개 항목` : "사용 안 함"}</option>
                  {glossaryNames.map((n) => <option key={n} value={n}>{n}{n === glossaryName ? ` · ${glossaryCount}개 항목` : ""}</option>)}
                </select>
                <button className="btn" aria-expanded={editGlossary} onClick={() => setEditGlossary(!editGlossary)}>
                  {editGlossary ? "닫기" : "편집"}
                </button>
              </span>
            </div>
            {editGlossary && (
              <div className="stack" style={{ gap: 6 }}>
                <textarea className="input" rows={5} value={glossaryText} spellCheck={false}
                  aria-label="용어집 항목. 한 줄에 하나씩 일본어=한국어"
                  placeholder={"ユウキ=유키\n先輩=선배"} onChange={(e) => setGlossaryText(e.target.value)} />
                <span className="small muted">한 줄에 하나씩 일본어=한국어. 인물 이름이 틀리지 않게 하고, 음성 인식에도 힌트로 씁니다.</span>
                <GlossarySave initial={glossaryName} onSave={saveGlossary} disabled={!glossaryText.trim()} />
                {saved && <span className="small ok-fg" role="status">{saved}</span>}
              </div>
            )}
          </section>

          <div className="stack" style={{ flex: "1 1 380px" }}>
            <section aria-labelledby="stt-h" className="card">
              <h2 id="stt-h">음성 인식</h2>
              <div className="grid2">
                <label className="field">
                  STT 모델
                  <select className="input" value={opts.stt.model} onChange={(e) => setOpts({ ...opts, stt: { ...opts.stt, model: e.target.value } })}>
                    {(sys?.stt_models ?? ["large-v3"]).map((m) => <option key={m} value={m}>{STT_LABELS[m] ?? m}</option>)}
                  </select>
                </label>
                <label className="field">
                  오프닝·엔딩 노래 구간
                  <select className="input" value={opts.songs.mode}
                    onChange={(e) => setOpts({ ...opts, songs: { ...opts.songs, mode: e.target.value as "translate" | "skip" } })}>
                    <option value="translate">가사도 번역</option>
                    <option value="skip">건너뛰기</option>
                  </select>
                </label>
              </div>
              <label className="field">
                노래 구간 (시작-끝, 쉼표로 여러 개)
                <input className="input mono" value={songs} onChange={(e) => setSongs(e.target.value)} placeholder="0:00-1:30, 22:10-23:40" />
              </label>
              <label className="check">
                <input type="checkbox" disabled={!sys?.separation_available} checked={opts.separate.enabled}
                  onChange={(e) => setOpts({ ...opts, separate: { ...opts.separate, enabled: e.target.checked } })} />
                <span className="check-text">
                  <b>보컬 분리 (Demucs)</b>
                  <span>{sys?.separation_available
                    ? "배경음악이 큰 장면에서 인식이 좋아집니다. 처리 시간이 늘어납니다."
                    : "이 설치본에는 보컬 분리 모듈이 없습니다 (개발 환경: uv sync --extra separation)."}</span>
                </span>
              </label>
              <label className="check">
                <input type="checkbox" checked={opts.filters.enabled}
                  onChange={(e) => setOpts({ ...opts, filters: { ...opts.filters, enabled: e.target.checked } })} />
                <span className="check-text">
                  <b>환각 필터</b>
                  <span>반복 문장과 알려진 환각 문구를 거릅니다.</span>
                </span>
              </label>
            </section>

            <section aria-labelledby="out-h" className="card">
              <h2 id="out-h">출력</h2>
              <fieldset className="formats" style={{ margin: 0, padding: 0, border: 0 }}>
                <legend className="small muted" style={{ padding: 0, marginBottom: 4, fontSize: 12 }}>형식</legend>
                {(["srt", "ass", "vtt"] as const).map((f) => (
                  <label className="check inline" key={f}>
                    <input type="checkbox" checked={opts.output.formats.includes(f)} onChange={(e) => {
                      const formats = e.target.checked ? [...opts.output.formats, f] : opts.output.formats.filter((x) => x !== f);
                      setOpts({ ...opts, output: { ...opts.output, formats } });
                    }} />
                    <span>{f.toUpperCase()}</span>
                  </label>
                ))}
                <label className="check inline">
                  <input type="checkbox" checked={opts.output.include_ja}
                    onChange={(e) => setOpts({ ...opts, output: { ...opts.output, include_ja: e.target.checked } })} />
                  <span>일본어 자막도</span>
                </label>
                <label className="check inline">
                  <input type="checkbox" checked={opts.output.dual_ass}
                    onChange={(e) => setOpts({ ...opts, output: { ...opts.output, dual_ass: e.target.checked } })} />
                  <span>이중 자막 ASS</span>
                </label>
              </fieldset>
              <div className="out-loc">
                <span>
                  <span className="small muted">저장 위치</span>
                  <b style={{ fontWeight: 500 }} className="wrap-any">
                    {opts.output.out_dir ?? "영상 파일 옆"} · {stem}.ko.{firstFmt}
                  </b>
                </span>
                <span style={{ display: "flex", gap: 6 }}>
                  {opts.output.out_dir && (
                    <button className="btn" onClick={() => setOpts({ ...opts, output: { ...opts.output, out_dir: null } })}>영상 옆으로</button>
                  )}
                  {inTauri && <button className="btn" onClick={pickOutDir}>변경</button>}
                </span>
              </div>
            </section>
          </div>
        </div>
      )}

      {confirmCloud && profile && (
        <Modal title="클라우드로 번역합니다" onClose={() => setConfirmCloud(false)} actions={<>
          <button className="btn" onClick={() => setConfirmCloud(false)}>취소</button>
          <button className="btn primary" onClick={async () => { setConfirmCloud(false); await saveUi({ cloud_notice_seen: true }); start(); }}>
            확인하고 시작
          </button>
        </>}>
          <p>번역할 일본어 대사 텍스트가 {PROVIDER_NAME[profile.kind]}으로 전송됩니다. 영상과 음성은 전송되지 않습니다.</p>
          <p className="muted small">이 안내는 한 번만 보여 줍니다. 클라우드 프로필을 고르면 옵션 화면에 같은 문구가 계속 표시됩니다.</p>
        </Modal>
      )}
    </Page>
  );
}

function GlossarySave({ initial, onSave, disabled }: { initial: string; onSave: (name: string) => void; disabled: boolean }) {
  const [name, setName] = useState(initial);
  useEffect(() => setName(initial), [initial]);
  return (
    <span style={{ display: "flex", gap: 6 }}>
      <input className="input sm" value={name} onChange={(e) => setName(e.target.value)} placeholder="작품 이름" aria-label="용어집 이름" />
      <button className="btn sm" disabled={disabled || !name.trim()} onClick={() => onSave(name)}>용어집 저장</button>
    </span>
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
  for (const raw of text.split(/[\r\n,]+/)) {
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
