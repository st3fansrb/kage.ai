"use client";

import { useState } from "react";
import { Agent, AgentStatus } from "@/lib/types";
import { stopAll } from "@/lib/actions";
import { duration, minutesOf, usd } from "@/lib/format";

const LABEL: Record<AgentStatus, [string, string]> = {
  running: ["run", "lucrează"],
  pending: ["", "așteaptă"],
  failed: ["neg", "eșuat"],
  done: ["done", "gata"],
};

function elapsed(a: Agent, nowMin: number): string {
  if (a.status === "running" && a.startedAt) return duration(Math.max(0, nowMin - minutesOf(a.startedAt)) * 60000);
  return duration(a.elapsedMs);
}

function Row({ a, nowMin, hasApprovals }: { a: Agent; nowMin: number; hasApprovals: boolean }) {
  const [cls, txt] = LABEL[a.status];
  const extra = a.note && a.note.trim() !== a.name.trim() ? a.note : "";
  const sub = [a.kind, a.status === "pending" && hasApprovals ? "oprit până decizi mai sus" : "", extra].filter(Boolean).join(" · ");
  return (
    <div className="row">
      <span className="st"><span className={`pill ${cls}`}>{txt}</span></span>
      <div className="who" style={{ minWidth: 0 }}>
        <div className="name">{a.name}</div>
        {sub && <div className="sub">{sub}</div>}
      </div>
      <span className="t">{elapsed(a, nowMin)}</span>
      <span className="c">{usd(a.costUsd)}</span>
    </div>
  );
}

export function Agents({ agents, nowMin, offline, hasApprovals, onNewTask, nextScheduled }: {
  agents: Agent[];
  nowMin: number;
  offline: boolean;
  hasApprovals: boolean;
  onNewTask: () => void;
  nextScheduled?: { at: string; message: string };
}) {
  const [armed, setArmed] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [stopErr, setStopErr] = useState(false);
  const order: AgentStatus[] = ["pending", "running", "failed"];
  const active = agents.filter((a) => a.status !== "done").sort((x, y) => order.indexOf(x.status) - order.indexOf(y.status));
  const done = agents.filter((a) => a.status === "done");
  const running = agents.filter((a) => a.status === "running").length;
  const failed = agents.filter((a) => a.status === "failed").length;

  async function onStop() {
    if (!armed) {
      setArmed(true);
      setTimeout(() => setArmed(false), 4000);
      return;
    }
    setStopping(true);
    setStopErr(false);
    const ok = await stopAll();
    setStopping(false);
    setArmed(false);
    if (!ok) setStopErr(true);
  }

  return (
    <section aria-labelledby="h-ag">
      <div className="sh">
        <h2 id="h-ag">Agenți</h2>
        {agents.length > 0 && (
          <span className="meta num">{running} lucrează{failed ? ` · ${failed} eșuat${failed > 1 ? "e" : ""}` : ""}</span>
        )}
        <div className="right">
          {running > 0 && !offline && (
            <button className="link danger" onClick={onStop} disabled={stopping}>
              {stopping ? "Se opresc…" : armed ? `Sigur? Oprește ${running === 1 ? "agentul" : `${running} agenți`} și pauzează programările` : "Oprește tot"}
            </button>
          )}
        </div>
      </div>
      {stopErr && <p className="err" role="alert" style={{ marginBottom: 10 }}>Oprirea n-a ajuns la Kage. Agenții încă rulează; încearcă din nou.</p>}

      {agents.length === 0 ? (
        <div className="none">
          <span className="kanji" lang="ja" aria-hidden="true">影</span>
          <span>
            Niciun agent nu lucrează acum.
            {nextScheduled && <> Următorul pornește la <span className="num">{nextScheduled.at}</span>: {nextScheduled.message}.</>}{" "}
            <button className="link" onClick={onNewTask}>Pornește un task</button>
          </span>
        </div>
      ) : (
        <div className="rows">
          {active.map((a) => <Row key={a.id} a={a} nowMin={nowMin} hasApprovals={hasApprovals} />)}
          {done.length > 0 && (
            <details className={active.length ? "more" : ""} style={active.length ? undefined : { padding: "12px 0" }}>
              <summary className="link">{done.length === 1 ? "1 terminat recent" : `${done.length} terminați recent`}</summary>
              {done.map((a) => <Row key={a.id} a={a} nowMin={nowMin} hasApprovals={false} />)}
            </details>
          )}
        </div>
      )}
    </section>
  );
}
