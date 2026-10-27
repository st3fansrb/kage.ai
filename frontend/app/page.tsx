"use client";

import { useEffect, useState } from "react";
import { useMissionState } from "@/lib/useMissionState";
import { hhmm, minutesOf, nowMinutes, todayLabel, usd } from "@/lib/format";
import { Decisions } from "@/components/Decisions";
import { Agents } from "@/components/Agents";
import { Budget, runsOutAt, spendPointsOf } from "@/components/Budget";
import { Schedule } from "@/components/Schedule";
import { Requests } from "@/components/Requests";
import { ChatPanel } from "@/components/ChatPanel";
import { TaskPanel } from "@/components/TaskPanel";

function capitalize(s: string): string {
  return s.charAt(0).toUpperCase() + s.slice(1);
}

// Ecranul „Astăzi”: ce așteaptă după tine, ce lucrează acum, cât s-a cheltuit.
export default function Today() {
  const { state, conn, retry } = useMissionState();
  const [chatOpen, setChatOpen] = useState(false);
  const [taskOpen, setTaskOpen] = useState(false);
  const [now, setNow] = useState<Date | null>(null);
  const [scrolled, setScrolled] = useState(false);

  // Ceasul paginii: un tic la 30 s ajunge pentru minute și „peste X”.
  useEffect(() => {
    setNow(new Date());
    const id = setInterval(() => setNow(new Date()), 30000);
    return () => clearInterval(id);
  }, []);

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 4);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (!(e.metaKey || e.ctrlKey)) return;
      const k = e.key.toLowerCase();
      if (k === "j") {
        e.preventDefault();
        setTaskOpen(false);
        setChatOpen((o) => !o);
      } else if (k === "k") {
        e.preventDefault();
        setChatOpen(false);
        setTaskOpen((o) => !o);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const offline = conn.state === "error";
  const { approvals, agents, budget, activity, briefing } = state;

  useEffect(() => {
    document.body.classList.toggle("stale", offline && conn.hasData);
  }, [offline, conn.hasData]);

  useEffect(() => {
    document.title = (approvals.length && !offline ? `(${approvals.length}) ` : "") + "Kage · Astăzi";
  }, [approvals.length, offline]);

  const nowMin = now ? nowMinutes(now) : 0;
  const running = agents.filter((a) => a.status === "running").length;
  const failed = agents.filter((a) => a.status === "failed").length;
  const tasks = briefing.tasksToday ?? [];
  const nextScheduled = tasks.find((t) => minutesOf(t.at) > nowMin);
  const since = conn.since ? hhmm(nowMinutes(conn.since)) : null;

  let finding: React.ReactNode = null;
  if (offline) {
    finding = (
      <p className="finding off">
        {conn.hasData
          ? `Nu am legătură cu Kage din ${since}. Ce vezi mai jos e starea de atunci.`
          : "Nu am legătură cu Kage, așa că nu am ce arăta încă."}
      </p>
    );
  } else if (conn.hasData && now) {
    const bits: React.ReactNode[] = [];
    if (approvals.length) bits.push(<><strong>{approvals[0].agent}</strong> așteaptă să aprobi o comandă</>);
    if (running) bits.push(running === 1 ? "un agent lucrează" : `${running} agenți lucrează`);
    if (failed) bits.push(failed === 1 ? "unul a eșuat" : `${failed} au eșuat`);
    const pct = budget.maxUsd > 0 ? Math.round((budget.spentUsd / budget.maxUsd) * 100) : 0;
    const runsOut = runsOutAt(spendPointsOf(budget, activity), budget.spentUsd, budget.maxUsd, nowMin);
    finding = (
      <p className="finding">
        {bits.length === 0
          ? "Nimic nu așteaptă după tine și niciun agent nu lucrează."
          : bits.map((b, i) => (
              <span key={i}>
                {i === 0 && typeof b === "string" ? capitalize(b) : b}
                {i < bits.length - 1 ? ", " : "."}
              </span>
            ))}{" "}
        Ai cheltuit <strong>{usd(budget.spentUsd)}</strong> din {usd(budget.maxUsd)} ({pct}%)
        {runsOut ? <>; în ritmul de azi, bugetul se termină pe la <strong>{runsOut}</strong>.</> : "."}
      </p>
    );
  }

  const connCls = offline ? "conn off" : conn.state === "connecting" ? "conn wait" : "conn";
  const connText = offline ? (since ? `deconectat din ${since}` : "deconectat") : conn.state === "connecting" ? "se conectează" : "conectat";

  return (
    <>
      <header className={`bar${scrolled ? " scrolled" : ""}`}>
        <div className="wrap">
          <a className="mark" href="/" aria-label="Kage, pagina Astăzi"><b lang="ja">影</b> kage</a>
          <div className={connCls} role="status"><span className="dot" /><span>{connText}</span></div>
        </div>
      </header>

      <main className="wrap">
        <div className="title">
          <div>
            <h1>Astăzi <span className="date">{now ? todayLabel(now) : ""}</span></h1>
            {finding}
          </div>
          <div className="actions">
            <button className="btn" onClick={() => setChatOpen(true)}>Chat <kbd>⌘J</kbd></button>
            <button className="btn primary" onClick={() => setTaskOpen(true)}>Task nou <kbd>⌘K</kbd></button>
          </div>
        </div>

        {offline && (
          <div className="notice" role="alert">
            <p>{conn.cause ? capitalize(conn.cause) + "." : "Verific de ce."} <span>Reîncerc automat la 5 s.</span></p>
            <button className="link" onClick={retry}>Reîncearcă acum</button>
          </div>
        )}

        {conn.hasData && (
          <>
            <Decisions approvals={approvals} offline={offline} />
            <Agents
              agents={agents}
              nowMin={nowMin}
              offline={offline}
              hasApprovals={approvals.length > 0}
              onNewTask={() => setTaskOpen(true)}
              nextScheduled={nextScheduled}
            />
            <div className="split">
              <Budget budget={budget} activity={activity} nowMin={nowMin} staleSince={offline ? since : null} />
              <Schedule tasks={tasks} nowMin={nowMin} onNewTask={() => setTaskOpen(true)} />
            </div>
            <Requests rows={activity} />
          </>
        )}
      </main>

      <ChatPanel open={chatOpen} onClose={() => setChatOpen(false)} />
      <TaskPanel open={taskOpen} onClose={() => setTaskOpen(false)} />
    </>
  );
}
