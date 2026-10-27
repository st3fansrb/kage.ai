"use client";

import { useState } from "react";
import { Approval } from "@/lib/types";
import { respondApproval } from "@/lib/actions";
import { clock, riskLabel } from "@/lib/format";

type Outcome = { id: string; ok: boolean; agent: string; at: string };

export function Decisions({ approvals, offline }: { approvals: Approval[]; offline: boolean }) {
  const [busy, setBusy] = useState<Record<string, boolean>>({});
  const [failed, setFailed] = useState<Record<string, boolean>>({});
  const [done, setDone] = useState<Outcome[]>([]);

  async function decide(ap: Approval, ok: boolean) {
    setBusy((b) => ({ ...b, [ap.id]: true }));
    setFailed((f) => ({ ...f, [ap.id]: false }));
    const sent = await respondApproval(ap.id, ok ? "confirm" : "block");
    setBusy((b) => ({ ...b, [ap.id]: false }));
    if (sent) setDone((d) => [...d, { id: ap.id, ok, agent: ap.agent, at: clock(new Date().toTimeString()) }]);
    else setFailed((f) => ({ ...f, [ap.id]: true }));
  }

  // Ascunde imediat ce a fost decis; snapshot-ul următor confirmă.
  const visible = approvals.filter((ap) => !done.some((d) => d.id === ap.id));
  const recent = done.slice(-3);

  return (
    <section aria-labelledby="h-dec" id="decizii">
      <div className="sh">
        <h2 id="h-dec">Așteaptă decizia ta</h2>
        {visible.length > 0 && <span className="meta num" aria-live="polite">{visible.length}</span>}
      </div>

      {recent.map((d) => (
        <div key={d.id} className="none" role="status" style={{ borderStyle: "solid", marginBottom: 10 }}>
          <span className={`pill ${d.ok ? "pos" : "neg"}`}>{d.ok ? "aprobat" : "refuzat"}</span>
          <span>{d.agent} · la {d.at}</span>
        </div>
      ))}

      {visible.map((ap) => {
        const risk = riskLabel(ap.risk);
        return (
          <article key={ap.id} className="approval" aria-label={`Aprobare pentru ${ap.agent}`}>
            <div className="who">
              <span className={`pill ${risk.danger ? "neg" : ""}`}>{risk.text}</span>
              <strong>{ap.agent || "agent"}</strong>
              <span className="quiet">vrea să ruleze:</span>
            </div>
            <pre>{ap.cmd}</pre>
            {ap.why && <p className="why">{ap.why}</p>}
            {failed[ap.id] && <p className="err" role="alert">Kage n-a primit decizia. Verifică legătura și încearcă din nou.</p>}
            {offline && <p className="err">Nu poți decide cât timp nu am legătură cu Kage.</p>}
            <div className="decide">
              <button className="btn" disabled={offline || busy[ap.id]} onClick={() => decide(ap, false)}>
                Refuză
              </button>
              <button className="btn primary" disabled={offline || busy[ap.id]} onClick={() => decide(ap, true)}>
                {busy[ap.id] ? "Se trimite…" : "Aprobă"}
              </button>
            </div>
          </article>
        );
      })}

      {visible.length === 0 && done.length === 0 && (
        <div className="none">
          <span className="kanji" lang="ja" aria-hidden="true">影</span>
          <span>Nimic de aprobat. Când un agent vrea să ruleze o comandă riscantă, apare aici.</span>
        </div>
      )}
    </section>
  );
}
