import { useCallback, useEffect, useMemo, useState } from "react";
import { api, basename, clock, inTauri, type Line, parseTc, tc } from "./api";
import { useApp } from "./ctx";
import { useJob } from "./job";
import { Icon, ICONS, Modal, Note, Page, useToast } from "./ui";

// Korean subtitle norms: at most two lines, and roughly 15 characters per second to stay readable
const MAX_CPS = 15;
const PAGE = 100;
const MODE_LABEL: Record<string, string> = { managed: "로컬 내장", external: "외부 서버", anthropic: "Claude", gemini: "Gemini" };

type Filter = "all" | "long" | "locked" | "failed";

function isFailed(ln: Line): boolean {
  return ln.flags.includes("translate_failed") || ln.flags.includes("refused") || (!ln.ko.trim() && !!ln.ja.trim());
}

function lengthIssue(ko: string, ln: Line, maxChars: number): string | null {
  if (ko.length > maxChars) return `한 자막 ${maxChars}자를 넘습니다`;
  const cps = ko.replace(/\s/g, "").length / Math.max(0.3, ln.end - ln.start);
  return cps > MAX_CPS ? `초당 ${cps.toFixed(0)}자로 읽기 빠듯합니다` : null;
}

export function Review({ jobKey }: { jobKey?: string }) {
  const { go, profiles, refreshJobs } = useApp();
  const [lines, setLines] = useState<Line[] | null>(null);
  const [source, setSource] = useState<"ko" | "ja">("ko");
  const [noLines, setNoLines] = useState(false);
  const [picked, setPicked] = useState<Set<number>>(new Set());
  const [filter, setFilter] = useState<Filter>("all");
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(0);
  const [fmt, setFmt] = useState("job");
  const [retrProfile, setRetrProfile] = useState<number | null>(null);
  const [exported, setExported] = useState<string[]>([]);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [toast, flash] = useToast();

  const loadLines = useCallback(async () => {
    if (!jobKey) return;
    try {
      const r = await api.get<{ source: "ko" | "ja"; lines: Line[] }>(`/jobs/${jobKey}/segments`);
      setLines(r.lines);
      setSource(r.source);
    } catch {
      setNoLines(true);
    }
  }, [jobKey]);

  const onFinish = useCallback((status: string) => {
    loadLines();
    refreshJobs();
    if (status === "done") flash("다시 번역했습니다.");
    if (status === "error") flash("다시 번역하지 못했습니다. 진행 화면에서 오류를 확인하세요.", true);
  }, [loadLines, refreshJobs, flash]);
  const { detail, live, missing, reload } = useJob(jobKey, onFinish);

  useEffect(() => {
    loadLines();
  }, [loadLines]);

  const exportKey = (detail?.meta.last_export ?? []).join("|");
  useEffect(() => setExported(exportKey ? exportKey.split("|") : []), [exportKey]);

  const maxChars = (detail?.options.output.ko_max_line_chars ?? 20) * 2;
  const counts = useMemo(() => {
    const c = { long: 0, locked: 0, failed: 0 };
    for (const ln of lines ?? []) {
      if (lengthIssue(ln.ko, ln, maxChars)) c.long++;
      if (ln.locked) c.locked++;
      if (isFailed(ln)) c.failed++;
    }
    return c;
  }, [lines, maxChars]);

  const shown = useMemo(() => {
    const q = query.trim();
    return (lines ?? []).filter((ln) => {
      if (filter === "long" && !lengthIssue(ln.ko, ln, maxChars)) return false;
      if (filter === "locked" && !ln.locked) return false;
      if (filter === "failed" && !isFailed(ln)) return false;
      return !q || ln.ja.includes(q) || ln.ko.includes(q);
    });
  }, [lines, filter, query, maxChars]);

  if (!jobKey || missing || noLines) {
    return (
      <Page eyebrow="REVIEW" title="검수·편집">
        <div className="empty">
          <b>{jobKey ? "아직 만든 자막이 없습니다" : "검수할 작업이 없습니다"}</b>
          <span>작업이 끝나면 원문과 번역을 나란히 보며 고치고 저장할 수 있습니다.</span>
          <button className="btn primary" onClick={() => go({ kind: jobKey ? "progress" : "home", key: jobKey } as never)}>
            {jobKey ? "진행 화면으로" : "새 작업"}
          </button>
        </div>
      </Page>
    );
  }
  if (!detail || !lines) return <Page eyebrow="REVIEW" title="불러오는 중…"><p className="muted">자막을 불러오는 중…</p></Page>;

  const name = detail.meta.video_name ?? basename(detail.meta.video_path);
  const t = detail.options.translate;
  const busy = !!live && (live.status === "running" || live.status === "queued");
  const retranslating = busy && live?.kind === "retranslate";
  const finished = detail.meta.stages.translate?.finished ?? detail.meta.updated;
  const pages = Math.max(1, Math.ceil(shown.length / PAGE));
  const cur = Math.min(page, pages - 1);
  const slice = shown.slice(cur * PAGE, cur * PAGE + PAGE);
  const out = detail.options.output;
  const retr = retrProfile ?? t.profile_id ?? profiles.find((p) => p.is_default)?.id ?? null;

  async function save(id: number, patch: Partial<Pick<Line, "ko" | "start" | "end">>) {
    try {
      const r = await api.patch<{ lines: Line[] }>(`/jobs/${jobKey}/segments`, { edits: [{ id, ...patch }] });
      setLines(r.lines);
    } catch (e) {
      flash(`저장하지 못했습니다: ${(e as Error).message}`, true);
    }
  }

  async function retranslate() {
    const ids = [...picked];
    if (!ids.length) return;
    try {
      await api.post(`/jobs/${jobKey}/retranslate`, { ids, profile_id: retr });
      setPicked(new Set());
      reload();
    } catch (e) {
      flash(`다시 번역을 시작하지 못했습니다: ${(e as Error).message}`, true);
    }
  }

  async function exportFiles() {
    const output = fmt === "job" ? out
      : fmt === "dual" ? { ...out, formats: [], include_ja: false, dual_ass: true }
        : { ...out, formats: [fmt], dual_ass: false };
    try {
      const r = await api.post<{ written: string[] }>(`/jobs/${jobKey}/export`, { output });
      setExported(r.written);
      reload();
      flash(`${r.written.length}개 파일을 저장했습니다.`);
    } catch (e) {
      flash(`저장하지 못했습니다: ${(e as Error).message}`, true);
    }
  }

  async function retranslateAll(profileId: number | null) {
    try {
      await api.post("/jobs", { video_path: detail!.meta.video_path, options: detail!.options, force_from: "translate", profile_id: profileId });
      await refreshJobs();
      go({ kind: "progress", key: jobKey });
    } catch (e) {
      flash(`번역을 다시 시작하지 못했습니다: ${(e as Error).message}`, true);
    }
  }

  const jobFormats = [...out.formats.map((f) => f.toUpperCase()), ...(out.dual_ass ? ["이중 ASS"] : [])].join(" + ") || "—";

  return (
    <Page wide
      eyebrow={`REVIEW${finished ? ` · ${clock(finished)} 완료` : ""} · ${lines.length}줄 · ${t.profile_name ?? MODE_LABEL[t.mode] ?? t.mode}`}
      title={name}
      actions={<>
        <label className="field row">
          형식
          <select className="input" value={fmt} onChange={(e) => setFmt(e.target.value)}>
            <option value="job">작업 설정 ({jobFormats})</option>
            <option value="srt">SRT</option>
            <option value="ass">ASS</option>
            <option value="vtt">VTT</option>
            <option value="dual">이중 자막 ASS (일본어 + 한국어)</option>
          </select>
        </label>
        {inTauri && (
          <button className="btn" onClick={() => api.post("/fs/reveal", { path: exported[0] ?? detail.meta.video_path })}>폴더 열기</button>
        )}
        <button className="btn primary" onClick={exportFiles} disabled={source !== "ko" || busy}>
          {out.out_dir ? "지정한 폴더에 저장" : "영상 옆에 저장"}
        </button>
      </>}
    >
      <div className="sticky-tools">
        <div className="tools-row">
          <label className="search">
            <Icon d={ICONS.search} size={14} />
            <span className="sr-only">원문 또는 번역 검색</span>
            <input type="search" placeholder="원문·번역에서 찾기" value={query} onChange={(e) => { setQuery(e.target.value); setPage(0); }} />
          </label>
          <div role="group" aria-label="필터" className="filters">
            <button className="chip" aria-pressed={filter === "all"} onClick={() => { setFilter("all"); setPage(0); }}>전체 {lines.length}</button>
            <button className="chip warn" aria-pressed={filter === "long"} onClick={() => { setFilter("long"); setPage(0); }}>길이 초과 {counts.long}</button>
            <button className="chip" aria-pressed={filter === "locked"} onClick={() => { setFilter("locked"); setPage(0); }}>수정됨·잠금 {counts.locked}</button>
            {counts.failed > 0 && (
              <button className="chip err" aria-pressed={filter === "failed"} onClick={() => { setFilter("failed"); setPage(0); }}>번역 빠짐·거절 {counts.failed}</button>
            )}
          </div>
        </div>
        {retranslating ? (
          <div className="selbar" role="status">
            <b>다시 번역하는 중 · {Math.round((live?.progress ?? 0) * 100)}%</b>
            <span className="muted">{live?.message}</span>
            <button className="btn sm end" onClick={() => api.post(`/jobs/${jobKey}/cancel`)}>멈추기</button>
          </div>
        ) : picked.size > 0 && (
          <div className="selbar" role="region" aria-label="선택한 줄 작업">
            <b>{picked.size}줄 선택됨</b>
            <label className="field row">
              다시 번역할 프로필
              <select className="input sm" value={retr ?? ""} onChange={(e) => setRetrProfile(Number(e.target.value))}>
                {profiles.map((p) => (
                  <option key={p.id} value={p.id} disabled={!p.status.ready}>
                    {p.name}{p.model && p.cloud ? ` (${p.model})` : ""}{p.status.ready ? "" : " · 준비 안 됨"}
                  </option>
                ))}
              </select>
            </label>
            <button className="btn primary sm" onClick={retranslate} disabled={busy}>선택한 줄 다시 번역</button>
            <span className="small muted">고른 줄은 직접 고쳤더라도 새 번역으로 바뀝니다</span>
            <button className="btn sm end" onClick={() => setPicked(new Set())}>선택 해제</button>
          </div>
        )}
      </div>

      {source !== "ko" && <Note tone="warn">아직 번역 전입니다. 원문만 보여 줍니다.</Note>}

      <div className="table-wrap">
        <table className="t lines">
          <thead>
            <tr>
              <th scope="col" style={{ width: 32 }}>
                <input type="checkbox" aria-label="이 쪽의 줄 모두 선택"
                  checked={slice.length > 0 && slice.every((l) => picked.has(l.id))}
                  onChange={(e) => setPicked((s) => {
                    const n = new Set(s);
                    for (const l of slice) { if (e.target.checked) n.add(l.id); else n.delete(l.id); }
                    return n;
                  })} />
              </th>
              <th scope="col" style={{ width: 34 }}>#</th>
              <th scope="col" style={{ width: 104 }}>시간</th>
              <th scope="col" style={{ width: "36%" }}>원문 (일본어)</th>
              <th scope="col" style={{ width: "44%" }}>번역 (한국어)</th>
              <th scope="col" style={{ width: 96 }}>상태</th>
            </tr>
          </thead>
          <tbody>
            {slice.map((ln) => (
              <Row key={ln.id} line={ln} maxChars={maxChars} picked={picked.has(ln.id)} disabled={busy}
                onPick={(v) => setPicked((s) => { const n = new Set(s); if (v) n.add(ln.id); else n.delete(ln.id); return n; })}
                onSave={(p) => save(ln.id, p)} />
            ))}
          </tbody>
        </table>
        {slice.length === 0 && <p className="empty">조건에 맞는 줄이 없습니다.</p>}
      </div>

      <div className="pager">
        <span>{shown.length ? `${cur * PAGE + 1}–${cur * PAGE + slice.length}` : 0} / {shown.length}줄 · 한 자막 최대 {maxChars}자 · 초당 {MAX_CPS}자</span>
        <span style={{ display: "flex", gap: 4 }}>
          <button className="btn" aria-label="이전 쪽" disabled={cur === 0} onClick={() => setPage(cur - 1)}>‹</button>
          <button className="btn" aria-label="다음 쪽" disabled={cur >= pages - 1} onClick={() => setPage(cur + 1)}>›</button>
        </span>
      </div>

      {exported.length > 0 && (
        <section className="card exports" aria-label="저장한 파일">
          <span className="small muted">저장한 파일</span>
          <ul>
            {exported.map((p) => (
              <li key={p}>
                <span className="mono small wrap-any">{p}</span>
                {inTauri && <button className="link" onClick={() => api.post("/fs/reveal", { path: p })}>폴더 열기</button>}
              </li>
            ))}
          </ul>
        </section>
      )}

      <section className="card" aria-label="작업 관리" style={{ flexDirection: "row", flexWrap: "wrap", alignItems: "center", gap: 8 }}>
        <span className="small muted" style={{ flex: "1 1 260px" }}>
          음성 인식 결과는 그대로 두고 번역만 처음부터 다시 할 수 있습니다. 직접 고친 줄은 유지합니다.
        </span>
        <RetranslateAll profiles={profiles.filter((p) => p.status.ready)} initial={t.profile_id} disabled={busy} onRun={retranslateAll} />
        <button className="btn danger" disabled={busy} onClick={() => setConfirmDelete(true)}>작업 지우기</button>
      </section>

      {confirmDelete && (
        <Modal title="작업을 지울까요?" onClose={() => setConfirmDelete(false)} actions={<>
          <button className="btn" onClick={() => setConfirmDelete(false)}>취소</button>
          <button className="btn danger" onClick={async () => {
            await api.del(`/jobs/${jobKey}?purge=true`);
            setConfirmDelete(false);
            await refreshJobs();
            go({ kind: "home" });
          }}>작업 지우기</button>
        </>}>
          <p>저장해 둔 중간 결과(음성 인식, 번역, 고친 줄)를 지웁니다. 이미 저장한 자막 파일은 그대로 남습니다.</p>
        </Modal>
      )}
      {toast}
    </Page>
  );
}

