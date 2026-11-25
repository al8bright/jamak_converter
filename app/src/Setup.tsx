import { useEffect, useMemo, useState } from "react";
import { useApp } from "./ctx";
import { gb, isReady, memoryLabel, memoryNote, type SetupComponent, toDownload, useSetup } from "./setupState";
import { Note, Page } from "./ui";

export function ComponentState({ c }: { c: SetupComponent }) {
  const total = c.total_bytes || c.approx_bytes;
  if (isReady(c)) return <span className="badge ok">준비됨</span>;
  if (c.status === "running") {
    return (
      <>
        <span className="bar" aria-hidden><span style={{ width: `${total ? (c.done_bytes / total) * 100 : 0}%` }} /></span>
        <span className="small muted" role="status">{gb(c.done_bytes)} / {gb(total)}</span>
      </>
    );
  }
  if (c.status === "queued") return <span className="muted">대기 중</span>;
  if (c.status === "error") return <span className="err-fg small" title={c.error ?? ""}>실패: {c.error}</span>;
  if (c.status === "cancelled") return <span className="muted">멈춤</span>;
  return <span className="muted">{gb(total)}</span>;
}

export function Setup({ onDone }: { onDone: (next?: "engine") => void }) {
  const { sys } = useApp();
  const home = sys?.home ?? "jamak 폴더";
  const { status, error, install, cancel } = useSetup();
  const [stt, setStt] = useState<string | null>(null);
  const [local, setLocal] = useState(false);

  const sttModels = useMemo(() => status?.components.filter((c) => c.kind === "stt") ?? [], [status]);
  useEffect(() => {
    if (!status || stt) return;
    setStt(status.ready.stt[0] ?? status.recommended_stt);
    setLocal(status.ready.local_translation);
  }, [status, stt]);

  if (!status || !stt) return <Page eyebrow="SETUP" title="설치 확인"><p className="muted">확인하는 중…</p></Page>;

  const ids = ["ffmpeg", stt, ...(local ? ["llm"] : [])];
  const llm = status.components.find((c) => c.id === "llm");
  const localBytes = (llm?.approx_bytes ?? 0) + (status.components.find((c) => c.id === "llama")?.approx_bytes ?? 0);
  const todo = toDownload(status, ids);
  const remaining = todo.reduce((a, c) => a + Math.max(0, (c.total_bytes || c.approx_bytes) - c.done_bytes), 0);
  const shown = status.running ? status.components.filter((c) => !["present", "missing"].includes(c.status)) : todo;
  const allDone = status.ready.ok && !status.running && todo.length === 0;
  const lowDisk = remaining > status.free_bytes * 0.95;
  const stopped = shown.some((c) => c.status === "error" || c.status === "cancelled");

  return (
    <Page eyebrow="SETUP · 처음 한 번" title={allDone ? "준비가 끝났습니다" : "필요한 파일을 내려받습니다"}>
      <p className="muted" style={{ margin: 0, maxWidth: 720 }}>
        {allDone
          ? status.ready.local_translation
            ? "이제 영상을 넣어 자막을 만들 수 있습니다."
            : "음성 인식은 준비됐습니다. 번역은 클라우드 API 키를 등록하면 바로 쓸 수 있습니다."
          : `음성 인식은 이 PC에서 처리합니다. 고른 항목만 ${home}에 받으며, 나중에 설정 › 모델 관리에서 더 받거나 지울 수 있습니다. 중간에 끊겨도 받은 부분부터 이어서 받습니다.`}
      </p>

      {error && <Note tone="err">{error}</Note>}

      {!status.running && !allDone && (
        <>
          <fieldset className="card" style={{ margin: 0 }}>
            <legend><b>음성 인식 모델</b> <span className="small muted">한 개만 있으면 됩니다 · {memoryNote(status)}</span></legend>
            <div className="stack" style={{ gap: 6 }}>
              {sttModels.map((c) => (
                <label key={c.id} className="check">
                  <input type="radio" name="stt" checked={stt === c.id} onChange={() => setStt(c.id)} />
                  <span className="check-text">
                    <b>
                      {c.name}
                      {c.id === status.recommended_stt && <span className="badge ok" style={{ marginLeft: 6 }}>이 PC에 추천</span>}
                      {isReady(c) && <span className="badge" style={{ marginLeft: 6 }}>받아 둠</span>}
                    </b>
                    <span>{c.summary} · {gb(c.approx_bytes)}{c.vram_gb ? ` · ${memoryLabel(status)} 약 ${c.vram_gb}GB` : ""}</span>
                  </span>
                </label>
              ))}
            </div>
          </fieldset>
          <div className="card" style={{ margin: 0 }}>
          <label className="check">
            <input type="checkbox" checked={local} onChange={(e) => setLocal(e.target.checked)} />
            <span className="check-text">
              <b>로컬 번역도 받기 (선택)</b>
              <span>
                Gemma 4 12B와 llama-server, 약 {gb(localBytes)}. 인터넷 없이 번역하며 {memoryLabel(status)} {llm?.vram_gb ?? 9}GB 이상이 필요합니다.
                받지 않으면 Claude, Gemini 같은 클라우드 번역을 씁니다.
              </span>
            </span>
          </label>
          </div>
        </>
      )}

      {shown.length > 0 && (
        <ul className="setup-list" aria-label="받을 항목">
          {shown.map((c) => (
            <li key={c.id} className={c.status}>
              <div className="setup-name">
                <b style={{ fontWeight: 500 }}>{c.name}</b>
                <small className="muted">{c.note}</small>
              </div>
              <div className="setup-state"><ComponentState c={c} /></div>
            </li>
          ))}
        </ul>
      )}

      {!allDone && !status.running && (
        <p className={`small ${lowDisk ? "err-fg" : "muted"}`} style={{ margin: 0 }}>
          받을 용량 약 {gb(remaining)} · 남은 디스크 {gb(status.free_bytes)}
          {lowDisk && " — 디스크 공간이 모자랍니다. 공간을 비우거나 더 작은 모델을 고르세요."}
        </p>
      )}

      <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 12 }}>
        {allDone ? (
          <>
            {!status.ready.local_translation && <button className="btn primary lg" onClick={() => onDone("engine")}>API 키 등록하기</button>}
            <button className={`btn lg${status.ready.local_translation ? " primary" : ""}`} onClick={() => onDone()}>자막 만들기로 가기</button>
          </>
        ) : status.running ? (
          <button className="btn lg" onClick={cancel}>멈추기</button>
        ) : (
          <button className="btn primary lg" onClick={() => install(ids)} disabled={lowDisk}>
            {stopped ? "이어서 내려받기" : "내려받기 시작"}
          </button>
        )}
        {!allDone && !status.running && (
          <p className="muted small">모든 파일은 각 배포처의 공식 파일을 받아 크기와 해시로 확인합니다.</p>
        )}
      </div>
    </Page>
  );
}
