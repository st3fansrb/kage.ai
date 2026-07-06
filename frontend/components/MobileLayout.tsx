"use client";

import { useState } from "react";
import { MissionState } from "@/lib/types";
import { ConnState } from "@/lib/useMissionState";
import { T } from "@/lib/tokens";
import { AgentsPanel } from "./AgentsPanel";
import { ApprovalsPanel } from "./ApprovalsPanel";

type Tab = "approvals" | "agents";

export function MobileLayout({
  state,
  conn,
  onOpenChat,
}: {
  state: MissionState;
  conn: ConnState;
  onOpenChat: () => void;
}) {
  const [tab, setTab] = useState<Tab>(state.approvals.length > 0 ? "approvals" : "agents");
  const live = conn === "live";

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100vh", maxWidth: 480, margin: "0 auto", background: T.bg, color: T.text, overflow: "hidden" }}>
      {/* header */}
      <header style={{ flex: "none", display: "flex", alignItems: "center", gap: 9, padding: "14px 16px 12px", borderBottom: `1px solid ${T.border}` }}>
        <span style={{ fontWeight: 700, fontSize: 16, textShadow: "2px 2px 0 rgba(255,106,48,.22)" }}>影 kage</span>
        <span style={{ display: "flex", alignItems: "center", gap: 5, fontSize: 10, color: live ? T.teal : T.orange2, fontFamily: T.mono }}>
          <span style={{ width: 6, height: 6, borderRadius: "50%", background: live ? T.teal : T.orange2, boxShadow: `0 0 8px ${live ? T.teal : T.orange2}`, animation: "kpulse 2s ease-in-out infinite" }} />
          {live ? "live" : "…"}
        </span>
        <span style={{ marginLeft: "auto", fontFamily: T.mono, fontSize: 10.5, color: T.muted }}>
          {state.budget.cloudCalls}/{state.budget.maxCloud} · <b style={{ color: T.text, fontWeight: 500 }}>${state.budget.spentUsd.toFixed(2)}</b>
        </span>
        <button onClick={onOpenChat} title="Chat" style={{ marginLeft: 4, background: "none", border: `1px solid ${T.border3}`, borderRadius: 8, color: T.muted, fontSize: 13, padding: "3px 8px", cursor: "pointer" }}>⌘</button>
      </header>

      {/* content */}
      <div style={{ flex: 1, minHeight: 0, overflowY: "auto", padding: 14 }}>
        {tab === "approvals" ? (
          <ApprovalsPanel approvals={state.approvals} />
        ) : (
          <AgentsPanel agents={state.agents} runningCount={state.runningCount} />
        )}
      </div>

      {/* tab bar */}
      <nav style={{ flex: "none", display: "flex", gap: 6, padding: "8px 12px 14px", borderTop: `1px solid ${T.border}`, background: T.rail }}>
        <TabButton icon="!" label="aprobă" active={tab === "approvals"} badge={state.approvals.length} onClick={() => setTab("approvals")} />
        <TabButton icon="◎" label="agenți" active={tab === "agents"} badge={0} onClick={() => setTab("agents")} />
      </nav>
    </div>
  );
}

function TabButton({ icon, label, active, badge, onClick }: { icon: string; label: string; active: boolean; badge: number; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      style={{
        flex: 1,
        height: 52,
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        gap: 4,
        borderRadius: 12,
        border: "none",
        cursor: "pointer",
        background: active ? "rgba(236,225,210,.08)" : "none",
        color: active ? T.text : T.muted,
        boxShadow: active ? "inset 0 0 0 1px rgba(236,225,210,.12)" : "none",
      }}
    >
      <span style={{ fontSize: 17, lineHeight: 1, position: "relative" }}>
        {icon}
        {badge > 0 && (
          <span style={{ position: "absolute", top: -6, right: -13, minWidth: 16, height: 16, borderRadius: 8, background: T.orange, color: T.bg, fontFamily: T.mono, fontSize: 10, fontWeight: 600, display: "flex", alignItems: "center", justifyContent: "center", padding: "0 4px" }}>
            {badge}
          </span>
        )}
      </span>
      <span style={{ fontSize: 10, letterSpacing: ".06em" }}>{label}</span>
    </button>
  );
}
