import { useCallback } from "react";
import { api, basename, clock, kindBadge, type LiveState, mmss, type Stage, STAGE_NAMES, STAGES, type Usage } from "./api";
import { useApp } from "./ctx";
import { type JobDetail, type ProgressEvent, type StageState, useJob } from "./job";
import { KindBadge, Note, Page } from "./ui";

const STEP_NAMES: Record<Stage, string> = { ...STAGE_NAMES, segment: "자막 단위 분할", export: "자막 쓰기" };

export function Progress({ jobKey }: { jobKey?: string }) {
  const { go, refreshJobs } = useApp();
  const onFinish = useCallback((status: string) => {
    refreshJobs();
    if (status === "done" && jobKey) go({ kind: "review", key: jobKey });
  }, [go, jobKey, refreshJobs]);
  const { detail, live, stages, log, usage, missing } = useJob(jobKey, onFinish);

  if (!jobKey || missing) {
    return (
      <Page eyebrow="PROGRESS" title="진행 중인 작업">
        <div className="empty">
          <b>지금 처리 중인 작업이 없습니다</b>
          <span>홈에서 영상을 고르면 여기에서 단계별 진행 상황을 볼 수 있습니다.</span>
          <button className="btn primary" onClick={() => go({ kind: "home" })}>새 작업</button>
        </div>
      </Page>
    );
  }
  if (!detail) return <Page eyebrow="PROGRESS" title="불러오는 중…"><p className="muted">불러오는 중…</p></Page>;

  const name = detail.meta.video_name ?? basename(detail.meta.video_path);
  const running = !!live && (live.status === "running" || live.status === "queued");
  const engine = live?.engine ?? null;
  const t = detail.options.translate;
  const cloud = engine ? engine.cloud : t.mode === "anthropic" || t.mode === "gemini";
  const kb = kindBadge(engine?.kind ?? t.mode);
  const profileName = engine?.profile_name ?? t.profile_name ?? "—";
  const overall = running ? live!.progress : live?.status === "done" || (!live && detail.meta.stages.translate) ? 1 : live?.progress ?? 0;
  const startedAt = live?.started_at;
  const elapsed = startedAt ? Date.now() / 1000 - startedAt : 0;
  const eta = running && overall > 0.05 && elapsed > 5 ? (elapsed / overall) * (1 - overall) : null;
  const curStage = live?.stage ?? null;
  const steps = STAGES.filter((s) => s !== "separate" || detail.options.separate.enabled);
  const curNo = curStage ? steps.indexOf(curStage) + 1 : 0;

  let eyebrow = "PROGRESS";
  if (live?.status === "running") eyebrow = `RUNNING · ${clock(startedAt)} 시작`;
  else if (live?.status === "queued") eyebrow = "QUEUED · 앞 작업을 기다리는 중";
  else if (live?.status === "error") eyebrow = `FAILED · ${clock(live.finished_at)}`;
  else if (live?.status === "cancelled") eyebrow = `CANCELLED · ${clock(live.finished_at)}`;
  else if (live?.status === "done" || detail.meta.stages.translate) eyebrow = `DONE${live?.finished_at ? ` · ${clock(live.finished_at)} 완료` : ""}`;

  async function rerun() {
    await api.post("/jobs", { video_path: detail!.meta.video_path, options: detail!.options, profile_id: t.profile_id });
    refreshJobs();
  }

  return (
    <Page eyebrow={eyebrow} title={name} actions={<>
      {running && <button className="btn danger" onClick={() => api.post(`/jobs/${jobKey}/cancel`)}>작업 취소</button>}
      <button className="btn" disabled={!detail.meta.stages.segment} onClick={() => go({ kind: "review", key: jobKey })}>검수 화면으로</button>
    </>}>
      <section aria-label="전체 진행률" className="card" style={{ gap: 8 }}>
        <div className="card-head" style={{ alignItems: "baseline" }}>
          <span style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
            <span className="muted" style={{ fontSize: 12 }}>전체</span>
            <span className="big-pct">{Math.round(overall * 100)}%</span>
          </span>
          <span style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 6, fontSize: 12 }} className="muted">
            <KindBadge cloud={kb.cloud} text={kb.text} />
            <b style={{ color: "var(--text)" }}>{profileName}</b>
            {running && curStage && <> · {curNo} {STEP_NAMES[curStage]} 중</>}
            {eta !== null && <> · 남은 시간 약 {eta < 60 ? `${Math.max(5, Math.round(eta / 5) * 5)}초` : `${Math.floor(eta / 60)}분 ${Math.round(eta % 60)}초`}</>}
          </span>
        </div>
        <div className="bar big" role="progressbar" aria-label="전체 진행률" aria-valuenow={Math.round(overall * 100)} aria-valuemin={0} aria-valuemax={100}>
          <span style={{ width: `${overall * 100}%` }} />
        </div>
      </section>

      {live?.status === "error" && (
        <Note tone="err">
          처리 중 오류가 났습니다: {live.error}{" "}
          <button className="btn sm" style={{ marginLeft: 6 }} onClick={rerun}>다시 시도</button>
        </Note>
      )}
      {live?.status === "cancelled" && (
        <Note>작업을 취소했습니다. 끝난 단계는 저장되어 있어 이어서 할 수 있습니다. <button className="btn sm" onClick={rerun}>이어서 하기</button></Note>
      )}

      <div className="cols">
        <section aria-labelledby="steps-h" className="card" style={{ flex: "1 1 480px", gap: 0 }}>
          <h2 id="steps-h" style={{ marginBottom: 4 }}>단계</h2>
          <ol className="steps">
            {steps.map((s, i) => (
              <Step key={s} no={i + 1} stage={s} detail={detail} st={stages[s]} live={live} running={running} usage={usage} />
            ))}
          </ol>
        </section>

        <div className="stack" style={{ flex: "1 1 320px" }}>
          {cloud ? <UsageCard usage={usage ?? (live?.stats?.translate as unknown as Usage | undefined) ?? null} /> : (
            <ServerCard detail={detail} log={log} stages={stages} running={running} model={engine?.model} />
          )}
          <LogCard log={log} live={live} steps={steps} />
        </div>
      </div>
    </Page>
  );
}

