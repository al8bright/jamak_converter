import { useCallback, useEffect, useMemo, useState } from "react";
import {
  api, connect, inTauri, type JobSummary, type Profile, type SystemInfo, type SystemUsage, type UiSettings,
} from "./api";
import { type AppState, Ctx, isLive, useApp, type View } from "./ctx";
import { Home } from "./Home";
import { Options } from "./Options";
import { Progress } from "./Progress";
import { Review } from "./Review";
import { Settings } from "./Settings";
import { Setup, setupNeeded, type SetupStatus } from "./Setup";
import { System } from "./System";
import { applyPalette } from "./theme";
import { Icon, ICONS, Logo } from "./ui";

const DEFAULT_UI: UiSettings = { palette: "seoul", sidebar: "open", cloud_notice_seen: false };
applyPalette("seoul");

export default function App() {
  const [ready, setReady] = useState<"wait" | "ok" | string>("wait");
  const [sys, setSys] = useState<SystemInfo | null>(null);
  const [jobs, setJobs] = useState<JobSummary[]>([]);
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [ui, setUi] = useState<UiSettings>(DEFAULT_UI);
  const [view, setView] = useState<View>({ kind: "home" });
  const [currentKey, setCurrentKey] = useState<string | null>(null);

  const refreshJobs = useCallback(async () => setJobs(await api.get<JobSummary[]>("/jobs")), []);
  const refreshProfiles = useCallback(async () => setProfiles(await api.get<Profile[]>("/settings/translation-profiles")), []);
  const refreshSys = useCallback(async () => setSys(await api.get<SystemInfo>("/system")), []);

  useEffect(() => {
    connect()
      .then(async () => {
        const u = await api.get<UiSettings>("/settings/ui");
        setUi(u);
        applyPalette(u.palette);
        setReady("ok");
        const s = await api.get<SetupStatus>("/setup");
        if (setupNeeded(s)) setView({ kind: "setup" });
        await Promise.all([refreshSys(), refreshJobs(), refreshProfiles()]);
      })
      .catch((e) => setReady(String(e?.message ?? e)));
  }, [refreshJobs, refreshProfiles, refreshSys]);

  // files dropped anywhere on the window open the options screen
  useEffect(() => {
    if (!inTauri) return;
    let unlisten: (() => void) | undefined;
    import("@tauri-apps/api/webview").then(({ getCurrentWebview }) =>
      getCurrentWebview()
        .onDragDropEvent((e) => {
          if (e.payload.type === "drop" && e.payload.paths.length) setView({ kind: "options", path: e.payload.paths[0] });
        })
        .then((u) => (unlisten = u)),
    );
    return () => unlisten?.();
  }, []);

  // keep the job list fresh while something runs
  const anyLive = jobs.some(isLive);
  useEffect(() => {
    if (ready !== "ok") return;
    const t = setInterval(refreshJobs, anyLive ? 1500 : 8000);
    return () => clearInterval(t);
  }, [ready, anyLive, refreshJobs]);

  const saveUi = useCallback(async (patch: Partial<UiSettings>) => {
    const next = { ...ui, ...patch };
    setUi(next);
    if (patch.palette) applyPalette(patch.palette);
    await api.put("/settings/ui", next);
  }, [ui]);

  const openJob = useCallback((key: string) => {
    setCurrentKey(key);
    const j = jobs.find((x) => x.key === key);
    setView(j && isLive(j) ? { kind: "progress", key } : { kind: "review", key });
  }, [jobs]);

  const go = useCallback((v: View) => {
    if ((v.kind === "progress" || v.kind === "review") && v.key) setCurrentKey(v.key);
    setView(v);
  }, []);

  const state: AppState = useMemo(() => ({
    sys, jobs, profiles, ui, go, openJob, refreshJobs, refreshProfiles, refreshSys, saveUi,
  }), [sys, jobs, profiles, ui, go, openJob, refreshJobs, refreshProfiles, refreshSys, saveUi]);

  if (ready === "wait") return <Splash text="백엔드를 시작하는 중" />;
  if (ready !== "ok") return <Splash text={ready} error />;

  const running = jobs.find(isLive);
  const progressKey = view.kind === "progress" && view.key ? view.key : running?.key ?? currentKey ?? undefined;
  const reviewKey = view.kind === "review" && view.key ? view.key
    : (currentKey && jobs.find((j) => j.key === currentKey && j.translated)?.key) ?? jobs.find((j) => j.translated)?.key;

  return (
    <Ctx.Provider value={state}>
      <div className="shell">
        <Sidebar view={view} running={running} go={go} progressKey={progressKey} reviewKey={reviewKey} />
        <div className="col">
          {view.kind === "home" && <Home />}
          {view.kind === "options" && <Options key={view.path} path={view.path} />}
          {view.kind === "progress" && <Progress key={progressKey ?? "none"} jobKey={progressKey} />}
          {view.kind === "review" && <Review key={reviewKey ?? "none"} jobKey={reviewKey} />}
          {view.kind === "engine" && <Settings />}
          {view.kind === "system" && <System />}
          {view.kind === "setup" && (
            <Setup onDone={async () => { await refreshSys(); await refreshProfiles(); setView({ kind: "home" }); }} />
          )}
          <Monitor running={running} />
        </div>
      </div>
    </Ctx.Provider>
  );
}

// ---------------------------------------------------------------------- sidebar

