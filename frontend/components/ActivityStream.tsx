import { ActivityRow, AgentStatus } from "@/lib/types";
import { T, fmtTime, fmtCost } from "@/lib/tokens";

function tierStyle(tier: string): React.CSSProperties {
  const cloud = tier === "T3" || tier === "T4" || tier === "T5" || tier === "T6";
  return {
    fontFamily: T.mono,
    fontSize: 9.5,
    fontWeight: 600,
    color: cloud ? T.orange2 : T.teal,
    border: `1px solid ${cloud ? "rgba(255,106,48,.4)" : "rgba(87,196,187,.35)"}`,
    borderRadius: 6,
    padding: "1px 6px",
    flex: "none",
    width: 30,
    textAlign: "center",
  };
}

function dot(s: AgentStatus): string {
  return s === "running" ? T.teal : s === "failed" ? T.orange2 : s === "pending" ? T.orange : T.dim;
}

export function ActivityStream({ events }: { events: ActivityRow[] }) {
  return (
    <section
      style={{
        minHeight: 0,
        display: "flex",
        flexDirection: "column",
        background: T.panel,
        border: `1px solid ${T.border2}`,
        borderRadius: 10,
        overflow: "hidden",
      }}
    >
      <div style={{ flex: "none", display: "flex", alignItems: "center", gap: 6, padding: "10px 12px", borderBottom: `1px solid ${T.border}` }}>
        <span style={{ fontSize: 10, letterSpacing: ".14em", textTransform: "uppercase", color: T.muted, fontWeight: 500 }}>
          Activity stream
        </span>
        <div style={{ flex: 1 }} />
        <span style={{ fontFamily: T.mono, fontSize: 10, color: T.dim }}>{events.length}</span>
      </div>
      <div style={{ flex: 1, overflowY: "auto", padding: "4px 0" }}>
        {events.length === 0 && (
          <div style={{ color: T.dim, fontSize: 12, padding: "18px 14px", fontFamily: T.mono }}>fără activitate azi</div>
        )}
        {events.map((ev) => (
          <div
            key={ev.id}
            style={{
              display: "flex",
              alignItems: "center",
              gap: 8,
              padding: "6px 12px",
              borderBottom: "1px solid rgba(236,225,210,.04)",
            }}
          >
            <span style={{ fontFamily: T.mono, fontSize: 10, color: T.dim, flex: "none" }}>{fmtTime(ev.ts)}</span>
            <span style={tierStyle(ev.tier)}>{ev.tier}</span>
            <span style={{ fontFamily: T.mono, fontSize: 10, color: T.muted, width: 44, flex: "none", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
              {ev.channel}
            </span>
            <span style={{ flex: 1, minWidth: 0, fontSize: 12.5, color: T.soft, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
              {ev.text} <span style={{ fontFamily: T.mono, fontSize: 10, color: T.muted }}>{ev.meta}</span>
            </span>
            <span style={{ width: 7, height: 7, borderRadius: "50%", background: dot(ev.status), flex: "none" }} />
            <span style={{ fontFamily: T.mono, fontSize: 10, color: T.dim, flex: "none" }}>{fmtCost(ev.costUsd)}</span>
          </div>
        ))}
      </div>
    </section>
  );
}
