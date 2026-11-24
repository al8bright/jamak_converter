// Glossary text format shared by the options screen and the glossary editor (mirrors backend core/glossary.py):
// one entry per line, `ja=ko`, `ja=ko # note` or `ja<TAB>ko<TAB>note`; a leading `*` marks a recognition hint.

import type { GlossaryEntry } from "./api";

export function parseGlossaryText(text: string): GlossaryEntry[] {
  const out: GlossaryEntry[] = [];
  for (const raw of text.split(/\r?\n/)) {
    let line = raw.trim().replace(/^﻿/, "");
    if (!line || line.startsWith("#")) continue;
    const hint = line.startsWith("*");
    line = line.replace(/^\*+/, "").trim();
    let ja: string, ko: string, note = "";
    if (line.includes("\t")) {
      [ja, ko = "", note = ""] = line.split("\t").map((p) => p.trim());
    } else if (line.includes("=")) {
      const i = line.indexOf("=");
      ja = line.slice(0, i);
      const rest = line.slice(i + 1);
      const j = rest.indexOf(" #");
      ko = j >= 0 ? rest.slice(0, j) : rest;
      note = j >= 0 ? rest.slice(j + 2) : "";
    } else continue;
    ja = ja.trim();
    ko = ko.trim();
    if (ja && ko) out.push({ ja, ko, note: note.trim(), hint });
  }
  return out;
}

export function glossaryText(entries: GlossaryEntry[]): string {
  return entries.map((e) => `${e.hint ? "*" : ""}${e.ja}=${e.ko}${e.note ? ` # ${e.note}` : ""}`).join("\n");
}

/** Later entries with the same Japanese replace earlier ones (same as the backend merge). */
export function mergeEntries(...lists: GlossaryEntry[][]): GlossaryEntry[] {
  const by = new Map<string, GlossaryEntry>();
  for (const l of lists) for (const e of l) by.set(e.ja, e);
  return [...by.values()];
}

export function sameEntries(a: GlossaryEntry[], b: GlossaryEntry[]): boolean {
  if (a.length !== b.length) return false;
  return a.every((e, i) => e.ja === b[i].ja && e.ko === b[i].ko && e.note === b[i].note && !!e.hint === !!b[i].hint);
}
