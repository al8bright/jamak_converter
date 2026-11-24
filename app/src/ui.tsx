import { type ReactNode, useEffect, useRef, useState } from "react";

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
      <span className="bar" />
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
};

export function Icon({ d, size = 16, stroke, width = 2, className }: {
  d: string; size?: number; stroke?: string; width?: number; className?: string;
}) {
  return (
    <svg aria-hidden width={size} height={size} viewBox="0 0 24 24" fill="none" stroke={stroke ?? "currentColor"}
      strokeWidth={width} strokeLinecap="round" strokeLinejoin="round" className={className}>
      <path d={d} />
    </svg>
  );
}

// ------------------------------------------------------------------ page frame: header + glowing body panel

export function Page({ eyebrow, title, actions, children, wide, label = "본문" }: {
  eyebrow: ReactNode; title: ReactNode; actions?: ReactNode; children: ReactNode; wide?: boolean; label?: string;
}) {
  return (
    <>
      <header className="head">
        <div className="head-title">
          <span className="eyebrow">{eyebrow}</span>
          <h1>{title}</h1>
        </div>
        {actions && <div className="head-actions">{actions}</div>}
      </header>
      <div className="panel" role="region" aria-label={label} tabIndex={0}>
        <div className={`inner${wide ? " wide" : ""}`}>{children}</div>
      </div>
    </>
  );
}

export function SettingsTabs({ current, go }: { current: "engine" | "system"; go: (v: "engine" | "system") => void }) {
  return (
    <nav className="seg" aria-label="설정 영역">
      <button aria-current={current === "engine" ? "page" : undefined} onClick={() => go("engine")}>번역 엔진</button>
      <button aria-current={current === "system" ? "page" : undefined} onClick={() => go("system")}>시스템 상태</button>
    </nav>
  );
}

export function KindBadge({ cloud, text }: { cloud: boolean; text: string }) {
  return <span className={`badge${cloud ? " cloud" : ""}`}>{text}</span>;
}

export function Note({ tone, children }: { tone?: "warn" | "err"; children: ReactNode }) {
  return (
    <div role="note" className={`note${tone ? ` ${tone}` : ""}`}>
      <Icon d={tone ? ICONS.alert : ICONS.info} size={15} />
      <span className="grow">{children}</span>
    </div>
  );
}

// ------------------------------------------------------------------ modal (in-app; never a browser dialog)

export function Modal({ title, children, actions, onClose }: {
  title: string; children: ReactNode; actions: ReactNode; onClose: () => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    ref.current?.querySelector<HTMLElement>("button.primary, button")?.focus();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div className="scrim" onClick={onClose}>
      <div className="modal" role="dialog" aria-modal="true" aria-label={title} ref={ref} onClick={(e) => e.stopPropagation()}>
        <h2>{title}</h2>
        {children}
        <div className="modal-actions">{actions}</div>
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ toast

export function useToast(): [ReactNode, (text: string, bad?: boolean) => void] {
  const [t, setT] = useState<{ text: string; bad: boolean } | null>(null);
  const timer = useRef<number | undefined>(undefined);
  const show = (text: string, bad = false) => {
    setT({ text, bad });
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setT(null), 3000);
  };
  const node = t ? <div className={`toast${t.bad ? " err" : ""}`} role="status">{t.text}</div> : null;
  return [node, show];
}
