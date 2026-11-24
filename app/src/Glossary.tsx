import { useEffect, useMemo, useState } from "react";
import { api, type Glossary as G, type GlossaryEntry, type GlossarySummary, inTauri, when } from "./api";
import { useApp } from "./ctx";
import { parseGlossaryText } from "./glossaryFormat";
import { Icon, ICONS, Modal, Note, Page, SettingsTabs, useToast } from "./ui";

const MAX_HINTS = 40;
const BLANK: GlossaryEntry = { ja: "", ko: "", note: "", hint: false };

type Ask = { kind: "new" | "rename" | "duplicate"; value: string } | { kind: "delete" } | { kind: "discard"; then: () => void } | null;

export function GlossaryView({ initial }: { initial?: string }) {
  const { go } = useApp();
  const [list, setList] = useState<GlossarySummary[]>([]);
  const [current, setCurrent] = useState<G | null>(null);
  const [rows, setRows] = useState<GlossaryEntry[]>([]);
  const [aliases, setAliases] = useState("");
  const [dirty, setDirty] = useState(false);
  const [query, setQuery] = useState("");
  const [paste, setPaste] = useState<string | null>(null);
  const [ask, setAsk] = useState<Ask>(null);
  const [error, setError] = useState<string | null>(null);
  const [toast, flash] = useToast();

  const reload = async () => {
    const l = await api.get<GlossarySummary[]>("/glossaries");
    setList(l);
    return l;
  };

  async function open(name: string) {
    const g = await api.get<G>(`/glossaries/${encodeURIComponent(name)}`);
    setCurrent(g);
    setRows(g.entries.map((e) => ({ ...e })));
    setAliases(g.aliases.join(", "));
    setDirty(false);
    setQuery("");
    setPaste(null);
    setError(null);
  }

  useEffect(() => {
    reload().then((l) => {
      const first = (initial && l.find((g) => g.name === initial)) || l[0];
      if (first) open(first.name);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const guard = (then: () => void) => (dirty ? setAsk({ kind: "discard", then }) : then());

  const dupes = useMemo(() => {
    const seen = new Map<string, number>();
    for (const r of rows) if (r.ja.trim()) seen.set(r.ja.trim(), (seen.get(r.ja.trim()) ?? 0) + 1);
    return new Set([...seen].filter(([, n]) => n > 1).map(([k]) => k));
  }, [rows]);
  const hints = rows.filter((r) => r.hint && r.ja.trim()).length;
  const q = query.trim().toLowerCase();
  const shown = rows.map((r, i) => ({ r, i })).filter(({ r }) =>
    !q || r.ja.toLowerCase().includes(q) || r.ko.toLowerCase().includes(q) || r.note.toLowerCase().includes(q));

  function edit(i: number, patch: Partial<GlossaryEntry>) {
    setRows((rs) => rs.map((r, j) => (j === i ? { ...r, ...patch } : r)));
    setDirty(true);
  }

  async function save() {
    if (!current) return;
    if (dupes.size) { setError(`같은 일본어가 두 번 있습니다: ${[...dupes].join(", ")}. 하나만 남기세요.`); return; }
    const entries = rows.filter((r) => r.ja.trim() && r.ko.trim());
    const g = await api.put<G>(`/glossaries/${encodeURIComponent(current.name)}`, {
      entries, aliases: aliases.split(/[,\n]/).map((a) => a.trim()).filter(Boolean),
    });
    setCurrent(g);
    setRows(g.entries.map((e) => ({ ...e })));
    setDirty(false);
    setError(null);
    await reload();
    flash(`"${g.name}"을(를) 저장했습니다.`);
  }

  async function submitName(kind: "new" | "rename" | "duplicate", value: string) {
    const name = value.trim();
    try {
      if (kind === "new") await api.put(`/glossaries/${encodeURIComponent(name)}`, { entries: [], aliases: [] });
      else if (kind === "rename" && current) await api.post(`/glossaries/${encodeURIComponent(current.name)}/rename`, { name });
      else if (current) await api.post(`/glossaries/${encodeURIComponent(current.name)}/duplicate`, { name });
      setAsk(null);
      await reload();
      await open(name);
      flash(kind === "new" ? `"${name}"을(를) 만들었습니다.` : kind === "rename" ? "이름을 바꿨습니다." : `"${name}"(으)로 복제했습니다.`);
    } catch (e) {
      flash((e as Error).message, true);
    }
  }

  async function remove() {
    if (!current) return;
    await api.del(`/glossaries/${encodeURIComponent(current.name)}`);
    setAsk(null);
    setCurrent(null);
    setRows([]);
    setDirty(false);
    const l = await reload();
    if (l[0]) await open(l[0].name);
    flash("용어집을 지웠습니다.");
  }

  async function importFile() {
    if (!inTauri) return;
    const { open: pick } = await import("@tauri-apps/plugin-dialog");
    const p = await pick({ multiple: false, filters: [{ name: "용어집", extensions: ["txt", "csv", "json"] }] });
    if (typeof p !== "string") return;
    try {
      const r = await api.post<{ name: string; count: number }>("/glossaries/import", { path: p });
      await reload();
      await open(r.name);
      flash(`"${r.name}"으로 ${r.count}개 항목을 가져왔습니다.`);
    } catch (e) {
      flash((e as Error).message, true);
    }
  }

  async function exportFile() {
    if (!inTauri || !current) return;
    const { save: pick } = await import("@tauri-apps/plugin-dialog");
    const p = await pick({ defaultPath: `${current.name}.txt`, filters: [
      { name: "텍스트 (일본어=한국어)", extensions: ["txt"] }, { name: "CSV (엑셀)", extensions: ["csv"] }, { name: "JSON", extensions: ["json"] },
    ] });
    if (!p) return;
    try {
      await api.post(`/glossaries/${encodeURIComponent(current.name)}/export`, { path: p });
      flash("내보냈습니다.");
    } catch (e) {
      flash((e as Error).message, true);
    }
  }

  function addPasted() {
    const add = parseGlossaryText(paste ?? "");
    if (!add.length) { flash("읽을 수 있는 줄이 없습니다. 한 줄에 일본어=한국어로 적어 주세요.", true); return; }
    setRows((rs) => {
      const by = new Map(rs.map((r, i) => [r.ja.trim(), i]));
      const next = [...rs];
      for (const e of add) {
        const i = by.get(e.ja);
        if (i !== undefined) next[i] = { ...next[i], ko: e.ko, note: e.note || next[i].note, hint: e.hint || next[i].hint };
        else next.push(e);
      }
      return next;
    });
    setDirty(true);
    setPaste(null);
    flash(`${add.length}개 항목을 넣었습니다. 저장을 눌러야 반영됩니다.`);
  }

  return (
    <Page eyebrow="SETTINGS" title="용어집" wide actions={<>
      <SettingsTabs current="glossary" go={(v) => guard(() => go({ kind: v }))} />
      {inTauri && <button className="btn" onClick={() => guard(importFile)}>가져오기</button>}
      <button className="btn cyan" onClick={() => guard(() => setAsk({ kind: "new", value: "" }))}>+ 용어집 만들기</button>
    </>}>
      <div className="cols">
        <section className="card" aria-labelledby="gl-h" style={{ flex: "0 1 280px" }}>
          <div className="card-head"><h2 id="gl-h">용어집</h2><span className="small muted">{list.length}개</span></div>
          {!list.length && (
            <p className="empty">
              <b>아직 용어집이 없습니다</b>
              작품마다 하나씩 만들어 두면 작업 옵션에서 골라 쓸 수 있습니다.
            </p>
          )}
          <ul className="gl-list">
            {list.map((g) => (
              <li key={g.name}>
                <button aria-current={current?.name === g.name ? "true" : undefined} onClick={() => guard(() => open(g.name))}>
                  <b>{g.name}</b>
                  <span className="small muted">항목 {g.count}개{g.hints ? ` · 인식 힌트 ${g.hints}` : ""} · {when(g.updated)}</span>
                </button>
              </li>
            ))}
          </ul>
        </section>

        {current && (
          <section className="card" aria-labelledby="ge-h" style={{ flex: "1 1 560px" }}>
            <div className="card-head">
              <h2 id="ge-h">{current.name}</h2>
              <span style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
                <button className="btn sm" onClick={() => setAsk({ kind: "rename", value: current.name })}>이름 바꾸기</button>
                <button className="btn sm" onClick={() => setAsk({ kind: "duplicate", value: `${current.name} 복사본` })}>복제</button>
                {inTauri && <button className="btn sm" onClick={() => guard(exportFile)}>내보내기</button>}
                <button className="btn sm bare danger" onClick={() => setAsk({ kind: "delete" })}>삭제</button>
              </span>
            </div>
            <label className="field">
              파일 이름 별칭 (쉼표로 구분)
              <input className="input" value={aliases} placeholder="bleach, ブリーチ" onChange={(e) => { setAliases(e.target.value); setDirty(true); }} />
              <span className="small muted">영상 파일 이름에 용어집 이름이나 별칭이 들어 있으면 작업 옵션에서 이 용어집을 미리 골라 둡니다.</span>
            </label>

            <div className="tools-row">
              <label className="search">
                <Icon d={ICONS.search} size={14} />
                <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="일본어·한국어·메모 검색" aria-label="항목 검색" />
              </label>
              <span className={`small ${hints > MAX_HINTS ? "warn-fg" : "muted"}`}>
                인식 힌트 {hints}/{MAX_HINTS}{hints > MAX_HINTS ? " · 앞 40개만 쓰입니다" : ""}
              </span>
              <span style={{ marginLeft: "auto", display: "flex", gap: 6 }}>
                <button className="btn sm" aria-expanded={paste !== null} onClick={() => setPaste(paste === null ? "" : null)}>여러 줄 붙여 넣기</button>
                <button className="btn sm" onClick={() => { setRows([...rows, { ...BLANK }]); setDirty(true); }}>+ 항목</button>
              </span>
            </div>

            {paste !== null && (
              <div className="stack" style={{ gap: 6 }}>
                <textarea className="input" rows={5} value={paste} spellCheck={false} onChange={(e) => setPaste(e.target.value)}
                  aria-label="붙여 넣을 항목" placeholder={"一護=이치고 # 주인공\n*ルキア=루키아\n死神\t사신"} />
                <span className="small muted">한 줄에 일본어=한국어. 메모는 뒤에 “ # 메모”, 앞에 *를 붙이면 인식 힌트. 엑셀에서 복사한 칸(탭 구분)도 됩니다.</span>
                <span style={{ display: "flex", gap: 6 }}>
                  <button className="btn sm primary" onClick={addPasted}>목록에 넣기</button>
                  <button className="btn sm" onClick={() => setPaste(null)}>닫기</button>
                </span>
              </div>
            )}

            {error && <Note tone="err">{error}</Note>}

            <div className="table-wrap">
              <table className="t compact gl-table">
                <thead>
                  <tr>
                    <th scope="col" style={{ width: "28%" }}>일본어</th>
                    <th scope="col" style={{ width: "28%" }}>한국어</th>
                    <th scope="col">메모</th>
                    <th scope="col" style={{ width: 70 }} title="음성 인식에도 이 단어를 알려 줍니다">인식 힌트</th>
                    <th scope="col" style={{ width: 40 }}><span className="sr-only">지우기</span></th>
                  </tr>
                </thead>
                <tbody>
                  {shown.map(({ r, i }) => (
                    <tr key={i} className={dupes.has(r.ja.trim()) ? "dupe" : undefined}>
                      <td><input className="input sm" lang="ja" value={r.ja} aria-label={`${i + 1}번 일본어`} onChange={(e) => edit(i, { ja: e.target.value })} /></td>
                      <td><input className="input sm" value={r.ko} aria-label={`${i + 1}번 한국어`} onChange={(e) => edit(i, { ko: e.target.value })} /></td>
                      <td><input className="input sm" value={r.note} aria-label={`${i + 1}번 메모`} placeholder="말투, 성별 등" onChange={(e) => edit(i, { note: e.target.value })} /></td>
                      <td style={{ textAlign: "center" }}>
                        <input type="checkbox" checked={!!r.hint} aria-label={`${i + 1}번 인식 힌트`} onChange={(e) => edit(i, { hint: e.target.checked })} />
                      </td>
                      <td>
                        <button className="btn sm bare danger" aria-label={`${i + 1}번 지우기`} onClick={() => { setRows(rows.filter((_, j) => j !== i)); setDirty(true); }}>
                          <Icon d={ICONS.x} size={13} />
                        </button>
                      </td>
                    </tr>
                  ))}
                  {!shown.length && (
                    <tr><td colSpan={5} className="muted" style={{ textAlign: "center", padding: 18 }}>
                      {rows.length ? "검색과 맞는 항목이 없습니다." : "항목이 없습니다. + 항목이나 여러 줄 붙여 넣기로 추가하세요."}
                    </td></tr>
                  )}
                </tbody>
              </table>
            </div>
            {dupes.size > 0 && <span className="small warn-fg" role="status">같은 일본어가 두 번 있습니다: {[...dupes].join(", ")}</span>}
            <p className="small muted" style={{ margin: 0 }}>
              인식 힌트는 주인공처럼 자주 나오는 이름에만 켜세요. 힌트로 쓰는 항목이 바뀌면 다음 작업에서 음성 인식부터 다시 하고,
              나머지 변경은 번역만 다시 합니다. 아무것도 켜지 않으면 앞 40개를 힌트로 씁니다.
            </p>
            <div className="editor-foot">
              <span className="small muted">{dirty ? "저장하지 않은 변경이 있습니다" : `마지막 저장 ${when(current.updated)}`}</span>
              <span className="end">
                <button className="btn" disabled={!dirty} onClick={() => open(current.name)}>되돌리기</button>
                <button className="btn primary" disabled={!dirty} onClick={save}>저장</button>
              </span>
            </div>
          </section>
        )}
      </div>

      {ask && (ask.kind === "new" || ask.kind === "rename" || ask.kind === "duplicate") && (
        <NameModal title={{ new: "용어집 만들기", rename: "이름 바꾸기", duplicate: "용어집 복제" }[ask.kind]}
          action={{ new: "만들기", rename: "바꾸기", duplicate: "복제" }[ask.kind]} initial={ask.value}
          onClose={() => setAsk(null)} onSubmit={(v) => submitName(ask.kind, v)} />
      )}
      {ask?.kind === "delete" && current && (
        <Modal title="용어집 지우기" onClose={() => setAsk(null)} actions={<>
          <button className="btn" onClick={() => setAsk(null)}>취소</button>
          <button className="btn danger" onClick={remove}>지우기</button>
        </>}>
          <p>“{current.name}” 용어집({rows.length}개 항목)을 지웁니다. 되돌릴 수 없습니다.</p>
          <p className="small muted">이미 만든 작업에는 그때의 용어집 내용이 따로 저장되어 있어 영향이 없습니다.</p>
        </Modal>
      )}
      {ask?.kind === "discard" && (
        <Modal title="저장하지 않은 변경" onClose={() => setAsk(null)} actions={<>
          <button className="btn" onClick={() => setAsk(null)}>계속 편집</button>
          <button className="btn danger" onClick={() => { const t = ask.then; setAsk(null); setDirty(false); t(); }}>버리고 이동</button>
        </>}>
          <p>“{current?.name}”에 저장하지 않은 변경이 있습니다.</p>
        </Modal>
      )}
      {toast}
    </Page>
  );
}

function NameModal({ title, action, initial, onClose, onSubmit }: {
  title: string; action: string; initial: string; onClose: () => void; onSubmit: (v: string) => void;
}) {
  const [v, setV] = useState(initial);
  const ok = /^[\p{L}\p{N}_\-. ]{1,64}$/u.test(v.trim()) && !v.trim().startsWith(".");
  return (
    <Modal title={title} onClose={onClose} actions={<>
      <button className="btn" onClick={onClose}>취소</button>
      <button className="btn primary" disabled={!ok} onClick={() => onSubmit(v)}>{action}</button>
    </>}>
      <label className="field">
        이름 (작품 이름 등)
        <input className="input" data-autofocus value={v} onChange={(e) => setV(e.target.value)} placeholder="블리치"
          onKeyDown={(e) => e.key === "Enter" && ok && onSubmit(v)} />
      </label>
      {!ok && v.trim() && <p className="small warn-fg">글자, 숫자, 공백, - _ . 만 쓸 수 있습니다 (64자까지).</p>}
    </Modal>
  );
}
