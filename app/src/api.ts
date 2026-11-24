// Backend client. Inside Tauri the shell starts the backend and tells us its URL + token;
// in a plain browser (pnpm dev) we talk to `jamak serve` on VITE_JAMAK_URL or 127.0.0.1:8765.

export const inTauri = typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;

let base = "";
let token = "";

export async function connect(): Promise<void> {
  if (inTauri) {
    const { invoke } = await import("@tauri-apps/api/core");
    const info = await invoke<{ url: string; token: string }>("backend_info");
    base = info.url;
    token = info.token;
  } else {
    base = import.meta.env.VITE_JAMAK_URL ?? "http://127.0.0.1:8765";
  }
  for (let i = 0; i < 150; i++) {
    try {
      const r = await fetch(`${base}/health`);
      if (r.ok) return;
    } catch {
      /* not up yet */
    }
    await new Promise((res) => setTimeout(res, 200));
  }
  throw new Error(`백엔드(${base})에 연결하지 못했습니다.`);
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
  const r = await fetch(`${base}${path}`, {
    method,
    headers: { "content-type": "application/json", ...(token ? { "x-jamak-token": token } : {}) },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!r.ok) {
    let detail = r.statusText;
    try {
      const j = await r.json();
      detail = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail);
    } catch {
      /* keep statusText */
    }
    throw new ApiError(r.status, detail);
  }
  return r.json() as Promise<T>;
}

export const api = {
  get: <T,>(p: string) => call<T>("GET", p),
  post: <T,>(p: string, b?: unknown) => call<T>("POST", p, b ?? {}),
  patch: <T,>(p: string, b: unknown) => call<T>("PATCH", p, b),
  put: <T,>(p: string, b: unknown) => call<T>("PUT", p, b),
  del: <T,>(p: string) => call<T>("DELETE", p),
};

export function events(key: string, onEvent: (e: JobEvent) => void): () => void {
  let closed = false;
  let ws: WebSocket | null = null;
  const open = () => {
    const url = `${base.replace(/^http/, "ws")}/jobs/${key}/events${token ? `?token=${token}` : ""}`;
    ws = new WebSocket(url);
    ws.onmessage = (m) => onEvent(JSON.parse(m.data));
    ws.onclose = () => {
      if (!closed) setTimeout(open, 1000);
    };
  };
  open();
  return () => {
    closed = true;
    ws?.close();
  };
}

// ------------------------------------------------------------------ types (mirror the backend)

export type Stage = "audio" | "separate" | "stt" | "segment" | "translate" | "export";
export const STAGES: Stage[] = ["audio", "separate", "stt", "segment", "translate", "export"];
export const STAGE_WEIGHTS: Record<Stage, number> = {
  audio: 0.03, separate: 0.12, stt: 0.45, segment: 0.02, translate: 0.38, export: 0.0,
};
export const STAGE_NAMES: Record<Stage, string> = {
  audio: "오디오 추출", separate: "보컬 분리", stt: "음성 인식", segment: "자막 나누기",
  translate: "번역", export: "파일 저장",
};

export type JobStatus = "queued" | "running" | "done" | "error" | "cancelled";

export interface EngineSummary {
  kind: "managed" | "external" | "anthropic" | "gemini" | null;
  profile_id?: number | null;
  profile_name?: string | null;
  model?: string;
  cloud: boolean;
}

export interface Usage {
  requests: number; retries: number; splits: number; failed_lines: number; refused_lines: number;
  prompt_tokens: number; completion_tokens: number; cache_read_tokens: number; cache_write_tokens: number;
  cost_usd: number | null; batches_total: number; batches_done: number;
}

export interface LiveState {
  key: string;
  video_path: string;
  status: JobStatus;
  kind: "run" | "retranslate";
  stage: Stage | null;
  progress: number;
  message: string;
  error: string | null;
  outputs: string[];
  timings: Record<string, number>;
  stats: Record<string, Record<string, unknown>>;
  engine: EngineSummary | null;
  usage: Usage | null;
  queued_at: number;
  started_at: number | null;
  finished_at: number | null;
  events?: JobEvent[];
}

export type JobEvent =
  | { type: "snapshot"; job: string; state: LiveState | null }
  | ({ type: "status"; job: string } & LiveState)
  | {
      type: "progress"; job: string; stage: Stage;
      status: "start" | "progress" | "done" | "skipped";
      progress: number; overall: number; message: string; usage?: Usage; t?: number; elapsed?: number;
    };

export interface JobSummary {
  key: string;
  video_path: string;
  video_name?: string;
  duration?: number;
  updated?: string;
  translated?: boolean;
  last_export?: string[];
  engine?: EngineSummary | null;
  stages?: Record<string, { elapsed?: number; finished?: string }>;
  live: LiveState | null;
}

export interface Line {
  id: number;
  start: number;
  end: number;
  ja: string;
  ko: string;
  locked: boolean;
  flags: string[];
}

export interface GlossaryEntry { ja: string; ko: string; note: string }

export interface Options {
  audio: { track: number | null };
  separate: { enabled: boolean; model: string; segment_seconds: number };
  stt: { engine: string; model: string; device: string; compute_type: string; beam_size: number; vad: boolean };
  filters: { enabled: boolean };
  segment: { max_chars: number; max_duration: number };
  songs: { mode: "translate" | "skip"; ranges: [number, number][] };
  translate: {
    mode: "managed" | "external" | "anthropic" | "gemini"; profile_id: number | null; profile_name: string | null;
    model_path: string | null; base_url: string | null; model_name: string; api_model: string | null;
    batch_size: number; context_lines: number; temperature: number; parallel: number;
  };
  output: { formats: string[]; include_ja: boolean; dual_ass: boolean; out_dir: string | null; ko_max_line_chars: number };
  glossary: GlossaryEntry[];
}

