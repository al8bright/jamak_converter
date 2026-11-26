// This app's own parts. The shared parts (Icon, Page, Modal, Note, useToast …) live in common.tsx
// (same file in all four apps) and are re-exported from here.

export * from "./common";

// ------------------------------------------------------------------ logo: corner brackets, 字 -> 자

export function Logo({ big }: { big?: boolean }) {
  const size = big ? 60 : 30;
  return (
    <span className={`logo${big ? " big" : ""}`} aria-hidden>
      <svg viewBox="0 0 32 32" width={size} height={size}>
        <path d="M3 11V3h8M29 21v8h-8" fill="none" strokeWidth="2.6" strokeLinecap="square" />
      </svg>
      <span className="g ja" lang="ja">字</span>
      <span className="g ko">자</span>
      <span className="g gh">자</span>
      <span className="g gh2" lang="ja">字</span>
      <span className="line" />
    </span>
  );
}

// ------------------------------------------------------------------ icons (Lucide-style line paths from the design)

export const ICONS = {
  home: "M3 10.5 12 3l9 7.5M5 9.5V21h14V9.5",
  progress: "M3 12h4l3-8 4 16 3-8h4",
  review: "M4 6h16M4 12h10M4 18h7M15 18l2 2 4-4",
  engine: "M4 6h9M17 6h3M4 12h3M11 12h9M4 18h11M19 18h1M13 6a2 2 0 1 0 4 0a2 2 0 1 0 -4 0M7 12a2 2 0 1 0 4 0a2 2 0 1 0 -4 0M15 18a2 2 0 1 0 4 0a2 2 0 1 0 -4 0",
  system: "M8 6h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2zM9 2v4M15 2v4M9 18v4M15 18v4M2 9h4M2 15h4M18 9h4M18 15h4",
  film: "M3 7a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2zM3 9h18M8 5v4M16 5v4M10 12.5v4l3.5-2z",
  video: "M5 5h14a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2zM10 9.5v5l4-2.5z",
  folder: "M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z",
  lock: "M7 11h10a2 2 0 0 1 2 2v6a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2v-6a2 2 0 0 1 2-2zM8 11V7a4 4 0 0 1 8 0v4",
  info: "M12 3a9 9 0 1 0 0 18a9 9 0 1 0 0-18M12 8h.01M11 12h1v5h1",
  search: "M11 4a7 7 0 1 0 0 14a7 7 0 1 0 0-14M20 20l-3.5-3.5",
  check: "M5 12.5 10 17l9-10",
  x: "M6 6l12 12M18 6 6 18",
  alert: "M12 3 2 20h20zM12 10v4M12 17h.01",
  book: "M4 4.5A2.5 2.5 0 0 1 6.5 2H20v17H6.5A2.5 2.5 0 0 0 4 21.5zM4 21.5A2.5 2.5 0 0 1 6.5 19H20M8 7h8M8 11h6",
  download: "M12 3v12M7 10l5 5 5-5M4 19h16",
};

// ------------------------------------------------------------------ settings

type SettingsView = "engine" | "glossary" | "models" | "system";

export function SettingsTabs({ current, go }: { current: SettingsView; go: (v: SettingsView) => void }) {
  return (
    <nav className="seg" aria-label="설정 영역">
      <button aria-current={current === "engine" ? "page" : undefined} onClick={() => go("engine")}>번역 엔진</button>
      <button aria-current={current === "glossary" ? "page" : undefined} onClick={() => go("glossary")}>용어집</button>
      <button aria-current={current === "models" ? "page" : undefined} onClick={() => go("models")}>모델 관리</button>
      <button aria-current={current === "system" ? "page" : undefined} onClick={() => go("system")}>시스템 상태</button>
    </nav>
  );
}

export function KindBadge({ cloud, text }: { cloud: boolean; text: string }) {
  return <span className={`badge${cloud ? " cloud" : ""}`}>{text}</span>;
}
