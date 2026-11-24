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
  stats: Record<string, Record<string, number>>;
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
      progress: number; overall: number; message: string;
    };

export interface JobSummary {
  key: string;
  video_path: string;
  video_name?: string;
  duration?: number;
  updated?: string;
  translated?: boolean;
  last_export?: string[];
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
    mode: "managed" | "external"; model_path: string | null; base_url: string | null; model_name: string;
    batch_size: number; context_lines: number; temperature: number; parallel: number;
  };
  output: { formats: string[]; include_ja: boolean; dual_ass: boolean; out_dir: string | null; ko_max_line_chars: number };
  glossary: GlossaryEntry[];
}

export interface SystemInfo {
  version: string;
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
