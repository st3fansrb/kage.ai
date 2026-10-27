"use client";

import { useState } from "react";
import { ActivityRow } from "@/lib/types";
import { clock, isCloudTier, usd } from "@/lib/format";
import { t } from "@/lib/i18n";

export function Requests({ rows }: { rows: ActivityRow[] }) {
  const [all, setAll] = useState(false);
  const shown = all ? rows : rows.slice(0, 6);
  return (
    <section aria-labelledby="h-rq">
      <div className="sh">
        <h2 id="h-rq">{t("Ultimele cereri", "Latest requests")}</h2>
        {rows.length > 0 && <span className="meta num">{rows.length} {t("recente", "recent")}</span>}
      </div>
      <div className="tbl">
        {rows.length === 0 ? (
          <p className="quiet" style={{ margin: "0 0 14px" }}>{t("Nicio cerere încă.", "No requests yet.")}</p>
        ) : (
          <>
            <table>
              <thead>
                <tr><th>{t("Ora", "Time")}</th><th>{t("Canal", "Channel")}</th><th>{t("Cerere", "Request")}</th><th>{t("Rutare", "Routed to")}</th><th className="r">{t("Cost", "Cost")}</th></tr>
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
                  {all ? t("Arată doar ultimele 6", "Show only the latest 6") : `${t("Arată toate cele", "Show all")} ${rows.length}`}
                </button>
              </div>
            )}
          </>
        )}
      </div>
    </section>
  );
}