function Step({ no, stage, detail, st, live, running, usage }: {
  no: number; stage: Stage; detail: JobDetail; st?: StageState; live: LiveState | null; running: boolean; usage: Usage | null;
}) {
  // while a run is in progress, meta.json still holds the previous run: trust it only for finished steps
  const fresh = !running || st?.status === "done" || st?.status === "skipped";
  const meta = fresh ? detail.meta.stages[stage] as Record<string, unknown> | undefined : undefined;
  const o = detail.options;
  let cls = "wait";
  let state = "대기";
  let frac: number | null = null;
  if (st && running) {
    if (st.status === "done") { cls = "done"; state = `완료 · ${mmss(st.elapsed ?? 0)}`; }
    else if (st.status === "skipped") { cls = "done"; state = "저장된 결과 사용"; }
    else { cls = "run"; frac = st.progress; state = `진행 중 · ${Math.round(st.progress * 100)}%`; }
  } else if (live?.status === "error" && live.stage === stage) {
    cls = "fail"; state = "실패";
  } else if (!running && meta?.elapsed !== undefined) {
    cls = "done"; state = `완료 · ${mmss(Number(meta.elapsed))}`;
  } else if (stage === "export" && !running && (live?.status === "done" || (detail.meta.last_export?.length ?? 0) > 0)) {
    cls = "done"; state = "완료";
  }

  const stem = (detail.meta.video_name ?? basename(detail.meta.video_path)).replace(/\.[^.]+$/, "");
  const t = o.translate;
  let info = "";
  switch (stage) {
    case "audio": {
      const tr = meta?.track ?? o.audio.track;
      info = `FFmpeg · 16kHz 모노${tr !== undefined && tr !== null ? ` · 트랙 #${Number(tr) + 1}` : ""}${meta?.language ? ` (${meta.language})` : ""}`;
      break;
    }
    case "separate": info = `Demucs ${o.separate.model} · 끝난 뒤 GPU 메모리 해제`; break;
    case "stt": info = `faster-whisper ${o.stt.model} · Silero VAD · 단어 타임스탬프${meta?.segments ? ` · 발화 ${meta.segments}개` : ""}`; break;
    case "segment": info = meta?.lines ? `${meta.lines}줄${meta.dropped ? ` · 걸러 낸 구간 ${meta.dropped}개` : ""}` : `한 줄 최대 ${o.segment.max_chars}자 · 최대 ${o.segment.max_duration}초`; break;
    case "translate": {
      const model = t.mode === "managed" ? `llama-server · ${t.model_path ? basename(t.model_path) : "기본 GGUF"}`
        : t.mode === "external" ? `${t.model_name} @ ${t.base_url}` : t.api_model ?? "";
      const b = usage?.batches_total ? ` · 묶음 ${usage.batches_done} / ${usage.batches_total}` : "";
      info = `${model}${b}${t.parallel > 1 ? ` · 동시 요청 ${t.parallel}` : ""}`;
      break;
    }
    case "export": {
      const fmts = o.output.formats.map((f) => `${stem}.ko.${f}`);
      if (o.output.dual_ass) fmts.push(`${stem}.ja-ko.ass`);
      info = fmts.join(" · ");
      break;
    }
  }
  if (cls === "run" && st?.message && !/^\d+\/\d+ lines$/.test(st.message)) info += ` · ${st.message}`;

  return (
    <li className={cls}>
      <span className="step-no" aria-hidden>{no}</span>
      <span className="step-body">
        <span className="step-top">
          <b>{STEP_NAMES[stage]}</b>
          <span className="step-state">{state}</span>
        </span>
        <span className="step-detail">{info}</span>
        {frac !== null && <span className="bar" aria-hidden><span style={{ width: `${frac * 100}%` }} /></span>}
      </span>
    </li>
  );
}

