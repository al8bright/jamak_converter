import { useCallback, useEffect, useState } from "react";
import { api, events, type JobEvent, type LiveState, type Options, type Stage, type Usage } from "./api";

export interface JobDetail {
  key: string;
  meta: {
    video_path: string; video_name: string; duration?: number; last_export?: string[]; updated?: string;
    stages: Record<string, { elapsed: number; finished?: string; [k: string]: unknown }>;
  };
  options: Options;
  live: LiveState | null;
}

export type StageState = { status: string; progress: number; message: string; t?: number; elapsed?: number };
export type ProgressEvent = Extract<JobEvent, { type: "progress" }>;

/** Job detail + live state over the WebSocket. `onFinish` runs when a run/retranslate ends. */
export function useJob(key: string | undefined, onFinish?: (status: string, live: LiveState) => void) {
  const [detail, setDetail] = useState<JobDetail | null>(null);
  const [live, setLive] = useState<LiveState | null>(null);
  const [stages, setStages] = useState<Partial<Record<Stage, StageState>>>({});
  const [log, setLog] = useState<ProgressEvent[]>([]);
  const [usage, setUsage] = useState<Usage | null>(null);
  const [missing, setMissing] = useState(false);

  const load = useCallback(async () => {
    if (!key) return;
    try {
      const d = await api.get<JobDetail>(`/jobs/${key}`);
      setDetail(d);
      setLive(d.live);
      setUsage(d.live?.usage ?? null);
      if (d.live?.events) {
        const s: Partial<Record<Stage, StageState>> = {};
        const l: ProgressEvent[] = [];
        for (const e of d.live.events) {
          if (e.type !== "progress") continue;
          s[e.stage] = e;
          l.push(e);
        }
        setStages(s);
        setLog(l);
      }
    } catch {
      setMissing(true);
    }
  }, [key]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    if (!key) return;
    return events(key, (e: JobEvent) => {
      if (e.type === "progress") {
        setStages((s) => ({ ...s, [e.stage]: e }));
        setLog((l) => [...l.slice(-400), e]);
        if (e.usage) setUsage(e.usage);
        setLive((l) => (l ? { ...l, stage: e.stage, progress: e.overall, message: e.message } : l));
      } else if (e.type === "status") {
        setLive(e);
        if (e.status === "queued") {
          setStages({});
          setLog([]);
          setUsage(null);
        }
        if (e.status === "done" || e.status === "error" || e.status === "cancelled") {
          load();
          onFinish?.(e.status, e);
        }
      }
    });
  }, [key, load, onFinish]);

  return { detail, live, stages, log, usage, missing, reload: load };
}