export interface SystemInfo {
  version: string;
  cpu_name: string;
  home: string;
  ffmpeg: string | null;
  cuda_devices: number;
  gpus: { name: string; memory_total_mb: number; memory_used_mb: number }[];
  llama_server: { exists: boolean; build?: string };
  llm_models: { name: string; path: string; size_gb: number }[];
  default_llm: string | null;
  stt_models: string[];
  separation_available: boolean;
}

export interface Probe {
  duration: number;
  name: string;
  size: number;
  picked: number | null;
  tracks: { index: number; codec: string; language: string | null; title: string | null; channels: number; default: boolean }[];
}

export type ProfileKind = "local_managed" | "local_external" | "anthropic" | "gemini";

export interface ProfileOptions {
  batch_size: number; context_lines: number; parallel: number | null; gpu_layers: number;
  effort: "low" | "medium" | "high" | null; refusal: "local" | "mark"; temperature: number | null;
}

export interface ApiKey { id: number; provider: "anthropic" | "gemini" | "openai_compat"; label: string; last4: string; created_at: string }

export interface Profile {
  id: number; name: string; kind: ProfileKind; model: string | null; base_url: string | null; api_key_id: number | null;
  options: ProfileOptions; last_test: { ok: boolean; message: string; at: string } | null;
  cloud: boolean; is_default: boolean; key: ApiKey | null;
  status: { ok: boolean; ready: boolean; text: string; at?: string };
}

export interface UiSettings { palette: "seoul" | "acid" | "ice" | "blade"; sidebar: "open" | "hidden"; cloud_notice_seen: boolean }

export interface SystemUsage {
  gpu: { name: string; util: number; vram_used_gb: number; vram_total_gb: number } | null;
  cpu: number; ram_used_gb: number; ram_total_gb: number; cpu_name: string;
}

export interface Check {
  state: "ok" | "warn" | "err" | "off"; area: string; title: string; detail: string;
  fix: string | null; bar: number | null; action: "setup" | "engine" | null;
}

export const KIND_LABEL: Record<ProfileKind, string> = {
  local_managed: "로컬 내장", local_external: "외부 서버", anthropic: "Claude", gemini: "Gemini",
};

export function kindBadge(kind: ProfileKind | EngineSummary["kind"]): { text: string; cloud: boolean } {
  if (kind === "anthropic" || kind === "gemini") return { text: "클라우드", cloud: true };
  if (kind === "local_external" || kind === "external") return { text: "외부 서버", cloud: false };
  return { text: "로컬", cloud: false };
}

export function profileDetail(p: Profile, llmName?: string | null): string {
  const o = p.options;
  if (p.kind === "local_managed") return `llama-server · ${p.model ? basename(p.model) : llmName ?? "기본 GGUF"}`;
  if (p.kind === "local_external") return `${p.base_url ?? ""} · ${p.model || "local"}`;
  const key = p.key ? `키 ••••${p.key.last4}` : "등록된 키 없음";
  return `${p.model ?? "모델 미정"} · ${key} · 동시 요청 ${o.parallel ?? 4}`;
}

export const PROVIDER_NAME: Record<string, string> = { anthropic: "Anthropic", gemini: "Google", openai_compat: "외부 서버" };

// ------------------------------------------------------------------ formatting

export function tc(sec: number, withMs = true): string {
  const s = Math.max(0, sec);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const ss = Math.floor(s % 60);
  const ms = Math.round((s - Math.floor(s)) * 1000);
  const base = `${h > 0 ? `${h}:` : ""}${String(m).padStart(h > 0 ? 2 : 1, "0")}:${String(ss).padStart(2, "0")}`;
  return withMs ? `${base}.${String(ms).padStart(3, "0").slice(0, 2)}` : base;
}

export function parseTc(text: string): number | null {
  const t = text.trim();
  if (!/^[\d:.]+$/.test(t)) return null;
  let sec = 0;
  for (const p of t.split(":")) {
    const v = Number(p);
    if (Number.isNaN(v)) return null;
    sec = sec * 60 + v;
  }
  return sec;
}

export function basename(p: string): string {
  return p.split(/[\\/]/).pop() ?? p;
}

export function clock(ts: number | string | null | undefined, withSec = false): string {
  if (ts === null || ts === undefined) return "";
  const d = typeof ts === "number" ? new Date(ts * 1000) : new Date(ts);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit", ...(withSec ? { second: "2-digit" } : {}), hour12: false });
}

export function when(ts: string | number | null | undefined): string {
  if (ts === null || ts === undefined || ts === "") return "";
  const d = typeof ts === "number" ? new Date(ts * 1000) : new Date(ts);
  if (Number.isNaN(d.getTime())) return "";
  const now = new Date();
  const day = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const diff = Math.round((day(now) - day(d)) / 86400000);
  const hm = d.toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit", hour12: false });
  if (diff === 0) return `오늘 ${hm}`;
  if (diff === 1) return `어제 ${hm}`;
  return `${d.getMonth() + 1}월 ${d.getDate()}일`;
}

export function duration(sec: number): string {
  const s = Math.max(0, Math.round(sec));
  if (s < 60) return `${s}초`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}분 ${s % 60 ? `${s % 60}초` : ""}`.trim();
  return `${Math.floor(m / 60)}시간 ${m % 60}분`;
}

export function mmss(sec: number): string {
  const s = Math.max(0, Math.round(sec));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}
