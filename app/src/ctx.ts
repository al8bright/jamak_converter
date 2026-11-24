import { createContext, useContext } from "react";
import type { JobSummary, Profile, SystemInfo, UiSettings } from "./api";

export type View =
  | { kind: "home" }
  | { kind: "options"; path: string }
  | { kind: "progress"; key?: string }
  | { kind: "review"; key?: string }
  | { kind: "engine" }
  | { kind: "system" }
  | { kind: "setup" };

export interface AppState {
  sys: SystemInfo | null;
  jobs: JobSummary[];
  profiles: Profile[];
  ui: UiSettings;
  go: (v: View) => void;
  openJob: (key: string) => void;
  refreshJobs: () => Promise<void>;
  refreshProfiles: () => Promise<void>;
  refreshSys: () => Promise<void>;
  saveUi: (patch: Partial<UiSettings>) => Promise<void>;
}

export const Ctx = createContext<AppState | null>(null);

export function useApp(): AppState {
  const c = useContext(Ctx);
  if (!c) throw new Error("no app context");
  return c;
}

export function isLive(j: JobSummary): boolean {
  return !!j.live && (j.live.status === "running" || j.live.status === "queued");
}
