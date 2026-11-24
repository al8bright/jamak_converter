import { useCallback, useEffect, useState } from "react";
import { api } from "./api";

export interface SetupComponent {
  id: string;
  name: string;
  note: string;
  approx_bytes: number;
  status: "present" | "missing" | "queued" | "running" | "done" | "error" | "cancelled";
  done_bytes: number;
  total_bytes: number;
  message: string;
  error: string | null;
}

export interface SetupStatus {
  running: boolean;
  components: SetupComponent[];
}

export function setupNeeded(s: SetupStatus | null): boolean {
  return !!s && (s.running || s.components.some((c) => c.status !== "present" && c.status !== "done"));
}

function gb(bytes: number): string {
  return bytes >= 1e9 ? `${(bytes / 1e9).toFixed(1)}GB` : `${Math.max(1, Math.round(bytes / 1e6))}MB`;
}

export function Setup({ home, onDone }: { home: string; onDone: () => void }) {
  const [status, setStatus] = useState<SetupStatus | null>(null);
  const [error, setError] = useState<string | null>(null);

  const poll = useCallback(async () => {
    try {
      setStatus(await api.get<SetupStatus>("/setup"));
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  useEffect(() => {
    poll();
  }, [poll]);

  useEffect(() => {
    if (!status?.running) return;
    const t = setInterval(poll, 700);
    return () => clearInterval(t);
  }, [status?.running, poll]);

  if (!status) return <section className="page"><p className="quiet">확인하는 중…</p></section>;

  const missing = status.components.filter((c) => c.status !== "present" && c.status !== "done");
  const remaining = missing.reduce((a, c) => a + (c.total_bytes || c.approx_bytes) - c.done_bytes, 0);
  const allDone = missing.length === 0;

  async function start() {
    setError(null);
    try {
      await api.post("/setup/install", {});
      poll();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  return (
    <section className="page">
      <header className="page-head setup-head">
        <h1>{allDone ? "준비가 끝났습니다" : "처음 한 번, 필요한 파일을 내려받습니다"}</h1>
        <p className="quiet">
          {allDone
            ? "이제 영상을 넣어 자막을 만들 수 있습니다."
            : `음성 인식과 번역은 모두 이 PC에서 처리하므로 엔진과 모델 파일이 필요합니다. 남은 용량은 약 ${gb(remaining)}이며 ${home}에 저장합니다. 중간에 끊겨도 받은 부분부터 이어서 받습니다.`}
        </p>
      </header>

      {error && <p className="notice bad" role="alert">{error}</p>}

      <ul className="setup-list">
        {status.components.map((c) => {
          const total = c.total_bytes || c.approx_bytes;
          const ready = c.status === "present" || c.status === "done";
          const p = ready ? 1 : total ? c.done_bytes / total : 0;
          return (
            <li key={c.id} className={`setup-item ${c.status}`}>
              <div className="setup-name">
                <span>{c.name}</span>
                <small className="quiet">{c.note}</small>
              </div>
              <div className="setup-state">
                {ready ? (
                  <span className="ok">준비됨</span>
                ) : c.status === "running" ? (
                  <>
                    <span className="setup-bar"><span style={{ transform: `scaleX(${p})` }} /></span>
                    <span className="small quiet">{gb(c.done_bytes)} / {gb(total)}</span>
                  </>
                ) : c.status === "queued" ? (
                  <span className="quiet">대기 중</span>
                ) : c.status === "error" ? (
                  <span className="bad small" title={c.error ?? ""}>실패: {c.error}</span>
                ) : c.status === "cancelled" ? (
                  <span className="quiet">멈춤</span>
                ) : (
                  <span className="quiet">{gb(total)}</span>
                )}
              </div>
            </li>
          );
        })}
      </ul>

      <div className="actions">
        {allDone ? (
          <button className="btn primary big" onClick={onDone}>자막 만들기로 가기</button>
        ) : status.running ? (
          <button className="btn big" onClick={() => api.post("/setup/cancel").then(poll)}>멈추기</button>
        ) : (
          <button className="btn primary big" onClick={start}>
            {missing.some((c) => c.status === "error" || c.status === "cancelled") ? "이어서 내려받기" : "내려받기 시작"}
          </button>
        )}
        {!allDone && !status.running && (
          <p className="quiet small">FFmpeg, CUDA 라이브러리, llama.cpp는 각 배포처의 공식 파일을 받아 SHA-256으로 확인합니다.</p>
        )}
      </div>
    </section>
  );
}