function Sidebar({ view, running, go, progressKey, reviewKey }: {
  view: View; running?: JobSummary; go: (v: View) => void; progressKey?: string; reviewKey?: string;
}) {
  const { ui, saveUi } = useApp();
  const shut = ui.sidebar === "hidden";
  const active = view.kind === "options" || view.kind === "setup" ? (view.kind === "setup" ? "system" : "home") : view.kind;
  const item = (id: string, label: string, short: string, d: string, onClick: () => void, badge?: string) => (
    <button key={id} className="nav-item" aria-current={active === id ? "page" : undefined} title={label} onClick={onClick}>
      <Icon d={d} size={shut ? 18 : 16} />
      <span>{shut ? short : label}</span>
      {badge && <span className="nav-badge">{badge}</span>}
    </button>
  );
  const pct = running?.live ? (running.live.status === "queued" ? "대기" : `${Math.round(running.live.progress * 100)}%`) : undefined;
  return (
    <nav className={`side${shut ? " shut" : ""}`} aria-label="주 메뉴">
      <div className="side-top">
        <Logo />
        {!shut && (
          <div className="brand-name">
            <b>JAMAK</b>
            <span><span lang="ja">字幕</span> → 자막</span>
          </div>
        )}
        <button className="side-toggle" onClick={() => saveUi({ sidebar: shut ? "open" : "hidden" })} aria-label={shut ? "사이드바 펼치기" : "사이드바 접기"}
          aria-expanded={!shut}>
          <svg aria-hidden width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
            strokeLinecap="round" strokeLinejoin="round">
            <path d="M4 5h16v14H4zM9 5v14" /><path d={shut ? "M13 10l2 2-2 2" : "M16 10l-2 2 2 2"} />
          </svg>
        </button>
      </div>
      <div className="side-group">
        <span>작업</span>
        {item("home", "홈", "홈", ICONS.home, () => go({ kind: "home" }))}
        {item("progress", "진행 중인 작업", "진행", ICONS.progress, () => go({ kind: "progress", key: progressKey }), pct)}
        {item("review", "검수·편집", "검수·편집", ICONS.review, () => go({ kind: "review", key: reviewKey }))}
      </div>
      <div className="side-group">
        <span>설정</span>
        {item("engine", "번역 엔진", "번역 엔진", ICONS.engine, () => go({ kind: "engine" }))}
        {item("system", "시스템 상태", "시스템", ICONS.system, () => go({ kind: "system" }))}
      </div>
      <span className="side-ver">V0.1.0</span>
    </nav>
  );
}

// ---------------------------------------------------------------------- footer monitor

function Monitor({ running }: { running?: JobSummary }) {
  const [u, setU] = useState<SystemUsage | null>(null);
  useEffect(() => {
    let alive = true;
    const tick = () => api.get<SystemUsage>("/system/usage").then((x) => alive && setU(x)).catch(() => {});
    tick();
    const t = setInterval(tick, 2000);
    return () => { alive = false; clearInterval(t); };
  }, []);

  const live = running?.live;
  let svc = "백엔드 연결됨 · 대기 중";
  let svcColor = "var(--ok)";
  if (live) {
    const eng = live.engine;
    if (live.status === "queued") svc = "작업 대기 중";
    else if (live.stage === "translate" && eng?.cloud) {
      svc = `${eng.kind === "anthropic" ? "Claude" : "Gemini"} API · 번역 중`;
      svcColor = "var(--s)";
    } else if (live.stage === "translate" && eng?.kind === "managed") {
      const port = /:(\d+)/.exec(live.message)?.[1];
      svc = `llama-server 실행 중${port ? ` · :${port}` : ""}`;
    } else svc = `처리 중 · ${Math.round(live.progress * 100)}%`;
  }

  const meters: { label: string; pct: number; text: string; w: string; color: string }[] = [];
  if (u) {
    if (u.gpu) {
      const vp = (u.gpu.vram_used_gb / u.gpu.vram_total_gb) * 100;
      meters.push({ label: "GPU", pct: u.gpu.util, text: `${u.gpu.util.toFixed(0)}%`, w: "4ch", color: "var(--p)" });
      meters.push({ label: "VRAM", pct: vp, text: `${u.gpu.vram_used_gb.toFixed(1)} / ${u.gpu.vram_total_gb.toFixed(0)} GB`, w: "12ch",
        color: vp >= 85 ? "var(--warn)" : "var(--s)" });
    }
    const rp = (u.ram_used_gb / u.ram_total_gb) * 100;
    meters.push({ label: "CPU", pct: u.cpu, text: `${u.cpu.toFixed(0)}%`, w: "4ch", color: "var(--p)" });
    meters.push({ label: "RAM", pct: rp, text: `${u.ram_used_gb.toFixed(1)} / ${u.ram_total_gb.toFixed(1)} GB`, w: "14ch",
      color: rp >= 85 ? "var(--warn)" : "var(--s)" });
  }
  return (
    <footer className="monitor" aria-label="시스템 모니터">
      {meters.map((m) => (
        <span className="meter" key={m.label}>
          <b>{m.label}</b>
          <span className="track" aria-hidden><span style={{ width: `${Math.min(100, m.pct)}%`, background: m.color, boxShadow: `0 0 6px ${m.color}` }} /></span>
          <span className="val" style={{ minWidth: m.w }}>{m.text}</span>
        </span>
      ))}
      <span className="monitor-right">
        <span className="svc">
          <span className="d pulse" aria-hidden style={{ background: svcColor, boxShadow: `0 0 6px ${svcColor}` }} />
          {svc}
        </span>
        {u && <span>{[u.gpu?.name, u.cpu_name].filter(Boolean).join(" · ")}</span>}
      </span>
    </footer>
  );
}

function Splash({ text, error }: { text: string; error?: boolean }) {
  return (
    <div className="splash">
      <Logo big />
      <p className={error ? "err-fg" : "muted"}>{text}</p>
    </div>
  );
}
