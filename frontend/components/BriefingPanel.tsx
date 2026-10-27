import { Briefing } from "@/lib/types";
import { T } from "@/lib/tokens";

export function BriefingPanel({ briefing }: { briefing: Briefing }) {
  const { tasksToday } = briefing;
  const empty = tasksToday.length === 0;

  return (
    <section style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 8, padding: "2px 2px 0" }}>
        <span style={{ fontSize: 10, letterSpacing: ".14em", textTransform: "uppercase", color: T.muted, fontWeight: 500 }}>
          Briefing
        </span>
      </div>

      {empty && (
        <div style={{ color: T.dim, fontSize: 12, padding: "14px 4px", fontFamily: T.mono }}>
          nimic programat azi
        </div>
      )}

      {tasksToday.length > 0 && (
        <div
          style={{
            background: T.panel,
            border: `1px solid ${T.border2}`,
            borderRadius: 12,
            padding: "11px 13px",
            display: "flex",
            flexDirection: "column",
            gap: 8,
          }}
        >
          <span style={{ fontSize: 9, letterSpacing: ".1em", textTransform: "uppercase", color: T.muted }}>Agenți programați azi</span>
          {tasksToday.map((t, i) => (
            <div key={i} style={{ display: "flex", alignItems: "baseline", gap: 9 }}>
              <span style={{ fontFamily: T.mono, fontSize: 11, color: T.orange2, flex: "none" }}>{t.at}</span>
              <span style={{ fontSize: 11.5, color: T.soft, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                {t.message}
              </span>
            </div>
          ))}
        </div>
      )}

    </section>
  );
}
