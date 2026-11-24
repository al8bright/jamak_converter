import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  api, basename, events, inTauri, type JobEvent, type Line, type LiveState, type Options,
  parseTc, type Stage, STAGE_NAMES, STAGE_WEIGHTS, STAGES, tc,
} from "./api";

interface JobDetail {
  key: string;
  meta: { video_path: string; video_name: string; duration?: number; stages: Record<string, { elapsed: number }>; last_export?: string[] };
  options: Options;
  live: LiveState | null;
}

export function JobView({ jobKey, onChanged, onDeleted }: { jobKey: string; onChanged: () => void; onDeleted: () => void }) {
  const [detail, setDetail] = useState<JobDetail | null>(null);
  const [live, setLive] = useState<LiveState | null>(null);
  const [stageState, setStageState] = useState<Partial<Record<Stage, { status: string; progress: number; message: string }>>>({});
  const [segVersion, setSegVersion] = useState(0);

  const load = useCallback(async () => {
    const d = await api.get<JobDetail>(`/jobs/${jobKey}`);
    setDetail(d);
    setLive(d.live);
    if (d.live?.events) {
      const s: typeof stageState = {};
      for (const e of d.live.events) if (e.type === "progress") s[e.stage] = e;
      setStageState(s);
    }
  }, [jobKey]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(
    () =>
      events(jobKey, (e: JobEvent) => {
        if (e.type === "progress") {
          setStageState((s) => ({ ...s, [e.stage]: e }));
          setLive((l) => (l ? { ...l, stage: e.stage, progress: e.overall, message: e.message } : l));
        } else if (e.type === "status") {
          setLive(e);
          if (e.status === "queued") setStageState({});
          if (e.status === "done" || e.status === "error" || e.status === "cancelled") {
            setSegVersion((v) => v + 1);
            load();
            onChanged();
          }
        }
      }),
    [jobKey, load, onChanged],
  );

  if (!detail) return <section className="page"><p className="quiet">불러오는 중…</p></section>;

  const running = live && (live.status === "running" || live.status === "queued");
  const name = detail.meta.video_name ?? basename(detail.meta.video_path);

  async function rerun(from?: Stage) {
    await api.post(`/jobs`, { video_path: detail!.meta.video_path, options: detail!.options, force_from: from ?? null });
    onChanged();
  }

  async function remove() {
    await api.del(`/jobs/${jobKey}?purge=true`);
    onDeleted();
  }

  return (
    <section className="page wide">
      <header className="page-head">
        <h1>{name}</h1>
        {detail.meta.duration ? <p className="quiet">{tc(detail.meta.duration, false)} 분량</p> : null}
        {!running && (
          <div className="row">
            <button className="btn ghost" onClick={() => rerun("translate")} title="음성 인식 결과는 그대로 두고 번역만 다시 합니다">
              번역 다시 하기
            </button>
            <button className="btn ghost danger" onClick={remove} title="저장한 중간 결과를 지웁니다. 만든 자막 파일은 남습니다">
              작업 지우기
            </button>
          </div>
        )}
      </header>

      {running ? (
        <ProgressView live={live!} stages={stageState} separate={detail.options.separate.enabled} jobKey={jobKey} />
      ) : (
        <>
          {live?.status === "error" && (
            <div className="notice bad" role="alert">
              <p>처리 중 오류가 났습니다: {live.error}</p>
              <button className="btn" onClick={() => rerun()}>다시 시도</button>
            </div>
          )}
          {live?.status === "cancelled" && (
            <div className="notice">
              <p>작업을 취소했습니다. 끝난 단계는 저장되어 있어 이어서 할 수 있습니다.</p>
              <button className="btn" onClick={() => rerun()}>이어서 하기</button>
            </div>
          )}
          <Review key={segVersion} jobKey={jobKey} options={detail.options} lastExport={detail.meta.last_export ?? []}
            onExported={load} />
        </>
      )}
    </section>
  );
}

// ---------------------------------------------------------------------- progress

function ProgressView({ live, stages, separate, jobKey }: {
  live: LiveState; stages: Partial<Record<Stage, { status: string; progress: number; message: string }>>;
  separate: boolean; jobKey: string;
}) {
  const active = STAGES.filter((s) => s !== "export" && (s !== "separate" || separate));
  const total = active.reduce((a, s) => a + STAGE_WEIGHTS[s], 0);
  const started = live.started_at ? Date.now() / 1000 - live.started_at : 0;
  const eta = live.progress > 0.05 && started > 5 ? (started / live.progress) * (1 - live.progress) : null;

  return (
    <div className="progress">
      <div className="progress-head">
        <span className="big-pct">{Math.round(live.progress * 100)}<small>%</small></span>
        <div>
          <p>{live.status === "queued" ? "앞 작업이 끝나기를 기다리는 중" : live.stage ? STAGE_NAMES[live.stage] : "준비 중"}</p>
          <p className="quiet small">
            {live.message}
            {eta !== null && `, 남은 시간 약 ${Math.max(1, Math.round(eta / 60))}분`}
          </p>
        </div>
        <button className="btn ghost" onClick={() => api.post(`/jobs/${jobKey}/cancel`)}>취소</button>
      </div>
      <ol className="strip" aria-label="처리 단계">
        {active.map((s) => {
          const st = stages[s];
          const p = st ? (st.status === "done" || st.status === "skipped" ? 1 : st.progress) : 0;
          return (
            <li key={s} style={{ flexGrow: STAGE_WEIGHTS[s] / total }} className={`strip-seg ${st?.status ?? "wait"}`}>
              <span className="strip-bar"><span className="strip-fill" style={{ transform: `scaleX(${p})` }} /></span>
              <span className="strip-label">
                {STAGE_NAMES[s]}
                {st?.status === "skipped" && <small> 저장된 결과 사용</small>}
              </span>
            </li>
          );
        })}
      </ol>
    </div>
  );
}

// ---------------------------------------------------------------------- review

function Review({ jobKey, options, lastExport, onExported }: {
  jobKey: string; options: Options; lastExport: string[]; onExported: () => void;
}) {
  const [lines, setLines] = useState<Line[] | null>(null);
  const [source, setSource] = useState<"ko" | "ja">("ko");
  const [missing, setMissing] = useState(false);
  const [sel, setSel] = useState<number | null>(null);
  const [picked, setPicked] = useState<Set<number>>(new Set());
  const [filter, setFilter] = useState<"all" | "failed" | "edited">("all");
  const [query, setQuery] = useState("");
  const [toast, setToast] = useState<string | null>(null);
  const [exported, setExported] = useState<string[]>(lastExport);
  const listRef = useRef<HTMLDivElement>(null);
  const exportKey = lastExport.join("|");
  useEffect(() => setExported(exportKey ? exportKey.split("|") : []), [exportKey]);

  useEffect(() => {
    api.get<{ source: "ko" | "ja"; lines: Line[] }>(`/jobs/${jobKey}/segments`)
      .then((r) => {
        setLines(r.lines);
        setSource(r.source);
        setSel(r.lines[0]?.id ?? null);
      })
      .catch(() => setMissing(true));
  }, [jobKey]);

  const shown = useMemo(() => {
    if (!lines) return [];
    const q = query.trim();
    return lines.filter((ln) => {
      if (filter === "failed" && !(ln.flags.includes("translate_failed") || !ln.ko.trim())) return false;
      if (filter === "edited" && !ln.locked) return false;
      return !q || ln.ja.includes(q) || ln.ko.includes(q);
    });
  }, [lines, filter, query]);

  if (missing) return <p className="quiet">아직 만든 자막이 없습니다.</p>;
  if (!lines) return <p className="quiet">자막을 불러오는 중…</p>;

  const failed = lines.filter((l) => l.flags.includes("translate_failed") || !l.ko.trim()).length;
  const edited = lines.filter((l) => l.locked).length;
  const current = lines.find((l) => l.id === sel) ?? null;

  function flash(text: string) {
    setToast(text);
    setTimeout(() => setToast(null), 2600);
  }

  async function save(id: number, patch: Partial<Pick<Line, "ko" | "start" | "end">>) {
    const r = await api.patch<{ lines: Line[] }>(`/jobs/${jobKey}/segments`, { edits: [{ id, ...patch }] });
    setLines(r.lines);
  }

  async function retranslate() {
    const ids = [...picked];
    if (!ids.length) return;
    await api.post(`/jobs/${jobKey}/retranslate`, { ids });
    setPicked(new Set());
  }

  async function exportFiles() {
    const r = await api.post<{ written: string[] }>(`/jobs/${jobKey}/export`, { output: options.output });
    setExported(r.written);
    onExported();
    flash(`${r.written.length}개 파일을 저장했습니다.`);
  }

  function move(delta: number) {
    const i = shown.findIndex((l) => l.id === sel);
    const next = shown[Math.min(shown.length - 1, Math.max(0, i + delta))];
    if (next) {
      setSel(next.id);
      listRef.current?.querySelector(`[data-id="${next.id}"]`)?.scrollIntoView({ block: "nearest" });
    }
  }

  return (
    <div className="review">
      <div className="screen" aria-label="자막 미리보기">
        {current ? (
          <>
            <span className="screen-tc">{tc(current.start)} – {tc(current.end)}</span>
            <div className="screen-subs">
              {options.output.dual_ass && <p className="sub-ja" lang="ja">{current.ja}</p>}
              <p className="sub-ko">{current.ko || <span className="sub-missing">번역 없음</span>}</p>
            </div>
          </>
        ) : (
          <p className="quiet">줄을 고르면 화면에 나올 모습을 보여 줍니다.</p>
        )}
      </div>

      <div className="toolbar">
        <div className="seg-ctl" role="radiogroup" aria-label="보기">
          <button role="radio" aria-checked={filter === "all"} onClick={() => setFilter("all")}>전체 {lines.length}</button>
          <button role="radio" aria-checked={filter === "failed"} onClick={() => setFilter("failed")} disabled={!failed}>
            번역 빠짐 {failed}
          </button>
          <button role="radio" aria-checked={filter === "edited"} onClick={() => setFilter("edited")} disabled={!edited}>
            고친 줄 {edited}
          </button>
        </div>
        <input className="search" type="search" placeholder="대사 찾기" value={query} onChange={(e) => setQuery(e.target.value)} />
        <span className="spacer" />
        <button className="btn" onClick={retranslate} disabled={!picked.size}>
          {picked.size ? `${picked.size}줄 다시 번역` : "고른 줄 다시 번역"}
        </button>
        <button className="btn primary" onClick={exportFiles} disabled={source !== "ko"}>
          자막 파일 저장
        </button>
      </div>

      {exported.length > 0 && (
        <ul className="exported">
          {exported.map((p) => (
            <li key={p}>
              <span className="path">{p}</span>
              {inTauri && <button className="link" onClick={() => api.post("/fs/reveal", { path: p })}>폴더 열기</button>}
            </li>
          ))}
        </ul>
      )}

      <div className="lines" ref={listRef} role="grid" aria-label="자막 줄"
        onKeyDown={(e) => {
          if ((e.target as HTMLElement).tagName === "TEXTAREA" && !e.altKey) return;
          if (e.key === "ArrowDown") { e.preventDefault(); move(1); }
          if (e.key === "ArrowUp") { e.preventDefault(); move(-1); }
        }}>
        {shown.map((ln) => (
          <LineRow key={ln.id} line={ln} selected={ln.id === sel} picked={picked.has(ln.id)}
            maxChars={options.output.ko_max_line_chars}
            onSelect={() => setSel(ln.id)}
            onPick={(v) => setPicked((s) => { const n = new Set(s); if (v) n.add(ln.id); else n.delete(ln.id); return n; })}
            onSave={(patch) => save(ln.id, patch)}
            onNext={() => move(1)} />
        ))}
        {shown.length === 0 && <p className="quiet pad">조건에 맞는 줄이 없습니다.</p>}
      </div>
      {toast && <div className="toast" role="status">{toast}</div>}
    </div>
  );
}

// Korean subtitle norms: at most two lines, and roughly 15 characters per second to stay readable
const MAX_CPS = 15;

function LineRow({ line, selected, picked, maxChars, onSelect, onPick, onSave, onNext }: {
  line: Line; selected: boolean; picked: boolean; maxChars: number; onSelect: () => void; onPick: (v: boolean) => void;
  onSave: (p: Partial<Pick<Line, "ko" | "start" | "end">>) => void; onNext: () => void;
}) {
  const [ko, setKo] = useState(line.ko);
  useEffect(() => setKo(line.ko), [line.ko]);
  const failed = line.flags.includes("translate_failed") || !line.ko.trim();
  const chars = ko.replace(/\s/g, "").length;
  const cps = chars / Math.max(0.3, line.end - line.start);
  const warn = ko.length > maxChars * 2 ? `두 줄(${maxChars * 2}자)을 넘습니다`
    : cps > MAX_CPS ? `초당 ${cps.toFixed(0)}자로 읽기 빠듯합니다` : null;

  function commit() {
    if (ko !== line.ko) onSave({ ko });
  }

  function editTime(which: "start" | "end", text: string) {
    const v = parseTc(text);
    if (v === null || v === line[which]) return;
    onSave({ [which]: v });
  }

  return (
    <div className={`line${selected ? " sel" : ""}${failed ? " failed" : ""}`} data-id={line.id} role="row"
      onClick={onSelect} onFocus={onSelect}>
      <input type="checkbox" aria-label={`${line.id}번 줄 고르기`} checked={picked} onChange={(e) => onPick(e.target.checked)}
        onClick={(e) => e.stopPropagation()} />
      <div className="line-tc">
        <TimeInput value={line.start} onCommit={(t) => editTime("start", t)} label="시작" />
        <TimeInput value={line.end} onCommit={(t) => editTime("end", t)} label="끝" />
      </div>
      <p className="line-ja" lang="ja">
        {line.flags.includes("song") && <span className="tag">노래</span>}
        {line.ja}
      </p>
      <textarea className="line-ko" rows={Math.max(1, Math.ceil(ko.length / 28))} value={ko}
        aria-label={`${line.id}번 줄 번역`} placeholder="번역이 비어 있습니다"
        onChange={(e) => setKo(e.target.value)} onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); commit(); onNext(); }
          if (e.key === "Escape") setKo(line.ko);
        }} />
      <span className="line-state">
        {line.locked && <span title="직접 고친 줄. 번역을 다시 해도 바뀌지 않습니다">고침</span>}
        {warn && <span className="warn" title={warn}>길다</span>}
      </span>
    </div>
  );
}

function TimeInput({ value, onCommit, label }: { value: number; onCommit: (t: string) => void; label: string }) {
  const [text, setText] = useState(tc(value));
  useEffect(() => setText(tc(value)), [value]);
  return (
    <input className="tc" value={text} aria-label={label} onChange={(e) => setText(e.target.value)}
      onBlur={() => onCommit(text)} onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()} />
  );
}
