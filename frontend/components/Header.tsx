import { Budget } from "@/lib/types";
import { ConnState } from "@/lib/useMissionState";
import { T } from "@/lib/tokens";

export function Header({ budget, conn }: { budget: Budget; conn: ConnState }) {
  const pct = budget.maxUsd > 0 ? Math.min(1, budget.spentUsd / budget.maxUsd) : 0;
  const live = conn === "live";
  return (
    <header
      style={{
        height: 52,
        flex: "none",
        display: "flex",
        alignItems: "center",
        gap: 14,
        padding: "0 20px",
        borderBottom: `1px solid ${T.border}`,
      }}
    >
      <span style={{ fontWeight: 600, fontSize: 15, letterSpacing: ".01em" }}>Mission Control</span>
      <span
        style={{
          display: "flex",
          alignItems: "center",
          gap: 6,
          fontSize: 11,
          color: live ? T.teal : T.orange2,
          fontFamily: T.mono,
        }}
      >
        <span
          style={{
            width: 6,
            height: 6,
            borderRadius: "50%",
            background: live ? T.teal : T.orange2,
            boxShadow: `0 0 9px ${live ? T.teal : T.orange2}`,
            animation: "kpulse 2s ease-in-out infinite",
          }}
        />
        {conn === "live" ? "SSE live" : conn === "connecting" ? "conectare…" : "reconectare…"}
      </span>
      <div style={{ flex: 1 }} />
      <span style={{ fontFamily: T.mono, fontSize: 11, color: T.muted }}>
        cloud <b style={{ color: T.text, fontWeight: 500 }}>{budget.cloudCalls}</b>/{budget.maxCloud}
      </span>
      <div style={{ width: 88, height: 7, borderRadius: 4, background: "rgba(236,225,210,.08)", overflow: "hidden" }}>
        <div
          style={{
            width: `${pct * 100}%`,
            height: "100%",
            background: pct > 0.85 ? T.orange : T.teal,
            boxShadow: `0 0 8px ${pct > 0.85 ? T.orange : T.teal}`,
            transition: "width .4s ease",
          }}
        />
      </div>
      <span style={{ fontFamily: T.mono, fontSize: 11, color: T.muted }}>
        <b style={{ color: T.text, fontWeight: 500 }}>${budget.spentUsd.toFixed(2)}</b> / ${budget.maxUsd.toFixed(2)}
        {budget.spentEur != null && (
          <span style={{ color: T.dim }}> · €{budget.spentEur.toFixed(2)}</span>
        )}
      </span>
    </header>
  );
}
