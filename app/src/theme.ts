// Palettes from the design canvas (Brand board). The app sets them as CSS variables on :root.

export type PaletteName = "seoul" | "acid" | "ice" | "blade";

interface Base {
  bg: string; side: string; panel: string; card: string; field: string; line: string; lineHi: string;
  text: string; muted: string; p: string; s: string; pInk: string;
  ok: string; warn: string; err: string; // status colors, chosen per palette (DESIGN.md 3.3)
}

export const PALETTES: Record<PaletteName, Base & { label: string }> = {
  seoul: { label: "서울 네온", bg: "#07050F", side: "#0B0817", panel: "#0E0A1C", card: "#141026", field: "#1A1530", line: "#2A2246", lineHi: "#3D3363", text: "#EEEAFF", muted: "#A49CC8", p: "#FF2E97", s: "#00E5FF", pInk: "#14000A", ok: "#3DFF9A", warn: "#FFD43B", err: "#FF773D" },
  acid: { label: "애시드", bg: "#040705", side: "#070C08", panel: "#0A100B", card: "#0F1711", field: "#142017", line: "#1F3023", lineHi: "#2E4734", text: "#E8F6E6", muted: "#93AC97", p: "#C6FF1A", s: "#B26BFF", pInk: "#0D1400", ok: "#2AF9D1", warn: "#FDB34C", err: "#FF5468" },
  ice: { label: "아이스", bg: "#02060C", side: "#040A13", panel: "#06101C", card: "#0A1626", field: "#0F1D31", line: "#18304B", lineHi: "#244667", text: "#E2F3FF", muted: "#8EA9C4", p: "#2DF0FF", s: "#FF4FD8", pInk: "#001418", ok: "#3DFF9A", warn: "#FFD43B", err: "#FF674C" },
  blade: { label: "블레이드", bg: "#09080A", side: "#0D0B0D", panel: "#110E10", card: "#181416", field: "#201A1C", line: "#332A2B", lineHi: "#4A3C3C", text: "#F6ECE6", muted: "#B3A39C", p: "#FF7A1A", s: "#19F0D0", pInk: "#1A0A00", ok: "#9AFC63", warn: "#FFDD44", err: "#FF4989" },
};

function rgba(hex: string, a: number): string {
  const n = parseInt(hex.slice(1), 16);
  return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
}

export function applyPalette(name: PaletteName): void {
  const b = PALETTES[name] ?? PALETTES.seoul;
  const vars: Record<string, string> = {
    bg: b.bg, side: b.side, panel: b.panel, card: b.card, field: b.field, line: b.line, "line-hi": b.lineHi,
    text: b.text, muted: b.muted, p: b.p, s: b.s, "p-ink": b.pInk,
    "p-soft": rgba(b.p, 0.12), "p-line": rgba(b.p, 0.5), "p-halo": rgba(b.p, 0.07), grid: rgba(b.p, 0.045),
    "p-glow": `0 0 0 1px ${rgba(b.p, 0.6)}, 0 0 18px ${rgba(b.p, 0.45)}`,
    "p-text": `0 0 12px ${rgba(b.p, 0.55)}`,
    "s-soft": rgba(b.s, 0.12), "s-line": rgba(b.s, 0.45),
    "icon-fx": `drop-shadow(0 0 4px ${rgba(b.p, 0.8)})`,
    ok: b.ok, warn: b.warn, err: b.err,
    "ok-soft": rgba(b.ok, 0.1), "ok-line": rgba(b.ok, 0.35),
    "warn-soft": rgba(b.warn, 0.1), "warn-line": rgba(b.warn, 0.4),
    "err-soft": rgba(b.err, 0.1), "err-line": rgba(b.err, 0.45),
    // text on a solid status color: the palette's darkest tone keeps 4.5:1 on all three
    "status-ink": b.bg,
  };
  const root = document.documentElement.style;
  for (const [k, v] of Object.entries(vars)) root.setProperty(`--${k}`, v);
  document.documentElement.dataset.palette = name;
}
