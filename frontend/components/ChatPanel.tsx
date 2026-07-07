"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { T } from "@/lib/tokens";

interface Msg {
  role: "user" | "assistant";
  content: string;
  badge?: string;
}

interface Session {
  id: string;
  started: string;
  messages: number;
}

const BADGE_RE = /^\*\*\[([^\]]+)\]\*\* /;
const SESSION_KEY = "kage_session"; // partajat cu kage.html → sesiuni comune

function newSessionId(): string {
  try {
    return crypto.randomUUID();
  } catch {
    return "ui-" + Date.now().toString(36) + Math.random().toString(36).slice(2, 8);
  }
}

export function ChatPanel({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [sessionId, setSessionId] = useState<string>("");
  const [sessions, setSessions] = useState<Session[]>([]);
  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  const loadHistory = useCallback(async (sid: string) => {
    try {
      const rows = await fetch(`/api/history?session_id=${encodeURIComponent(sid)}`).then((r) => r.json());
      if (Array.isArray(rows)) {
        setMsgs(rows.map((r: { role: string; content: string }) => ({ role: r.role === "user" ? "user" : "assistant", content: r.content })));
      }
    } catch {
      /* sesiune goală sau backend indisponibil */
    }
  }, []);

  const loadSessions = useCallback(async () => {
    try {
      const rows = await fetch("/api/sessions").then((r) => r.json());
      if (Array.isArray(rows)) setSessions(rows);
    } catch {
      /* ignoră */
    }
  }, []);

  // Inițializează sesiunea din localStorage (o dată).
  useEffect(() => {
    let sid = localStorage.getItem(SESSION_KEY);
    if (!sid) {
      sid = newSessionId();
      localStorage.setItem(SESSION_KEY, sid);
    }
    setSessionId(sid);
  }, []);

  // La deschidere: încarcă lista de sesiuni + istoricul sesiunii curente.
  useEffect(() => {
    if (open && sessionId) {
      loadSessions();
      if (msgs.length === 0) loadHistory(sessionId);
      inputRef.current?.focus();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, sessionId]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [msgs]);

  function newChat() {
    const sid = newSessionId();
    localStorage.setItem(SESSION_KEY, sid);
    setSessionId(sid);
    setMsgs([]);
    inputRef.current?.focus();
  }

  function switchSession(sid: string) {
    if (sid === sessionId) return;
    localStorage.setItem(SESSION_KEY, sid);
    setSessionId(sid);
    setMsgs([]);
    loadHistory(sid);
  }

  async function send() {
    const text = input.trim();
    if (!text || busy || !sessionId) return;
    const history = [...msgs, { role: "user" as const, content: text }];
    setMsgs([...history, { role: "assistant", content: "" }]);
    setInput("");
    setBusy(true);

    try {
      const resp = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Session-Id": sessionId },
        body: JSON.stringify({
          model: "auto",
          stream: true,
          messages: history.map((m) => ({ role: m.role, content: m.content })),
        }),
      });
      if (!resp.body) throw new Error("fără stream");

      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";
      let acc = "";
      let badge: string | undefined;

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        const lines = buf.split("\n");
        buf = lines.pop() || "";
        for (const line of lines) {
          if (!line.startsWith("data: ")) continue;
          const raw = line.slice(6).trim();
          if (raw === "[DONE]") continue;
          let delta = "";
          try {
            delta = JSON.parse(raw).choices?.[0]?.delta?.content || "";
          } catch {
            continue;
          }
          if (!delta) continue;
          acc += delta;
          if (badge === undefined) {
            const m = acc.match(BADGE_RE);
            if (m) {
              badge = m[1];
              acc = acc.slice(m[0].length);
            }
          }
          const cur = acc;
          const b = badge;
          setMsgs((prev) => {
            const next = [...prev];
            next[next.length - 1] = { role: "assistant", content: cur, badge: b };
            return next;
          });
        }
      }
    } catch (e) {
      setMsgs((prev) => {
        const next = [...prev];
        next[next.length - 1] = { role: "assistant", content: `⚠️ ${String(e)}` };
        return next;
      });
    } finally {
      setBusy(false);
      loadSessions(); // reflectă sesiunea nouă în listă
    }
  }

  if (!open) return null;

  return (
    <>
      <div
        onClick={onClose}
        style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,.35)", zIndex: 40 }}
      />
      <aside
        style={{
          position: "fixed",
          top: 0,
          right: 0,
          height: "100vh",
          width: "min(420px, 92vw)",
          background: T.bg,
          borderLeft: `1px solid ${T.border}`,
          zIndex: 50,
          display: "flex",
          flexDirection: "column",
          boxShadow: "-16px 0 40px rgba(0,0,0,.4)",
        }}
      >
        <div style={{ height: 52, flex: "none", display: "flex", alignItems: "center", gap: 10, padding: "0 12px 0 16px", borderBottom: `1px solid ${T.border}` }}>
          <span style={{ fontWeight: 600, fontSize: 14 }}>chat</span>
          <span style={{ fontFamily: T.mono, fontSize: 10, color: T.dim }}>⌘J</span>
          <div style={{ flex: 1 }} />
          {sessions.length > 0 && (
            <select
              value={sessions.some((s) => s.id === sessionId) ? sessionId : ""}
              onChange={(e) => e.target.value && switchSession(e.target.value)}
              title="Sesiuni recente"
              style={{
                background: T.panel,
                color: T.muted,
                border: `1px solid ${T.border3}`,
                borderRadius: 8,
                fontSize: 11,
                fontFamily: T.mono,
                padding: "3px 6px",
                maxWidth: 150,
                outline: "none",
              }}
            >
              {!sessions.some((s) => s.id === sessionId) && <option value="">· sesiune nouă</option>}
              {sessions.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.id.slice(0, 8)} · {s.messages} msg
                </option>
              ))}
            </select>
          )}
          <button
            onClick={newChat}
            title="Conversație nouă"
            style={{ background: "none", border: "none", color: T.muted, fontSize: 16, cursor: "pointer", padding: "0 2px" }}
          >
            ✦
          </button>
          <button
            onClick={onClose}
            style={{ background: "none", border: "none", color: T.muted, fontSize: 18, cursor: "pointer" }}
          >
            ✕
          </button>
        </div>

        <div ref={scrollRef} style={{ flex: 1, overflowY: "auto", padding: 16, display: "flex", flexDirection: "column", gap: 12 }}>
          {msgs.length === 0 && (
            <div style={{ color: T.dim, fontSize: 13, textAlign: "center", marginTop: 40, lineHeight: 1.6 }}>
              <div style={{ fontSize: 34, opacity: 0.5, textShadow: "3px 3px 0 rgba(255,106,48,.15)" }}>影</div>
              Vorbește cu Kage.<br />Rutare automată pe tier.
            </div>
          )}
          {msgs.map((m, i) => (
            <div key={i} style={{ alignSelf: m.role === "user" ? "flex-end" : "flex-start", maxWidth: "88%" }}>
              {m.badge && (
                <div style={{ fontFamily: T.mono, fontSize: 9.5, color: T.teal, marginBottom: 3 }}>{m.badge}</div>
              )}
              <div
                style={{
                  background: m.role === "user" ? "rgba(255,106,48,.12)" : T.panel,
                  border: `1px solid ${m.role === "user" ? T.orangeLine : T.border2}`,
                  borderRadius: 12,
                  padding: "9px 12px",
                  fontSize: 13.5,
                  lineHeight: 1.55,
                  color: T.soft,
                  whiteSpace: "pre-wrap",
                  wordBreak: "break-word",
                }}
              >
                {m.content || (busy && i === msgs.length - 1 ? "…" : "")}
              </div>
            </div>
          ))}
        </div>

        <div style={{ flex: "none", padding: 12, borderTop: `1px solid ${T.border}`, display: "flex", gap: 8 }}>
          <textarea
            ref={inputRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                send();
              }
            }}
            placeholder="mesaj… (Enter trimite)"
            rows={1}
            style={{
              flex: 1,
              resize: "none",
              background: T.panel,
              border: `1px solid ${T.border3}`,
              borderRadius: 10,
              color: T.text,
              fontFamily: T.sans,
              fontSize: 13.5,
              padding: "10px 12px",
              outline: "none",
              maxHeight: 120,
            }}
          />
          <button
            onClick={send}
            disabled={busy || !input.trim()}
            style={{
              flex: "none",
              width: 44,
              borderRadius: 10,
              border: "none",
              background: T.orange,
              color: T.bg,
              fontWeight: 700,
              fontSize: 16,
              cursor: busy || !input.trim() ? "not-allowed" : "pointer",
              opacity: busy || !input.trim() ? 0.5 : 1,
            }}
          >
            ↑
          </button>
        </div>
      </aside>
    </>
  );
}
