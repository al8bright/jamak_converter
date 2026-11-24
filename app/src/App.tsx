import { useCallback, useEffect, useState } from "react";
import { api, basename, connect, inTauri, type JobSummary, type SystemInfo, tc } from "./api";
import { NewJob } from "./NewJob";
import { JobView } from "./JobView";

type View = { kind: "new"; path?: string } | { kind: "job"; key: string };

export default function App() {
  const [ready, setReady] = useState<"wait" | "ok" | string>("wait");
  const [sys, setSys] = useState<SystemInfo | null>(null);
  const [jobs, setJobs] = useState<JobSummary[]>([]);
  const [view, setView] = useState<View>({ kind: "new" });

  const refresh = useCallback(async () => {
    setJobs(await api.get<JobSummary[]>("/jobs"));
  }, []);

  useEffect(() => {
    connect()
      .then(async () => {
        setReady("ok");
        setSys(await api.get<SystemInfo>("/system"));
        await refresh();
      })
      .catch((e) => setReady(String(e?.message ?? e)));
  }, [refresh]);

  // files dropped anywhere on the window start a new job
  useEffect(() => {
    if (!inTauri) return;
    let unlisten: (() => void) | undefined;
    import("@tauri-apps/api/webview").then(({ getCurrentWebview }) =>
      getCurrentWebview()
        .onDragDropEvent((e) => {
          if (e.payload.type === "drop" && e.payload.paths.length) {
            setView({ kind: "new", path: e.payload.paths[0] });
          }
        })
        .then((u) => (unlisten = u)),
    );
    return () => unlisten?.();
  }, []);

  // keep the rail fresh while something runs
  useEffect(() => {
    if (ready !== "ok") return;
    const anyLive = jobs.some((j) => j.live && (j.live.status === "running" || j.live.status === "queued"));
    const t = setInterval(refresh, anyLive ? 1500 : 8000);
    return () => clearInterval(t);
  }, [ready, jobs, refresh]);

  if (ready === "wait") return <Splash text="백엔드를 시작하는 중" />;
  if (ready !== "ok") return <Splash text={ready} error />;

  return (
    <div className="shell">
      <aside className="rail">
        <div className="brand">
          <span className="brand-mark" aria-hidden>字</span>
          <span>jamak</span>
        </div>
        <button className="btn primary wide" onClick={() => setView({ kind: "new" })}>
          새 자막 만들기
        </button>
        <nav className="history" aria-label="작업 목록">
          {jobs.length === 0 && <p className="quiet small">만든 자막이 여기에 쌓입니다.</p>}
          {jobs.map((j) => (
            <button
              key={j.key}
              className={`job-item${view.kind === "job" && view.key === j.key ? " current" : ""}`}
              onClick={() => setView({ kind: "job", key: j.key })}
            >
              <span className="job-name">{j.video_name ?? basename(j.video_path)}</span>
              <JobBadge job={j} />
            </button>
          ))}
        </nav>
        <SystemPanel sys={sys} />
      </aside>
      <main className="main">
        {view.kind === "new" ? (
          <NewJob
            key={view.path ?? "blank"}
            sys={sys}
            initialPath={view.path}
            onStarted={(key) => {
              refresh();
              setView({ kind: "job", key });
            }}
          />
        ) : (
          <JobView
            key={view.key}
            jobKey={view.key}
            onChanged={refresh}
            onDeleted={() => {
              refresh();
              setView({ kind: "new" });
            }}
          />
        )}
      </main>
    </div>
  );
}

function JobBadge({ job }: { job: JobSummary }) {
  const live = job.live;
  if (live && (live.status === "running" || live.status === "queued")) {
    return (
      <span className="job-meta">
        <span className="meter" style={{ ["--p" as string]: live.progress }} />
        {live.status === "queued" ? "대기 중" : `${Math.round(live.progress * 100)}%`}
      </span>
    );
  }
  if (live?.status === "error") return <span className="job-meta bad">실패</span>;
  return (
    <span className="job-meta">
      {job.duration ? tc(job.duration, false) : ""}
      {job.translated ? "" : " 번역 전"}
    </span>
  );
}

function SystemPanel({ sys }: { sys: SystemInfo | null }) {
  if (!sys) return null;
  const gpu = sys.gpus[0];
  const model = sys.default_llm ? basename(sys.default_llm) : null;
  const problems: string[] = [];
  if (!sys.ffmpeg) problems.push("FFmpeg를 찾지 못했습니다");
  if (!sys.llama_server.exists) problems.push("llama-server가 없습니다 (scripts/setup_llama.ps1)");
  if (!model) problems.push("번역 모델(GGUF)이 없습니다");
  if (sys.cuda_devices === 0) problems.push("CUDA GPU가 없어 CPU로 처리합니다(느림)");
  return (
    <section className="system" aria-label="시스템 상태">
      {gpu && (
        <p>
          {gpu.name.replace("NVIDIA GeForce ", "")}
          <span className="quiet"> {Math.round(gpu.memory_total_mb / 1024)}GB</span>
        </p>
      )}
      {model && <p className="quiet small" title={sys.default_llm ?? ""}>{model}</p>}
      {problems.map((p) => (
        <p key={p} className="bad small">{p}</p>
      ))}
    </section>
  );
}

function Splash({ text, error }: { text: string; error?: boolean }) {
  return (
    <div className="splash">
      <span className="brand-mark big" aria-hidden>字</span>
      <p className={error ? "bad" : "quiet"} style={{ whiteSpace: "pre-wrap" }}>{text}</p>
    </div>
  );
}
