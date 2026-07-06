// Design tokens derivate din mockup-urile Claude Design (Kage Mission Control / Kage Mobile).
export const T = {
  bg: "#0d0b09",
  panel: "#14110d",
  rail: "#0b0907",
  inset: "#0b0907",
  text: "#ece9e4",
  soft: "#d8d2c8",
  muted: "#948d82",
  dim: "#5f594f",
  soft2: "#b5aea3",
  orange: "#ff6a30",
  orange2: "#ff8a57",
  teal: "#57c4bb",
  green: "#8fae5e",
  border: "rgba(236,225,210,.08)",
  border2: "rgba(236,225,210,.09)",
  border3: "rgba(236,225,210,.16)",
  orangeLine: "rgba(255,106,48,.5)",
  orangeTint: "rgba(255,106,48,.08)",
  cardShadow: "5px 5px 0 -1px #070605",
  mono: "'JetBrains Mono', ui-monospace, monospace",
  sans: "'Space Grotesk', system-ui, sans-serif",
} as const;

export function fmtElapsed(ms: number | null): string {
  if (ms == null) return "—";
  const sec = Math.floor(ms / 1000);
  if (sec < 60) return sec + "s";
  return Math.floor(sec / 60) + "m " + String(sec % 60).padStart(2, "0") + "s";
}

export function fmtCost(usd: number | null): string {
  if (usd == null || usd === 0) return "$0";
  return "$" + usd.toFixed(2);
}

export function fmtTime(ts: string): string {
  // ISO → HH:MM:SS
  const m = ts.match(/T(\d{2}:\d{2}:\d{2})/);
  return m ? m[1] : ts.slice(0, 8);
}
