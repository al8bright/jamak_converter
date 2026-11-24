import { useCallback, useEffect, useState } from "react";
import { api, type Check, clock } from "./api";
import { useApp } from "./ctx";
import { type PaletteName, PALETTES } from "./theme";
import { Page, SettingsTabs } from "./ui";

const STATE_LABEL: Record<Check["state"], string> = { ok: "정상", warn: "주의", err: "오류", off: "미사용" };

export function System() {
  const { go, ui, saveUi, refreshSys } = useApp();
  const [checks, setChecks] = useState<Check[] | null>(null);
  const [at, setAt] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setBusy(true);
    try {
      const r = await api.get<{ checked_at: string; checks: Check[] }>("/system/checks");
      setChecks(r.checks);
      setAt(r.checked_at);
      refreshSys();
    } finally {
      setBusy(false);
    }
  }, [refreshSys]);
  useEffect(() => { load(); }, [load]);

  const count = (s: Check["state"]) => checks?.filter((c) => c.state === s).length ?? 0;

  return (
    <Page eyebrow="SETTINGS" title="시스템 상태" wide actions={<>
      <SettingsTabs current="system" go={(v) => go({ kind: v })} />
      <button className="btn" onClick={load} disabled={busy}>{busy ? "확인하는 중…" : "다시 확인"}</button>
    </>}>
      <section className="card summary" aria-label="점검 요약">
        <b>점검 결과</b>
        <span className="badges">
          <span className="badge pill ok">정상 {count("ok")}</span>
          <span className="badge pill warn">주의 {count("warn")}</span>
          <span className="badge pill err">오류 {count("err")}</span>
        </span>
        <span className="end">마지막 확인 {clock(at)} · 실시간 사용량은 아래 모니터에 표시됩니다</span>
      </section>

      {!checks ? <p className="muted">확인하는 중…</p> : (
        <ul className="checks">
          {checks.map((c, i) => (
            <li key={i} className={c.state}>
              <span className="check-top">
                <span>{c.area}</span>
                <span className={`badge ${c.state === "off" ? "" : c.state}`}>{STATE_LABEL[c.state]}</span>
              </span>
              <b>{c.title}</b>
              <span className="check-detail">{c.detail}</span>
              {c.bar !== null && (
                <span className="bar" aria-hidden>
                  <span style={{ width: `${Math.min(100, c.bar * 100)}%`, background: c.bar >= 0.85 ? "var(--warn)" : undefined }} />
                </span>
              )}
              {(c.fix || c.action) && (
                <span className="check-fix">
                  {c.fix && <span>{c.fix}</span>}
                  {c.action === "setup" && <button className="btn sm" onClick={() => go({ kind: "setup" })}>설치 화면 열기</button>}
                  {c.action === "engine" && <button className="btn sm" onClick={() => go({ kind: "engine" })}>번역 엔진 설정</button>}
                </span>
              )}
            </li>
          ))}
        </ul>
      )}

      <section className="card" aria-labelledby="pal-h">
        <div className="card-head">
          <h2 id="pal-h">색 테마</h2>
          <span className="small muted">앱 전체의 강조색을 바꿉니다</span>
        </div>
        <div className="swatches" role="group" aria-labelledby="pal-h">
          {(Object.keys(PALETTES) as PaletteName[]).map((k) => (
            <button key={k} className="swatch" aria-pressed={ui.palette === k} onClick={() => saveUi({ palette: k })}>
              <i aria-hidden>
                <span style={{ background: PALETTES[k].bg }} />
                <span style={{ background: PALETTES[k].p }} />
                <span style={{ background: PALETTES[k].s }} />
              </i>
              {PALETTES[k].label}
            </button>
          ))}
        </div>
      </section>
    </Page>
  );
}
