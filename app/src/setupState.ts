/** First-run and model-management state shared by Setup.tsx and Models.tsx. */
import { useCallback, useEffect, useState } from "react";
import { api } from "./api";

export interface SetupComponent {
  id: string;
  kind: "tool" | "runtime" | "stt" | "llm";
  name: string;
  note: string;
  summary: string;
  license: string;
  tags: string[];
  vram_gb: number | null;
  approx_bytes: number;
  disk_bytes: number | null;
  requires: string[];
  missing_requires: string[];
  used_by: string[];
  removable: boolean;
  status: "present" | "missing" | "queued" | "running" | "done" | "error" | "cancelled";
  done_bytes: number;
  total_bytes: number;
  message: string;
  error: string | null;
}

export interface SetupStatus {
  running: boolean;
  components: SetupComponent[];
  ready: { ok: boolean; ffmpeg: boolean; stt: string[]; local_translation: boolean; gpu: boolean };
  vram_gb: number | null;
  recommended_stt: string;
  platform?: "win" | "mac";
  accelerator?: "cuda" | "metal" | "cpu";
  free_bytes: number;
}

/** What model memory is called on this computer: VRAM on an NVIDIA card, part of unified memory on a Mac. */
export function memoryLabel(s: SetupStatus): string {
  return s.platform === "mac" ? "모델에 쓸 수 있는 메모리" : "GPU 메모리";
}

/** Text for the memory a computer can give models ("" when unknown). */
export function memoryNote(s: SetupStatus): string {
  if (s.vram_gb) return s.platform === "mac" ? `이 Mac에서 ${memoryLabel(s)} 약 ${s.vram_gb}GB` : `이 PC의 GPU 메모리 ${s.vram_gb}GB`;
  return s.platform === "mac" ? "Apple Silicon이 아니라 매우 느립니다" : "NVIDIA GPU를 찾지 못했습니다";
}

/** First run: the app cannot make a subtitle until FFmpeg and one speech-recognition model are in place. */
export function setupNeeded(s: SetupStatus | null): boolean {
  return !!s && (s.running || !s.ready.ok);
}

export function gb(bytes: number): string {
  return bytes >= 1e9 ? `${(bytes / 1e9).toFixed(1)}GB` : `${Math.max(1, Math.round(bytes / 1e6))}MB`;
}

export const isReady = (c: SetupComponent) => c.status === "present" || c.status === "done";

/** `ids` plus what they need, minus what is already installed: what pressing "받기" downloads. */
export function toDownload(s: SetupStatus, ids: string[]): SetupComponent[] {
  const by = new Map(s.components.map((c) => [c.id, c]));
  const want = new Set<string>();
  const add = (id: string) => {
    const c = by.get(id);
    if (!c || want.has(id)) return;
    want.add(id);
    c.requires.forEach(add);
  };
  ids.forEach(add);
  return s.components.filter((c) => want.has(c.id) && !isReady(c));
}

export function useSetup(sizes = false) {
  const [status, setStatus] = useState<SetupStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const poll = useCallback(async () => {
    try {
      setStatus(await api.get<SetupStatus>(`/setup${sizes ? "?sizes=true" : ""}`));
    } catch (e) {
      setError((e as Error).message);
    }
  }, [sizes]);
  useEffect(() => { poll(); }, [poll]);
  useEffect(() => {
    if (!status?.running) return;
    const t = setInterval(poll, 700);
    return () => clearInterval(t);
  }, [status?.running, poll]);
  const install = useCallback(async (ids: string[]) => {
    setError(null);
    try {
      await api.post("/setup/install", { ids });
    } catch (e) {
      setError((e as Error).message);
    }
    poll();
  }, [poll]);
  const cancel = useCallback(() => api.post("/setup/cancel").then(poll), [poll]);
  return { status, error, setError, poll, install, cancel };
}

