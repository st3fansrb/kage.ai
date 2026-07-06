"use client";

import { useMissionState } from "@/lib/useMissionState";
import { Header } from "@/components/Header";
import { AgentsPanel } from "@/components/AgentsPanel";
import { ApprovalsPanel } from "@/components/ApprovalsPanel";
import { ActivityStream } from "@/components/ActivityStream";
import { T } from "@/lib/tokens";

export default function MissionControl() {
  const { state, conn } = useMissionState();

  return (
    <div style={{ display: "flex", height: "100vh", width: "100%", background: T.bg, color: T.text, overflow: "hidden" }}>
      {/* icon rail */}
      <nav
        style={{
          width: 64,
          flex: "none",
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          padding: "14px 0",
          borderRight: `1px solid ${T.border}`,
          background: T.rail,
        }}
      >
        <div style={{ fontSize: 20, fontWeight: 700, color: T.text, textShadow: "3px 3px 0 rgba(255,106,48,.25)" }}>影</div>
      </nav>

      {/* main */}
      <main style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column" }}>
        <Header budget={state.budget} conn={conn} runningCount={state.runningCount} />

        <div
          style={{
            flex: 1,
            minHeight: 0,
            display: "grid",
            gridTemplateColumns: "1.5fr 1fr .92fr",
            gap: 12,
            padding: "14px 16px",
          }}
        >
          <ActivityStream events={state.activity} />
          <AgentsPanel agents={state.agents} runningCount={state.runningCount} />
          <div style={{ minHeight: 0, overflowY: "auto", paddingRight: 2 }}>
            <ApprovalsPanel approvals={state.approvals} />
          </div>
        </div>
      </main>
    </div>
  );
}
