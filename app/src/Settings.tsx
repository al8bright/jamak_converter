import { useEffect, useState } from "react";
import {
  api, type ApiKey, basename, type CloudKind, isCloud, kindBadge, type Profile, profileDetail,
  type ProfileKind, type ProfileOptions, PROVIDER_NAME, type ProviderInfo,
} from "./api";
import { useApp } from "./ctx";
import { Icon, ICONS, KindBadge, Modal, Note, Page, SettingsTabs, useToast } from "./ui";

type KindGroup = "local_managed" | "local_external" | "cloud";
const GROUPS: { id: KindGroup; label: string }[] = [
  { id: "local_managed", label: "로컬 내장" }, { id: "local_external", label: "외부 서버" }, { id: "cloud", label: "클라우드" },
];
const DEFAULT_OPTS: ProfileOptions = {
  batch_size: 25, context_lines: 5, parallel: null, gpu_layers: 99, effort: null, refusal: "local", temperature: null,
  fallback_profile_id: null,
};

function openLink(url: string) {
  api.post("/system/open-url", { url }).catch(() => {});
}

function useProviders(): ProviderInfo[] {
  const [list, setList] = useState<ProviderInfo[]>([]);
  useEffect(() => { api.get<ProviderInfo[]>("/settings/providers").then(setList).catch(() => {}); }, []);
  return list;
}

interface Draft {
  id: number | null; name: string; kind: ProfileKind; model: string; base_url: string;
  api_key_id: number | null; options: ProfileOptions;
}

function toDraft(p: Profile): Draft {
  return { id: p.id, name: p.name, kind: p.kind, model: p.model ?? "", base_url: p.base_url ?? "", api_key_id: p.api_key_id, options: { ...p.options } };
}

const EMPTY: Draft = { id: null, name: "", kind: "anthropic", model: "claude-opus-5-5", base_url: "", api_key_id: null, options: { ...DEFAULT_OPTS } };

export function Settings() {
  const { profiles, sys, go, refreshProfiles } = useApp();
  const [keys, setKeys] = useState<ApiKey[]>([]);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [toast, flash] = useToast();
  const providers = useProviders();

  const loadKeys = () => api.get<ApiKey[]>("/settings/api-keys").then(setKeys);
  useEffect(() => { loadKeys(); }, []);
  useEffect(() => {
    if (draft === null && profiles.length) setDraft(toDraft(profiles.find((p) => p.is_default) ?? profiles[0]));
  }, [profiles, draft]);

  async function setDefault(id: number) {
    await api.put("/settings/default-profile", { profile_id: id });
    await refreshProfiles();
    flash("기본 프로필을 바꿨습니다.");
  }

  const llmName = sys?.default_llm ? basename(sys.default_llm) : null;

  return (
    <Page eyebrow="SETTINGS" title="번역 엔진" wide actions={<>
      <SettingsTabs current="engine" go={(v) => go({ kind: v })} />
      <button className="btn cyan" onClick={() => setDraft({ ...EMPTY, options: { ...DEFAULT_OPTS } })}>+ 프로필 추가</button>
    </>}>
      <div className="cols">
        <div className="stack" style={{ flex: "1 1 460px" }}>
          <section className="card" aria-labelledby="pl-h">
            <div className="card-head">
              <h2 id="pl-h">번역 프로필</h2>
              <span className="small muted">작업을 만들 때 기본 프로필이 미리 선택됩니다</span>
            </div>
            <div className="stack" style={{ gap: 6 }}>
              {profiles.map((p) => {
                const kb = kindBadge(p.kind);
                const on = draft?.id === p.id;
                return (
                  <div key={p.id} className={`profile-row${on ? " on" : ""}`}>
                    <span className="p-body">
                      <span className="p-name">
                        <b>{p.name}</b>
                        <KindBadge cloud={kb.cloud} text={kb.text} />
                        {p.is_default && <span className="badge default">기본</span>}
                      </span>
                      <span className="p-detail">{profileDetail(p, llmName)}</span>
                      <span className="p-status" style={{ color: p.status.ok ? "var(--ok)" : p.status.ready ? "var(--muted)" : "var(--err)" }}>
                        <span className="dot" aria-hidden />{p.status.text}
                      </span>
                    </span>
                    <span style={{ display: "flex", gap: 4 }}>
                      {!p.is_default && <button className="btn sm" onClick={() => setDefault(p.id)}>기본으로</button>}
                      <button className="btn sm" aria-pressed={on} onClick={() => setDraft(toDraft(p))}>편집</button>
                    </span>
                  </div>
                );
              })}
            </div>
          </section>
          <KeysCard keys={keys} providers={providers} reload={async () => { await loadKeys(); await refreshProfiles(); }} flash={flash} />
        </div>

        {draft && (
          <Editor key={draft.id ?? "new"} draft={draft} keys={keys} providers={providers} profiles={profiles} llmModels={sys?.llm_models ?? []}
            onSaved={async (id, msg) => {
              await refreshProfiles();
              const fresh = (await api.get<Profile[]>("/settings/translation-profiles")).find((p) => p.id === id);
              if (fresh) setDraft(toDraft(fresh));
              flash(msg);
            }}
            onDeleted={async () => { await refreshProfiles(); setDraft(null); flash("프로필을 지웠습니다."); }}
            flash={flash} />
        )}
      </div>
      {toast}
    </Page>
  );
}

