"use client";

import { useEffect, useRef, useState } from "react";
import { ActivityRow, Budget as BudgetT, SpendPoint } from "@/lib/types";
import { hhmm, isCloudTier, minutesOf, usd } from "@/lib/format";

// Ora la care se termină bugetul în ritmul de azi (de la primul apel plătit până acum).
export function runsOutAt(points: SpendPoint[], spent: number, max: number, nowMin: number): string | null {
  if (points.length < 2 || spent <= 0 || spent >= max) return null;
  const span = nowMin - minutesOf(points[0].ts);
  if (span < 15) return null; // prea devreme pentru o estimare cinstită
  const at = nowMin + (max - spent) / (spent / span);
  return at < 24 * 60 ? hhmm(at) : null;
}

// Backend mai vechi fără spendPoints: cade pe run-urile plătite din activitate.
export function spendPointsOf(budget: BudgetT, activity: ActivityRow[]): SpendPoint[] {
  return budget.spendPoints ?? activity
    .filter((r) => (r.costUsd ?? 0) > 0)
    .map((r) => ({ ts: r.ts, costUsd: r.costUsd as number }))
    .reverse();
}

function SpendChart({ points, locals, max, nowMin, runsOut }: {
  points: SpendPoint[];
  locals: string[];
  max: number;
  nowMin: number;
  runsOut: string | null;
}) {
  const host = useRef<HTMLDivElement>(null);
  const [W, setW] = useState(640);
  const [tip, setTip] = useState<{ x: number; y: number; text: string } | null>(null);

  useEffect(() => {
    const el = host.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setW(Math.max(280, el.clientWidth)));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const H = 168, top = 18, base = 128, right = W - 44;
  const runM = runsOut ? minutesOf(runsOut) : null;
  const first = points.length ? minutesOf(points[0].ts) : nowMin - 120;
  const x0 = Math.max(0, Math.floor((first - 45) / 60) * 60);
  const x1 = Math.min(1440, Math.ceil((Math.max(runM ?? nowMin, nowMin) + 45) / 60) * 60);
  const X = (m: number) => ((m - x0) / Math.max(1, x1 - x0)) * right;
  const Y = (v: number) => base - (Math.min(v, max) / max) * (base - top);

  let acc = 0;
  let d = `M${X(x0)},${Y(0)}`;
  const dots = points.map((p) => {
    const xm = X(minutesOf(p.ts));
    acc += p.costUsd;
    d += ` H${xm} V${Y(acc)}`;
    return { x: xm, y: Y(acc), text: `${p.ts.slice(11, 16)} · ${usd(p.costUsd)}, total ${usd(acc)}` };
  });
  d += ` H${X(nowMin)}`;
  const hours: number[] = [];
  for (let m = x0; m <= x1; m += 60) hours.push(m);
  // Cel puțin ~56px între etichetele de oră, ca să nu se suprapună pe telefon.
  const step = Math.max(1, Math.ceil(56 / (right / Math.max(1, hours.length - 1))));
  // Eticheta „acum” stă în dreapta liniei (zona de sub proiecție e mereu goală) când e loc.
  const nowRight = right - X(nowMin) > 120;

  return (
    <div className="spend" ref={host}>
      <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`} role="img"
        aria-label={`Cheltuiala cumulată azi: ${usd(acc)} din ${usd(max)}${runsOut ? `, estimat să atingă limita pe la ${runsOut}` : ""}`}>
        <line x1={0} x2={right} y1={Y(max)} y2={Y(max)} stroke="var(--control)" strokeDasharray="2 4" />
        <text x={right + 6} y={Y(max) + 4}>{usd(max)}</text>
        <line x1={0} x2={right} y1={base + 0.5} y2={base + 0.5} stroke="var(--border)" />
        <text x={right + 6} y={base + 4}>$0</text>
        <path d={d} fill="none" stroke="var(--data)" strokeWidth={2} strokeLinejoin="round" />
        {dots.map((p, i) => (
          <g key={i}>
            <circle cx={p.x} cy={p.y} r={2.5} fill="var(--data)" />
            <circle cx={p.x} cy={p.y} r={10} fill="transparent"
              onPointerEnter={(e) => setTip({ x: e.clientX, y: e.clientY, text: p.text })}
              onPointerLeave={() => setTip(null)} />
          </g>
        ))}
        {runM != null && runM <= x1 && (
          <>
            <line x1={X(nowMin)} y1={Y(acc)} x2={X(runM)} y2={Y(max)} stroke="var(--data)" strokeWidth={1.5} strokeDasharray="4 4" />
            <circle cx={X(runM)} cy={Y(max)} r={3.5} fill="var(--data)" />
            <text x={X(runM)} y={Y(max) - 7} textAnchor="middle" className="em">se termină ~{runsOut}</text>
          </>
        )}
        {locals.filter((t) => minutesOf(t) >= x0).map((t, i) => (
          <rect key={i} x={X(minutesOf(t)) - 1} y={base + 6} width={2} height={8} rx={1} fill="var(--local-mark)" />
        ))}
        <g className="now">
          <line x1={X(nowMin)} x2={X(nowMin)} y1={top - 6} y2={base} stroke="var(--text)" strokeOpacity={0.5} />
          <text x={X(nowMin) + (nowRight ? 6 : -6)} y={base - 6} textAnchor={nowRight ? "start" : "end"}>acum {hhmm(nowMin)} · {usd(acc)}</text>
        </g>
        {hours.filter((_, i) => i % step === 0).map((m) => (
          <text key={m} x={X(m)} y={H - 8} textAnchor={m === x0 ? "start" : "middle"}>{hhmm(m)}</text>
        ))}
      </svg>
      {tip && <div className="tip" style={{ left: Math.min(tip.x + 12, window.innerWidth - 270), top: tip.y - 40 }}>{tip.text}</div>}
    </div>
  );
}

export function Budget({ budget, activity, nowMin, staleSince }: {
  budget: BudgetT;
  activity: ActivityRow[];
  nowMin: number;
  staleSince: string | null;
}) {
  const n = new Date();
  const today = `${n.getFullYear()}-${String(n.getMonth() + 1).padStart(2, "0")}-${String(n.getDate()).padStart(2, "0")}`;
  const points = spendPointsOf(budget, activity);
  const locals = activity.filter((r) => !isCloudTier(r.tier) && r.ts.startsWith(today)).map((r) => r.ts);
  const pct = budget.maxUsd > 0 ? Math.round((budget.spentUsd / budget.maxUsd) * 100) : 0;
  const runsOut = runsOutAt(points, budget.spentUsd, budget.maxUsd, nowMin);

  return (
    <section aria-labelledby="h-bud">
      <div className="sh">
        <h2 id="h-bud">Bugetul zilei</h2>
        <span className="meta">{staleSince ? `date de la ${staleSince}` : "se resetează la 00:00"}</span>
      </div>
      <div className="card">
        <div className="kpis">
          <div className="kpi"><div className="l">Cheltuit</div><div className="v">{usd(budget.spentUsd)}<small>din {usd(budget.maxUsd)}, {pct}%</small></div></div>
          <div className="kpi"><div className="l">Apeluri cloud</div><div className="v">{budget.cloudCalls}<small>din {budget.maxCloud}</small></div></div>
          {budget.localCalls != null && (
            <div className="kpi"><div className="l">Rezolvate local</div><div className="v local">{budget.localCalls}<small>gratuit</small></div></div>
          )}
        </div>
        <div className="legend">
          <span className="key"><i />cheltuit, cumulat</span>
          <span className="key"><i className="loc" />cerere locală, gratuită</span>
        </div>
        <SpendChart points={points} locals={locals} max={budget.maxUsd} nowMin={nowMin} runsOut={runsOut} />
      </div>
    </section>
  );
}