function UsageCard({ usage }: { usage: Usage | null }) {
  const n = (v: number | undefined) => (v ?? 0).toLocaleString("en-US");
  return (
    <section aria-labelledby="use-h" className="card" style={{ borderColor: "var(--s-line)", gap: 8 }}>
      <h2 id="use-h">API 사용량</h2>
      <dl className="usage">
        <div><dt>입력 토큰</dt><dd>{n(usage?.prompt_tokens)}</dd></div>
        <div><dt>출력 토큰</dt><dd>{n(usage?.completion_tokens)}</dd></div>
        <div><dt>요청</dt><dd>{usage?.batches_total ? `${usage.batches_done} / ${usage.batches_total}` : n(usage?.requests)}</dd></div>
        <div><dt>예상 비용</dt><dd>{usage?.cost_usd != null ? `≈ $${usage.cost_usd.toFixed(2)}` : "—"}</dd></div>
      </dl>
      <p className="small muted" style={{ margin: 0 }}>
        {usage?.retries ? `재시도 ${usage.retries}회. ` : ""}
        {usage?.refused_lines ? `거절 ${usage.refused_lines}줄. ` : ""}
        {usage?.cache_read_tokens ? `캐시에서 읽은 토큰 ${n(usage.cache_read_tokens)}. ` : ""}
        전송 데이터: 일본어 대사 텍스트만.
      </p>
    </section>
  );
}

function ServerCard({ detail, log, stages, running, model }: {
  detail: JobDetail; log: ProgressEvent[]; stages: Partial<Record<Stage, StageState>>; running: boolean; model?: string;
}) {
  const t = detail.options.translate;
  const ready = [...log].reverse().find((e) => /llama-server ready :(\d+)/.test(e.message));
  const port = ready ? /:(\d+)/.exec(ready.message)?.[1] : null;
  const tr = stages.translate;
  const sttDone = stages.stt?.status === "done" || stages.stt?.status === "skipped" || (!running && !!detail.meta.stages.stt);
  let server = "번역 단계에서 시작";
  if (t.mode === "external") server = t.base_url ?? "";
  else if (running && tr && tr.status !== "done" && tr.status !== "skipped") server = port ? `실행 중 · 127.0.0.1:${port}` : "시작하는 중";
  else if (tr?.status === "done" || (!running && detail.meta.stages.translate)) server = "종료됨 · GPU 메모리 해제";
  return (
    <section aria-labelledby="srv-h" className="card" style={{ gap: 6 }}>
      <h2 id="srv-h">번역 서버</h2>
      <ul className="kv">
        <li><span>{t.mode === "external" ? "외부 서버" : "llama-server"}</span><span>{server}</span></li>
        <li><span>모델</span><span>{model ?? (t.model_path ? basename(t.model_path) : "기본 GGUF")}</span></li>
        <li><span>Whisper 모델</span><span>{sttDone ? "해제됨" : stages.stt ? "사용 중" : "대기"}</span></li>
        <li><span>병렬 슬롯</span><span>{t.parallel}</span></li>
      </ul>
    </section>
  );
}

function LogCard({ log, live, steps }: { log: ProgressEvent[]; live: LiveState | null; steps: Stage[] }) {
  const lines: { t?: number; m: string }[] = [];
  let last = "";
  for (const e of log) {
    const no = steps.indexOf(e.stage) + 1;
    const nm = STEP_NAMES[e.stage];
    let m = "";
    if (e.status === "start") m = `${no} ${nm} 시작`;
    else if (e.status === "done") m = `${no} ${nm} 완료${e.elapsed !== undefined ? ` · ${e.elapsed.toFixed(1)}초` : ""}`;
    else if (e.status === "skipped") m = `${no} ${nm} · 저장된 결과 사용`;
    else if (e.message && !/^\d+\/\d+ lines$/.test(e.message) && !/^\d+(\.\d+)?%/.test(e.message) && !/^\d+(\.\d+)?s\b/.test(e.message)) m = `${no} ${e.message}`;
    if (m && m !== last) lines.push({ t: e.t, m });
    if (m) last = m;
  }
  if (live?.status === "error") lines.push({ t: live.finished_at ?? undefined, m: `오류: ${live.error}` });
  const shown = lines.slice(-40);
  return (
    <section aria-labelledby="log-h" className="log">
      <h2 id="log-h">LOG · 로그</h2>
      {shown.length === 0 ? <p className="small muted" style={{ margin: 0 }}>이 세션에서 실행한 기록이 여기에 표시됩니다.</p> : (
        <ol>
          {shown.map((l, i) => <li key={i}><time>{clock(l.t, true)}</time> {l.m}</li>)}
        </ol>
      )}
    </section>
  );
}
