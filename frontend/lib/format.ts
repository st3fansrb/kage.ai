// Formatări comune pentru ecranul „Astăzi”.

import { LOCALE, t } from "./i18n";

export function usd(v: number | null | undefined): string {
  if (v == null || v === 0) return "$0";
  return "$" + v.toFixed(2);
}

// Minute de la miezul nopții pentru un ISO sau un „HH:MM”.
export function minutesOf(ts: string): number {
  const m = ts.match(/(?:T|^)(\d{2}):(\d{2})/);
  return m ? Number(m[1]) * 60 + Number(m[2]) : 0;
}

export function clock(ts: string): string {
  const m = ts.match(/(?:T|^)(\d{2}:\d{2})/);
  return m ? m[1] : ts.slice(0, 5);
}

export function hhmm(minutes: number): string {
  const h = Math.floor(minutes / 60) % 24;
  const m = Math.round(minutes % 60);
  return String(h).padStart(2, "0") + ":" + String(m).padStart(2, "0");
}

export function duration(ms: number | null | undefined): string {
  if (ms == null) return "—";
  const min = Math.floor(ms / 60000);
  if (min < 1) return Math.max(1, Math.round(ms / 1000)) + " s";
  if (min < 60) return min + " min";
  return Math.floor(min / 60) + " h " + String(min % 60).padStart(2, "0") + " min";
}

export function nowMinutes(d = new Date()): number {
  return d.getHours() * 60 + d.getMinutes();
}

// Tier-urile T3+ merg în cloud și costă; T1–T2 (și cache) rămân locale.
export function isCloudTier(tier: string): boolean {
  const n = Number(tier.replace(/^T/, ""));
  return Number.isFinite(n) && n >= 3;
}

const DAY = new Intl.DateTimeFormat(LOCALE, { weekday: "long", day: "numeric", month: "long" });
export function todayLabel(d = new Date()): string {
  return DAY.format(d);
}

export function riskLabel(risk: string): { text: string; danger: boolean } {
  const r = risk.toUpperCase();
  if (r === "DESTRUCTIV") return { text: t("destructiv", "destructive"), danger: true };
  if (r === "FILESYSTEM") return { text: t("scrie fișiere", "writes files"), danger: false };
  if (r === "GIT") return { text: "git", danger: false };
  return { text: t("comandă", "command"), danger: false };
}
