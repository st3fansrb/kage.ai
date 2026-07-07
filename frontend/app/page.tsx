"use client";

import { useEffect, useState } from "react";
import { useMissionState } from "@/lib/useMissionState";
import { Header } from "@/components/Header";
import { AgentsPanel } from "@/components/AgentsPanel";
import { ApprovalsPanel } from "@/components/ApprovalsPanel";
import { ActivityStream } from "@/components/ActivityStream";
import { ChatPanel } from "@/components/ChatPanel";
import { CacheMemoryPanel } from "@/components/CacheMemoryPanel";
import { BriefingPanel } from "@/components/BriefingPanel";
import { MobileLayout } from "@/components/MobileLayout";
import { useIsMobile } from "@/lib/useIsMobile";
import { T } from "@/lib/tokens";

export default function MissionControl() {
  const { state, conn } = useMissionState();
  const [chatOpen, setChatOpen] = useState(false);
  const isMobile = useIsMobile();

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "j") {
        e.preventDefault();
        setChatOpen((o) => !o);
      }
      if (e.key === "Escape") setChatOpen(false);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  if (isMobile) {
    return (
      <>
        <MobileLayout state={state} conn={conn} onOpenChat={() => setChatOpen(true)} />
        <ChatPanel open={chatOpen} onClose={() => setChatOpen(false)} />
      </>
    );
  }

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
        <div style={{ flex: 1 }} />
        <button
          onClick={() => setChatOpen((o) => !o)}
          title="Chat ⌘J"
          style={{
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            gap: 4,
            width: 48,
            padding: "8px 0",
            borderRadius: 12,
            border: "none",
            cursor: "pointer",
            background: chatOpen ? "rgba(236,225,210,.08)" : "none",
            color: chatOpen ? T.text : T.muted,
          }}
        >
          <span style={{ fontSize: 15, lineHeight: 1 }}>⌘</span>
          <span style={{ fontSize: 8.5, letterSpacing: ".08em", textTransform: "uppercase" }}>chat</span>
        </button>
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
          <div style={{ minHeight: 0, overflowY: "auto", paddingRight: 2, display: "flex", flexDirection: "column", gap: 14 }}>
            <ApprovalsPanel approvals={state.approvals} />
            <BriefingPanel briefing={state.briefing} />
            <CacheMemoryPanel cache={state.cache} />
          </div>
        </div>
      </main>

      <ChatPanel open={chatOpen} onClose={() => setChatOpen(false)} />
    </div>
  );
}
