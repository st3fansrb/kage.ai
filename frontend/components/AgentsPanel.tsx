import { Agent, AgentStatus } from "@/lib/types";
import { T, fmtElapsed, fmtCost } from "@/lib/tokens";

const STATUS_LABEL: Record<AgentStatus, string> = {
  running: "RUNNING",
  pending: "PENDING",
  done: "DONE",
  failed: "FAILED",
};

function statusStyle(s: AgentStatus): React.CSSProperties {
  const base: React.CSSProperties = {
    fontFamily: T.mono,
    fontSize: 9.5,
    letterSpacing: ".07em",
    borderRadius: 7,
    padding: "2px 8px",
    marginLeft: "auto",
    flex: "none",
    border: "1px solid",
  };
  switch (s) {
    case "running":
      return { ...base, color: T.teal, borderColor: "rgba(87,196,187,.4)" };
    case "pending":
      return { ...base, color: T.bg, borderColor: T.orange, background: T.orange };
    case "failed":
      return { ...base, color: T.orange2, borderColor: "rgba(255,106,48,.45)" };
    default:
      return { ...base, color: T.muted, borderColor: "rgba(236,225,210,.16)" };
  }
}

function cardStyle(s: AgentStatus): React.CSSProperties {
  const pending = s === "pending", failed = s === "failed", running = s === "running", done = s === "done";
  return {
    background: pending ? "rgba(255,106,48,.06)" : T.panel,
    border: pending
      ? "1.5px solid rgba(255,106,48,.55)"
      : failed
        ? "1px dashed rgba(255,106,48,.4)"
        : `1px solid ${T.border2}`,
    borderLeft: running ? `3px solid ${T.teal}` : undefined,
    borderRadius: 12,
    padding: 13,
    flex: "none",
    opacity: done ? 0.62 : 1,
    boxShadow: pending ? `${T.cardShadow}, 0 0 24px rgba(255,106,48,.12)` : "none",
  };
}

export function AgentsPanel({ agents, runningCount }: { agents: Agent[]; runningCount: number }) {
  return (
    <section style={{ minHeight: 0, display: "flex", flexDirection: "column", gap: 10, overflowY: "auto", paddingRight: 2 }}>
      <div style={{ flex: "none", display: "flex", alignItems: "baseline", gap: 8, padding: "2px 2px 0" }}>
        <span style={{ fontSize: 10, letterSpacing: ".14em", textTransform: "uppercase", color: T.muted, fontWeight: 500 }}>
          Agenți
        </span>
        <span style={{ fontFamily: T.mono, fontSize: 10, color: T.teal }}>{runningCount} activi</span>
      </div>

      {agents.length === 0 && (
        <div style={{ color: T.dim, fontSize: 12, padding: "18px 4px", fontFamily: T.mono }}>niciun run recent</div>
      )}

      {agents.map((a) => (
        <div key={a.id} style={cardStyle(a.status)}>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            {a.status === "running" && (
              <span
                style={{
                  width: 7,
                  height: 7,
                  borderRadius: "50%",
                  background: T.teal,
                  boxShadow: `0 0 8px ${T.teal}`,
                  animation: "kpulse 1.4s ease-in-out infinite",
                  flex: "none",
                }}
              />
            )}
            {a.status === "done" && <span style={{ color: T.green, fontSize: 14, flex: "none" }}>✓</span>}
            {a.status === "failed" && <span style={{ color: T.orange2, fontSize: 14, flex: "none" }}>✕</span>}
            <span style={{ fontWeight: 600, fontSize: 13, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
              {a.name}
            </span>
            <span style={statusStyle(a.status)}>{STATUS_LABEL[a.status]}</span>
          </div>
          <div
            style={{
              fontFamily: T.mono,
              fontSize: 10.5,
              color: T.muted,
              marginTop: 6,
              whiteSpace: "nowrap",
              overflow: "hidden",
              textOverflow: "ellipsis",
            }}
          >
            {a.note}
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 10, marginTop: 8, fontFamily: T.mono, fontSize: 10, color: T.dim }}>
            <span>{fmtElapsed(a.elapsedMs)}</span>
            <span>·</span>
            <span>{fmtCost(a.costUsd)}</span>
          </div>
        </div>
      ))}
    </section>
  );
}