// ---------------------------------------------------------------------- API keys

function KeysCard({ keys, providers, reload, flash }: {
  keys: ApiKey[]; providers: ProviderInfo[]; reload: () => Promise<void>; flash: (t: string, bad?: boolean) => void;
}) {
  const [provider, setProvider] = useState<CloudKind>("anthropic");
  const [label, setLabel] = useState("");
  const [parts, setParts] = useState<string[]>(["", ""]);
  const [busy, setBusy] = useState(false);
  const [removing, setRemoving] = useState<ApiKey | null>(null);
  const [checks, setChecks] = useState<Record<number, { ok: boolean; message: string } | "busy">>({});
  const info = providers.find((p) => p.id === provider);
  const partNames = info?.key_parts ?? ["API 키"];
  const filled = partNames.every((_, i) => (parts[i] ?? "").trim().length >= 8);

  async function check(k: ApiKey) {
    setChecks((c) => ({ ...c, [k.id]: "busy" }));
    try {
      const r = await api.post<{ ok: boolean; message: string }>(`/settings/api-keys/${k.id}/check`);
      setChecks((c) => ({ ...c, [k.id]: r }));
    } catch (e) {
      setChecks((c) => ({ ...c, [k.id]: { ok: false, message: (e as Error).message } }));
    }
  }

  async function save() {
    setBusy(true);
    try {
      const secret = partNames.map((_, i) => (parts[i] ?? "").trim()).join("\n");
      const k = await api.post<ApiKey>("/settings/api-keys", { provider, label: label.trim(), secret });
      setParts(["", ""]);
      setLabel("");
      await reload();
      flash("키를 저장했습니다. 키를 확인합니다…");
      check(k);
    } catch (e) {
      flash(`키를 저장하지 못했습니다: ${(e as Error).message}`, true);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card" aria-labelledby="keys-h">
      <h2 id="keys-h">API 키</h2>
      {keys.length > 0 ? (
        <div className="table-wrap" style={{ background: "transparent" }}>
          <table className="t compact">
            <thead><tr><th scope="col">제공자</th><th scope="col">이름</th><th scope="col">키</th><th scope="col">상태</th><th scope="col"><span className="sr-only">작업</span></th></tr></thead>
            <tbody>
              {keys.map((k) => {
                const c = checks[k.id];
                return (
                  <tr key={k.id}>
                    <td>{PROVIDER_NAME[k.provider] ?? k.provider}</td>
                    <td>{k.label || "—"}</td>
                    <td className="mono" style={{ fontSize: 12 }}>••••{k.last4}</td>
                    <td className={`small ${c && c !== "busy" ? (c.ok ? "ok-fg" : "err-fg") : "muted"}`} role="status">
                      {c === "busy" ? "확인하는 중…" : c ? c.message : `${k.created_at.slice(0, 10)} 등록`}
                    </td>
                    <td style={{ textAlign: "right", whiteSpace: "nowrap" }}>
                      {k.provider !== "openai_compat" && <button className="btn bare sm" disabled={c === "busy"} onClick={() => check(k)}>키 확인</button>}
                      <button className="btn bare danger sm" onClick={() => setRemoving(k)}>삭제</button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : <p className="small muted" style={{ margin: 0 }}>등록된 키가 없습니다. 클라우드 번역을 쓰려면 키를 하나 저장하세요.</p>}

      <form className="key-form" onSubmit={(e) => { e.preventDefault(); if (filled) save(); }}>
        <label className="field">
          제공자
          <select className="input" value={provider} onChange={(e) => { setProvider(e.target.value as CloudKind); setParts(["", ""]); }}>
            <optgroup label="LLM (말투·문맥 반영)">
              {providers.filter((p) => p.llm).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </optgroup>
            <optgroup label="번역 전용 API">
              {providers.filter((p) => !p.llm).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </optgroup>
          </select>
        </label>
        <label className="field">
          이름
          <input className="input" value={label} onChange={(e) => setLabel(e.target.value)} placeholder="예: 개인 키" maxLength={60} />
        </label>
        {partNames.map((n, i) => (
          <label key={n} className="field wide">
            {n}
            <input className="input mono" type="password" value={parts[i] ?? ""} autoComplete="off" spellCheck={false} placeholder="붙여 넣기"
              onChange={(e) => setParts((ps) => { const next = [...ps]; next[i] = e.target.value; return next; })} />
          </label>
        ))}
        <button className="btn primary" disabled={busy || !filled}>키 저장</button>
      </form>
      {info && (
        <p className="small muted" style={{ margin: 0 }}>
          <button type="button" className="link" onClick={() => openLink(info.key_url)}>{info.name} 키 만들기</button>
          {" · "}
          <button type="button" className="link" onClick={() => openLink(info.pricing_url)}>요금 보기</button>
          {info.key_note && <> · {info.key_note}</>}
        </p>
      )}
      <p className="lock-p">
        <Icon d={ICONS.lock} size={12} />
        키는 현재 Windows 사용자 계정으로 암호화해 이 PC의 설정 파일(jamak.db)에 저장합니다. 저장한 뒤에는 끝 4자리만 볼 수 있습니다.
      </p>

      {removing && (
        <Modal title="키를 지울까요?" onClose={() => setRemoving(null)} actions={<>
          <button className="btn" onClick={() => setRemoving(null)}>취소</button>
          <button className="btn danger" onClick={async () => {
            try {
              await api.del(`/settings/api-keys/${removing.id}`);
              flash("키를 지웠습니다.");
            } catch (e) {
              flash(`키를 지우지 못했습니다: ${(e as Error).message}`, true);
            }
            setRemoving(null);
            await reload();
          }}>키 삭제</button>
        </>}>
          <p>{PROVIDER_NAME[removing.provider]} 키 ••••{removing.last4}{removing.label ? ` (${removing.label})` : ""}를 지웁니다. 이 키를 쓰는 프로필은 새 키를 고를 때까지 쓸 수 없습니다.</p>
        </Modal>
      )}
    </section>
  );
}

// ---------------------------------------------------------------------- profile editor

function Editor({ draft: initial, keys, providers, profiles, llmModels, onSaved, onDeleted, flash }: {
  draft: Draft; keys: ApiKey[]; providers: ProviderInfo[]; profiles: Profile[]; llmModels: { name: string; path: string; size_gb: number }[];
  onSaved: (id: number, msg: string) => void; onDeleted: () => void; flash: (t: string, bad?: boolean) => void;
}) {
  const [d, setD] = useState<Draft>(initial);
  const [models, setModels] = useState<string[]>([]);
  const [modelErr, setModelErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [test, setTest] = useState<{ ok: boolean; message: string } | null>(null);
  const [testing, setTesting] = useState(false);
  const [confirmDel, setConfirmDel] = useState(false);
  const saved = profiles.find((p) => p.id === d.id) ?? null;
  const cloud = isCloud(d.kind);
  const provider = cloud ? d.kind : null;
  const info = providers.find((p) => p.id === provider);
  const llm = info?.llm ?? true;
  const kindKeys = keys.filter((k) => k.provider === provider);
  const fallbacks = profiles.filter((p) => p.cloud && p.id !== d.id);
  const o = d.options;
  const setO = (patch: Partial<ProfileOptions>) => setD({ ...d, options: { ...o, ...patch } });

  useEffect(() => {
    setModels([]);
    setModelErr(null);
    if (d.kind === "anthropic") api.get<{ models: string[] }>("/settings/models?kind=anthropic").then((r) => setModels(r.models));
    else if (isCloud(d.kind) && d.kind !== "deepl" && d.kind !== "papago" && d.api_key_id !== null) {
      api.get<{ models: string[] }>(`/settings/models?kind=${d.kind}&key_id=${d.api_key_id}`)
        .then((r) => setModels(r.models))
        .catch((e) => setModelErr((e as Error).message));
    }
  }, [d.kind, d.api_key_id]);

  useEffect(() => {
    setTest(saved?.last_test ? { ok: saved.last_test.ok, message: saved.last_test.message } : null);
  }, [saved?.last_test]);

  function changeKind(kind: ProfileKind) {
    const key = keys.find((k) => k.provider === kind);
    setD({
      ...d, kind,
      model: kind === "anthropic" ? "claude-opus-5-5" : kind === "local_external" ? "local" : "",
      base_url: kind === "local_external" ? d.base_url || "http://127.0.0.1:8080/v1" : "",
      api_key_id: isCloud(kind) ? key?.id ?? null : null,
    });
  }
  const group: KindGroup = cloud ? "cloud" : (d.kind as KindGroup);
  function changeGroup(g: KindGroup) {
    if (g === group) return;
    changeKind(g === "cloud" ? (keys.find((k) => isCloud(k.provider))?.provider as CloudKind | undefined) ?? "anthropic" : g);
  }

  function body() {
    return {
      name: d.name.trim(), kind: d.kind,
      model: d.model.trim() || null,
      base_url: d.kind === "local_external" ? d.base_url.trim() : null,
      api_key_id: cloud ? d.api_key_id : null,
      options: o,
    };
  }

  async function save(): Promise<number | null> {
    setBusy(true);
    try {
      const r = d.id === null
        ? await api.post<Profile>("/settings/translation-profiles", body())
        : await api.put<Profile>(`/settings/translation-profiles/${d.id}`, body());
      setD({ ...d, id: r.id });
      onSaved(r.id, d.id === null ? "프로필을 만들었습니다." : "프로필을 저장했습니다.");
      return r.id;
    } catch (e) {
      flash(`저장하지 못했습니다: ${(e as Error).message}`, true);
      return null;
    } finally {
      setBusy(false);
    }
  }

  async function runTest() {
    const id = await save();
    if (id === null) return;
    setTesting(true);
    setTest(null);
    try {
      const r = await api.post<{ ok: boolean; message: string }>(`/settings/translation-profiles/${id}/test`);
      setTest(r);
      onSaved(id, r.ok ? "연결을 확인했습니다." : "연결 테스트에 실패했습니다.");
    } catch (e) {
      setTest({ ok: false, message: (e as Error).message });
    } finally {
      setTesting(false);
    }
  }

  const valid = d.name.trim() && (d.kind !== "local_external" || d.base_url.trim())
    && (!cloud || ((!llm || d.model.trim()) && (d.api_key_id !== null || !!info?.env.length)));

  return (
    <section className="editor" aria-labelledby="ed-h">
      <div className="card-head">
        <span>
          <span className="eyebrow">{d.id === null ? "NEW PROFILE" : "EDIT PROFILE"}</span>
          <h2 id="ed-h">{d.name || "새 프로필"}</h2>
        </span>
        {saved?.is_default && <span className="badge default">기본</span>}
      </div>

      <label className="field">
        이름
        <input className="input" value={d.name} maxLength={60} onChange={(e) => setD({ ...d, name: e.target.value })} placeholder="예: Claude · Opus 5.5" />
      </label>

      <div className="field">
        <span id="kind-l">종류</span>
        <div className="seg fill" role="group" aria-labelledby="kind-l">
          {GROUPS.map((g) => (
            <button key={g.id} type="button" aria-pressed={group === g.id} onClick={() => changeGroup(g.id)}>{g.label}</button>
          ))}
        </div>
      </div>
      {cloud && (
        <label className="field">
          서비스
          <select className="input" value={d.kind} onChange={(e) => changeKind(e.target.value as ProfileKind)}>
            <optgroup label="LLM (말투·문맥 반영)">
              {providers.filter((p) => p.llm).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </optgroup>
            <optgroup label="번역 전용 API (용어집은 단어 치환만)">
              {providers.filter((p) => !p.llm).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </optgroup>
          </select>
        </label>
      )}

      {d.kind === "local_managed" && (
        <>
          <label className="field">
            GGUF 모델
            <select className="input mono" value={d.model} onChange={(e) => setD({ ...d, model: e.target.value })}>
              <option value="">기본 GGUF (설치 시 받은 모델)</option>
              {llmModels.map((m) => <option key={m.path} value={m.path}>{m.name} · {m.size_gb.toFixed(1)} GB</option>)}
            </select>
          </label>
          <span className="small muted" style={{ marginTop: -6 }}>다른 GGUF는 jamak 폴더의 models\llm에 넣으면 목록에 나타납니다.</span>
          <div className="grid2">
            <label className="field">
              GPU 레이어
              <input className="input mono" type="number" min={0} max={999} value={o.gpu_layers} onChange={(e) => setO({ gpu_layers: Number(e.target.value) })} />
            </label>
            <label className="field">
              병렬 슬롯
              <select className="input" value={o.parallel ?? 1} onChange={(e) => setO({ parallel: Number(e.target.value) })}>
                <option value={1}>1 (순차)</option>
                <option value={2}>2</option>
                <option value={4}>4</option>
              </select>
            </label>
          </div>
        </>
      )}

      {d.kind === "local_external" && (
        <div className="grid2">
          <label className="field">
            서버 주소 (OpenAI 호환)
            <input className="input mono" value={d.base_url} onChange={(e) => setD({ ...d, base_url: e.target.value })} placeholder="http://127.0.0.1:8080/v1" />
          </label>
          <label className="field">
            모델 이름
            <input className="input mono" value={d.model} onChange={(e) => setD({ ...d, model: e.target.value })} placeholder="local" />
          </label>
        </div>
      )}

      {cloud && (
        <>
          <div className="grid2">
            {llm && <label className="field">
              모델
              {models.length ? (
                <select className="input mono" value={d.model} onChange={(e) => setD({ ...d, model: e.target.value })}>
                  {!models.includes(d.model) && d.model && <option value={d.model}>{d.model}</option>}
                  {!d.model && <option value="">모델 고르기</option>}
                  {models.map((m) => <option key={m} value={m}>{m}</option>)}
                </select>
              ) : (
                <input className="input mono" value={d.model} onChange={(e) => setD({ ...d, model: e.target.value })}
                  placeholder={info?.lists_models && d.api_key_id === null ? `키를 고르면 목록을 불러옵니다 (예: ${info.model_hint})` : info?.model_hint} />
              )}
            </label>}
            <label className="field">
              API 키
              <select className="input" value={d.api_key_id ?? ""} onChange={(e) => setD({ ...d, api_key_id: e.target.value ? Number(e.target.value) : null })}>
                <option value="">{kindKeys.length ? "키 고르기" : "등록된 키 없음"}</option>
                {kindKeys.map((k) => <option key={k.id} value={k.id}>{k.label || PROVIDER_NAME[k.provider]} · ••••{k.last4}</option>)}
              </select>
            </label>
            <label className="field">
              동시 요청 수
              <select className="input" value={o.parallel ?? 4} onChange={(e) => setO({ parallel: Number(e.target.value) })}>
                {[1, 2, 4, 8].map((n) => <option key={n} value={n}>{n}</option>)}
              </select>
            </label>
            <label className="field">
              이 서비스가 실패하면
              <select className="input" value={o.fallback_profile_id ?? ""}
                onChange={(e) => setO({ fallback_profile_id: e.target.value ? Number(e.target.value) : null })}>
                <option value="">멈추고 알리기</option>
                {fallbacks.map((p) => <option key={p.id} value={p.id}>“{p.name}”으로 마저 번역</option>)}
              </select>
            </label>
            {llm && <label className="field">
              거절 응답일 때
              <select className="input" value={o.refusal} onChange={(e) => setO({ refusal: e.target.value as "local" | "mark" })}>
                <option value="local">로컬로 다시 번역</option>
                <option value="mark">검수 화면에 표시만</option>
              </select>
            </label>}
            {d.kind === "anthropic" && (
              <label className="field">
                추론 강도
                <select className="input" value={o.effort ?? ""} onChange={(e) => setO({ effort: (e.target.value || null) as ProfileOptions["effort"] })}>
                  <option value="">기본 (medium)</option>
                  <option value="low">low · 빠르고 저렴</option>
                  <option value="medium">medium</option>
                  <option value="high">high · 느리고 비쌈</option>
                </select>
              </label>
            )}
          </div>
          {!llm && <Note>번역 전용 API는 말투 지시와 앞뒤 문맥을 반영하지 못합니다. 용어집 단어는 번역 전에 한국어로 바꿔 넣습니다. 요금은 보낸 글자 수로 계산됩니다.</Note>}
          {!kindKeys.length && <Note tone="warn">{PROVIDER_NAME[provider!]} 키가 없습니다. 왼쪽 API 키 칸에서 먼저 등록하세요.</Note>}
          {modelErr && <Note tone="err">{modelErr}</Note>}
          <Note>이 프로필로 번역하면 일본어 대사 텍스트가 {PROVIDER_NAME[provider!]}으로 전송됩니다. 영상과 음성은 전송되지 않습니다.</Note>
        </>
      )}

      <div className="grid2">
        <label className="field">
          묶음 크기 (줄)
          <input className="input mono" type="number" min={1} max={200} value={o.batch_size} onChange={(e) => setO({ batch_size: Number(e.target.value) })} />
        </label>
        <label className="field">
          문맥 줄 수
          <input className="input mono" type="number" min={0} max={50} value={o.context_lines} onChange={(e) => setO({ context_lines: Number(e.target.value) })} />
        </label>
      </div>

      <div className="editor-foot">
        <button className="btn cyan-ghost" onClick={runTest} disabled={!valid || busy || testing}>{testing ? "확인하는 중…" : "연결 테스트"}</button>
        {test && (
          <span className={`test-result ${test.ok ? "ok-fg" : "err-fg"}`} role="status">
            <Icon d={test.ok ? ICONS.check : ICONS.x} size={13} width={2.6} />{test.message}
          </span>
        )}
        <span className="end">
          {d.id !== null && profiles.length > 1 && <button className="btn bare danger" onClick={() => setConfirmDel(true)}>삭제</button>}
          <button className="btn primary" onClick={save} disabled={!valid || busy}>저장</button>
        </span>
      </div>

      {confirmDel && (
        <Modal title="프로필을 지울까요?" onClose={() => setConfirmDel(false)} actions={<>
          <button className="btn" onClick={() => setConfirmDel(false)}>취소</button>
          <button className="btn danger" onClick={async () => {
            try {
              await api.del(`/settings/translation-profiles/${d.id}`);
              setConfirmDel(false);
              onDeleted();
            } catch (e) {
              setConfirmDel(false);
              flash(`지우지 못했습니다: ${(e as Error).message}`, true);
            }
          }}>프로필 삭제</button>
        </>}>
          <p>"{d.name}" 프로필을 지웁니다. 이미 만든 자막과 작업 기록은 그대로 남습니다.{saved?.is_default ? " 기본 프로필은 로컬 프로필로 바뀝니다." : ""}</p>
        </Modal>
      )}
    </section>
  );
}