function RetranslateAll({ profiles, initial, disabled, onRun }: {
  profiles: { id: number; name: string }[]; initial: number | null; disabled: boolean; onRun: (id: number | null) => void;
}) {
  const [id, setId] = useState<number | null>(initial);
  return (
    <span style={{ display: "flex", gap: 6, alignItems: "center" }}>
      <select className="input" aria-label="번역 전체를 다시 할 프로필" value={id ?? ""} onChange={(e) => setId(Number(e.target.value))}>
        {profiles.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
      </select>
      <button className="btn" disabled={disabled} onClick={() => onRun(id)}>번역 전체 다시 하기</button>
    </span>
  );
}

function Row({ line, maxChars, picked, disabled, onPick, onSave }: {
  line: Line; maxChars: number; picked: boolean; disabled: boolean; onPick: (v: boolean) => void;
  onSave: (p: Partial<Pick<Line, "ko" | "start" | "end">>) => void;
}) {
  const [ko, setKo] = useState(line.ko);
  useEffect(() => setKo(line.ko), [line.ko]);
  const issue = lengthIssue(ko, line, maxChars);
  const failed = isFailed(line);
  const refused = line.flags.includes("refused");

  function commit() {
    if (ko !== line.ko) onSave({ ko });
  }

  return (
    <tr className={picked ? "sel" : undefined}>
      <td style={{ paddingTop: 9 }}>
        <input type="checkbox" aria-label={`${line.id}번 줄 선택`} checked={picked} onChange={(e) => onPick(e.target.checked)} />
      </td>
      <td className="id">{line.id}</td>
      <td className="tc">
        <TimeInput value={line.start} label={`${line.id}번 줄 시작`} disabled={disabled} onCommit={(v) => onSave({ start: v })} />
        <TimeInput value={line.end} label={`${line.id}번 줄 끝`} disabled={disabled} onCommit={(v) => onSave({ end: v })} />
      </td>
      <td className="ja" lang="ja">{line.ja}</td>
      <td style={{ padding: "4px 6px" }}>
        <span className="ko-cell">
          <textarea aria-label={`${line.id}번 줄 번역`} rows={Math.max(1, Math.ceil(ko.length / 30))} value={ko}
            className={issue ? "warn" : failed ? "err" : undefined} disabled={disabled}
            placeholder={refused ? "서비스가 이 줄의 번역을 거절했습니다" : "번역이 비어 있습니다"}
            onChange={(e) => setKo(e.target.value)} onBlur={commit}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); (e.target as HTMLTextAreaElement).blur(); }
              if (e.key === "Escape") setKo(line.ko);
            }} />
          <span className={`ko-len${issue ? " warn" : ""}`} title={issue ?? undefined}>{ko.length}</span>
        </span>
      </td>
      <td style={{ paddingTop: 8 }}>
        <span className="badges">
          {line.locked && (
            <span className="badge lock" title="직접 고친 줄. 전체 번역을 다시 해도 바뀌지 않습니다">
              <Icon d={ICONS.lock} size={10} width={2.6} />수정됨
            </span>
          )}
          {issue && <span className="badge warn" title={issue}>길이 초과</span>}
          {refused && <span className="badge err">거절됨</span>}
          {!refused && failed && <span className="badge err">번역 빠짐</span>}
          {line.flags.includes("local_fallback") && <span className="badge" title="클라우드가 거절해 로컬 모델로 번역했습니다">로컬 대체</span>}
          {line.flags.includes("song") && <span className="badge">노래</span>}
          {!line.locked && !issue && !failed && !line.flags.includes("song") && !line.flags.includes("local_fallback") && <span className="small muted">—</span>}
        </span>
      </td>
    </tr>
  );
}

function TimeInput({ value, label, disabled, onCommit }: { value: number; label: string; disabled: boolean; onCommit: (v: number) => void }) {
  const [text, setText] = useState(tc(value));
  useEffect(() => setText(tc(value)), [value]);
  return (
    <input className="tc-in" value={text} aria-label={label} disabled={disabled} onChange={(e) => setText(e.target.value)}
      onBlur={() => { const v = parseTc(text); if (v === null) setText(tc(value)); else if (v !== value) onCommit(v); }}
      onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()} />
  );
}
