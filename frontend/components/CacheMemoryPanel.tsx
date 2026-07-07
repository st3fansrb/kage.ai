import { Cache } from "@/lib/types";
import { T } from "@/lib/tokens";

function Stat({ label, value, hint, accent }: { label: string; value: string; hint?: string; accent?: string }) {
  return (
    <div
      style={{
        background: T.panel,
        border: `1px solid ${T.border2}`,
        borderRadius: 12,
        padding: "11px 13px",
        display: "flex",
        flexDirection: "column",
        gap: 3,
      }}
    >
      <span style={{ fontSize: 9, letterSpacing: ".1em", textTransform: "uppercase", color: T.muted }}>{label}</span>
      <span style={{ fontFamily: T.mono, fontSize: 20, fontWeight: 600, color: accent ?? T.text, lineHeight: 1.1 }}>
        {value}
      </span>
      {hint && <span style={{ fontFamily: T.mono, fontSize: 9.5, color: T.dim }}>{hint}</span>}
    </div>
  );
}

export function CacheMemoryPanel({ cache }: { cache: Cache }) {
  const rate = cache.hitRate == null ? "—" : Math.round(cache.hitRate * 100) + "%";
  const total = cache.hits + cache.misses;

  return (
    <section style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 8, padding: "2px 2px 0" }}>
        <span style={{ fontSize: 10, letterSpacing: ".14em", textTransform: "uppercase", color: T.muted, fontWeight: 500 }}>
          Cache · Memorie
        </span>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
        <Stat
          label="Hit rate"
          value={rate}
          hint={total ? `${cache.hits}/${total} azi` : "fără apeluri azi"}
          accent={cache.hitRate != null && cache.hitRate >= 0.5 ? T.teal : undefined}
        />
        <Stat label="Intrări cache" value={String(cache.entries)} hint="cache semantic" />
        <Stat label="Amintiri" value={String(cache.memories)} hint="memorie de lung termen" />
        <Stat label="Rutare" value={String(cache.routingExamples)} hint="exemple few-shot" />
      </div>
    </section>
  );
}
