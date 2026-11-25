import { useState } from "react";
import { api } from "./api";
import { useApp } from "./ctx";
import { ComponentState } from "./Setup";
import { gb, isReady, type SetupComponent, toDownload, useSetup } from "./setupState";
import { Modal, Note, Page, SettingsTabs } from "./ui";

const GROUPS: { kind: SetupComponent["kind"][]; title: string; note: string }[] = [
  { kind: ["stt"], title: "음성 인식", note: "작업마다 고를 수 있습니다. 한 개는 꼭 있어야 합니다." },
  { kind: ["llm"], title: "로컬 번역", note: "받은 모델은 설정 › 번역 엔진의 로컬 프로필에서 고릅니다. 클라우드 번역만 쓴다면 받지 않아도 됩니다." },
  { kind: ["runtime", "tool"], title: "실행 환경과 도구", note: "모델을 받으면 필요한 것이 같이 받아집니다." },
];

export function Models() {
  const { go, refreshSys, refreshProfiles } = useApp();
  const { status, error, setError, poll, install, cancel } = useSetup(true);
  const [confirm, setConfirm] = useState<SetupComponent | null>(null);
  const [plan, setPlan] = useState<SetupComponent | null>(null);

  if (!status) {
    return <Page eyebrow="SETTINGS" title="모델 관리" wide><p className="muted">확인하는 중…</p></Page>;
  }
  const by = new Map(status.components.map((c) => [c.id, c]));
  const used = status.components.reduce((a, c) => a + (c.disk_bytes ?? 0), 0);
  const nameOf = (id: string) => by.get(id)?.name ?? id;
  const lastStt = status.ready.stt.length === 1;

  async function remove(c: SetupComponent) {
    setConfirm(null);
    try {
      await api.post("/setup/remove", { id: c.id });
    } catch (e) {
      setError((e as Error).message);
    }
    await poll();
    refreshSys();
    refreshProfiles();
  }

  async function start(c: SetupComponent) {
    setPlan(null);
    await install([c.id]);
    refreshSys();
  }

  const pending = plan ? toDownload(status, [plan.id]) : [];
  const pendingBytes = pending.reduce((a, c) => a + c.approx_bytes, 0);

  return (
    <Page eyebrow="SETTINGS" title="모델 관리" wide actions={<>
      <SettingsTabs current="models" go={(v) => go({ kind: v })} />
      {status.running && <button className="btn" onClick={cancel}>받기 멈추기</button>}
    </>}>
      <p className="muted" style={{ margin: 0, maxWidth: 760 }}>
        설치 파일에는 모델이 들어 있지 않습니다. 쓸 것만 받으세요. 지금 {gb(used)}를 쓰고 있고, 디스크에 {gb(status.free_bytes)} 남았습니다.
      </p>
      {error && <Note tone="err">{error}</Note>}

      {GROUPS.map((g) => (
        <section key={g.title} className="card" aria-labelledby={`mg-${g.kind[0]}`}>
          <h2 id={`mg-${g.kind[0]}`} style={{ marginBottom: 2 }}>{g.title}</h2>
          <p className="small muted" style={{ marginTop: 0 }}>{g.note}</p>
          <ul className="setup-list">
            {status.components.filter((c) => g.kind.includes(c.kind)).map((c) => {
              const ready = isReady(c);
              const busy = c.status === "running" || c.status === "queued";
              const blocker = c.used_by.length ? `${c.used_by.map(nameOf).join(", ")}이(가) 씁니다` :
                c.kind === "stt" && lastStt && ready ? "음성 인식 모델이 하나는 있어야 합니다" :
                !c.removable ? "지울 수 없는 항목입니다" : null;
              return (
                <li key={c.id} className={c.status}>
                  <div className="setup-name" style={{ flex: "1 1 360px" }}>
                    <b style={{ fontWeight: 500 }}>
                      {c.name}
                      {c.id === status.recommended_stt && <span className="badge ok" style={{ marginLeft: 6 }}>이 PC에 추천</span>}
                      {c.tags.map((t) => <span key={t} className="badge" style={{ marginLeft: 6 }}>{t}</span>)}
                    </b>
                    {c.summary && <small>{c.summary}</small>}
                    <small className="muted">
                      {ready && c.disk_bytes ? `${gb(c.disk_bytes)} 사용` : `약 ${gb(c.approx_bytes)}`}
                      {c.vram_gb ? ` · GPU 메모리 약 ${c.vram_gb}GB` : ""}
                      {c.requires.length ? ` · 필요: ${c.requires.map(nameOf).join(", ")}` : ""}
                      {c.license ? ` · ${c.license}` : ""}
                    </small>
                    {c.vram_gb && status.vram_gb !== null && c.vram_gb > status.vram_gb && (
                      <small className="warn-fg">이 PC의 GPU 메모리({status.vram_gb}GB)보다 많이 씁니다. 느리거나 실패할 수 있습니다.</small>
                    )}
                  </div>
                  <div className="setup-state">
                    {(busy || c.status === "error" || c.status === "cancelled") && <ComponentState c={c} />}
                    {ready && !busy && <span className="badge ok">받아 둠</span>}
                    {ready && !busy && c.removable && (
                      <button className="btn sm bare danger" disabled={status.running || !!blocker} title={blocker ?? undefined}
                        onClick={() => setConfirm(c)}>지우기</button>
                    )}
                    {!ready && !busy && (
                      <button className="btn sm" disabled={status.running} onClick={() => setPlan(c)}>
                        {c.status === "error" || c.status === "cancelled" ? "이어서 받기" : `받기 · ${gb(c.approx_bytes)}`}
                      </button>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
        </section>
      ))}

      {plan && (
        <Modal title={`${plan.name} 받기`} onClose={() => setPlan(null)} actions={<>
          <button className="btn" onClick={() => setPlan(null)}>취소</button>
          <button className="btn primary" disabled={pendingBytes > status.free_bytes} onClick={() => start(plan)}>받기</button>
        </>}>
          <p style={{ marginTop: 0 }}>다음 항목을 받습니다. 약 {gb(pendingBytes)}, 디스크에 {gb(status.free_bytes)} 남았습니다.</p>
          <ul className="small" style={{ margin: 0, paddingLeft: 18 }}>
            {pending.map((c) => <li key={c.id}>{c.name} · 약 {gb(c.approx_bytes)}</li>)}
          </ul>
          {pendingBytes > status.free_bytes && <p className="small err-fg" role="alert">디스크 공간이 모자랍니다.</p>}
        </Modal>
      )}
      {confirm && (
        <Modal title={`${confirm.name}을(를) 지울까요?`} onClose={() => setConfirm(null)} actions={<>
          <button className="btn" onClick={() => setConfirm(null)}>취소</button>
          <button className="btn danger" onClick={() => remove(confirm)}>지우기</button>
        </>}>
          <p style={{ margin: 0 }}>
            {confirm.disk_bytes ? `${gb(confirm.disk_bytes)}를 비웁니다. ` : ""}필요하면 여기서 다시 받을 수 있습니다.
            이미 만든 자막과 작업 기록은 지워지지 않습니다.
          </p>
        </Modal>
      )}
    </Page>
  );
}
