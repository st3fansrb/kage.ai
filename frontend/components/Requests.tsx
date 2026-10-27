"use client";

import { useState } from "react";
import { ActivityRow } from "@/lib/types";
import { clock, isCloudTier, usd } from "@/lib/format";

export function Requests({ rows }: { rows: ActivityRow[] }) {
  const [all, setAll] = useState(false);
  const shown = all ? rows : rows.slice(0, 6);
  return (
    <section aria-labelledby="h-rq">
      <div className="sh">
        <h2 id="h-rq">Ultimele cereri</h2>
        {rows.length > 0 && <span className="meta num">{rows.length} recente</span>}
      </div>
      <div className="tbl">
        {rows.length === 0 ? (
          <p className="quiet" style={{ margin: "0 0 14px" }}>Nicio cerere încă.</p>
        ) : (
          <>
            <table>
              <thead>
                <tr><th>Ora</th><th>Canal</th><th>Cerere</th><th>Rutare</th><th className="r">Cost</th></tr>
              </thead>
              <tbody>
                {shown.map((r) => {
                  const cloud = isCloudTier(r.tier);
                  return (
                    <tr key={r.id}>
                      <td className="time num">{clock(r.ts)}</td>
                      <td className="ch quiet">{r.channel}</td>
                      <td className="q" title={r.text}>{r.text}</td>
                      <td className="rt"><span className={`route ${cloud ? "cloud" : ""}`}>{[r.tier, r.meta].filter(Boolean).join(" · ")}</span></td>
                      <td className={`r cost ${r.costUsd ? "paid" : ""}`}>{usd(r.costUsd)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            {rows.length > 6 && (
              <div className="tbl-foot">
                <button className="link" onClick={() => setAll((a) => !a)} aria-expanded={all}>
                  {all ? "Arată doar ultimele 6" : `Arată toate cele ${rows.length}`}
                </button>
              </div>
            )}
          </>
        )}
      </div>
    </section>
  );
}
