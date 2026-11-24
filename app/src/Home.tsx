import { useState } from "react";
import { basename, inTauri, type JobSummary, kindBadge, mmss, when } from "./api";
import { isLive, useApp } from "./ctx";
import { Icon, ICONS, KindBadge, Page } from "./ui";

export const VIDEO_EXT = ["mkv", "mp4", "avi", "mov", "webm", "m2ts", "ts", "wmv", "flv"];

export async function pickVideo(): Promise<string | null> {
  if (!inTauri) return null;
  const { open } = await import("@tauri-apps/plugin-dialog");
  const p = await open({ multiple: false, filters: [{ name: "동영상", extensions: VIDEO_EXT }] });
  return typeof p === "string" ? p : null;
}

export function Home() {
  const { profiles, jobs, go, openJob } = useApp();
  const def = profiles.find((p) => p.is_default);
  const [path, setPath] = useState("");

  async function choose() {
    const p = await pickVideo();
    if (p) go({ kind: "options", path: p });
  }

  return (
    <Page eyebrow="HOME" title="새 작업" actions={
      <div className="engine-chip">
        <span>기본 번역 엔진</span>
        <b>{def?.name ?? "—"}</b>
        <button onClick={() => go({ kind: "engine" })}>변경</button>
      </div>
    }>
      <section className="drop" aria-label="영상 선택">
        <div className="icon-tile" aria-hidden><Icon d={ICONS.film} size={22} /></div>
        <h2>영상을 여기로 끌어다 놓으세요</h2>
        <p>{inTauri ? "또는 파일을 직접 고릅니다." : "브라우저에서는 영상 경로를 직접 입력합니다."} mkv · mp4 · avi · webm</p>
        {inTauri ? (
          <button className="btn primary lg" onClick={choose}>
            <Icon d={ICONS.folder} size={15} width={2.2} />
            파일 선택
          </button>
        ) : (
          <form className="path-form" onSubmit={(e) => {
            e.preventDefault();
            const v = path.trim().replace(/^"|"$/g, "");
            if (v) go({ kind: "options", path: v });
          }}>
            <label htmlFor="video-path" className="sr-only">영상 경로</label>
            <input id="video-path" className="input mono" value={path} onChange={(e) => setPath(e.target.value)}
              placeholder="C:\Videos\ep01.mkv" autoComplete="off" />
            <button className="btn primary" disabled={!path.trim()}>열기</button>
          </form>
        )}
        <p className="lock-note">
          <Icon d={ICONS.lock} size={12} />
          영상은 업로드되지 않습니다. 이 PC에서 바로 읽습니다.
        </p>
      </section>

      <section aria-labelledby="recent-h" className="stack" style={{ gap: 8 }}>
        <div className="card-head">
          <h2 id="recent-h" style={{ margin: 0, fontSize: 14 }}>최근 작업</h2>
          <span className="small muted">작업 캐시가 남아 있으면 바뀐 단계만 다시 실행합니다</span>
        </div>
        {jobs.length === 0 ? (
          <div className="table-wrap"><p className="empty">만든 자막이 여기에 쌓입니다. 위에서 영상을 골라 시작하세요.</p></div>
        ) : (
          <div className="table-wrap">
            <table className="t" style={{ minWidth: 680 }}>
              <thead>
                <tr><th scope="col">파일</th><th scope="col">길이</th><th scope="col">번역 엔진</th><th scope="col">상태</th><th scope="col">마지막 작업</th></tr>
              </thead>
              <tbody>
                {jobs.slice(0, 30).map((j) => <JobRow key={j.key} job={j} open={() => openJob(j.key)} />)}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </Page>
  );
}

function JobRow({ job, open }: { job: JobSummary; open: () => void }) {
  const eng = job.live?.engine ?? job.engine;
  const kb = eng?.kind ? kindBadge(eng.kind) : null;
  return (
    <tr>
      <td><button className="row-link" onClick={open}>{job.video_name ?? basename(job.video_path)}</button></td>
      <td className="mono" style={{ fontSize: 12 }}>{job.duration ? mmssLong(job.duration) : "—"}</td>
      <td>
        {kb && eng?.profile_name ? (
          <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
            <KindBadge cloud={kb.cloud} text={kb.text} />{eng.profile_name}
          </span>
        ) : <span className="muted">—</span>}
      </td>
      <td><StatusBadge job={job} /></td>
      <td className="muted">{when(job.live?.finished_at ?? job.updated ?? job.live?.queued_at)}</td>
    </tr>
  );
}

function StatusBadge({ job }: { job: JobSummary }) {
  const live = job.live;
  if (live && isLive(job)) {
    return <span className="badge pill cloud">{live.status === "queued" ? "대기 중" : `진행 중 · ${Math.round(live.progress * 100)}%`}</span>;
  }
  if (live?.status === "error") return <span className="badge pill err" title={live.error ?? ""}>실패 · {shortError(live.error)}</span>;
  if (live?.status === "cancelled") return <span className="badge pill">취소됨</span>;
  if (job.translated) return <span className="badge pill ok">완료</span>;
  return <span className="badge pill">번역 전</span>;
}

function shortError(e: string | null): string {
  if (!e) return "오류";
  const msg = e.replace(/^\w+(Error|Exception)?: /, "");
  return msg.length > 28 ? `${msg.slice(0, 28)}…` : msg;
}

function mmssLong(sec: number): string {
  if (sec >= 3600) {
    const h = Math.floor(sec / 3600);
    const rest = mmss(sec - h * 3600).padStart(5, "0");
    return `${h}:${rest}`;
  }
  return mmss(sec);
}
