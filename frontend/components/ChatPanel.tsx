"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Sheet } from "./Sheet";
import { LOCALE, t } from "@/lib/i18n";

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
const SESSION_KEY = "kage_session"; // partajat cu vechiul kage.html → sesiuni comune
const SESSION_FMT = new Intl.DateTimeFormat(LOCALE, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });

function newSessionId(): string {
  try {
    return crypto.randomUUID();
  } catch {
    return "ui-" + Date.now().toString(36) + Math.random().toString(36).slice(2, 8);
  }
}

function sessionLabel(s: Session): string {
  const d = new Date(s.started);
  const when = Number.isNaN(d.getTime()) ? s.started.slice(0, 16) : SESSION_FMT.format(d);
  return `${when} · ${s.messages} ${t("mesaje", "messages")}`;
}

export function ChatPanel({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [sessionId, setSessionId] = useState<string>("");
  const [sessions, setSessions] = useState<Session[]>([]);
  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const frame = useRef<number | null>(null);

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
    let sid: string | null = null;
    try {
      sid = localStorage.getItem(SESSION_KEY);
      if (!sid) {
        sid = newSessionId();
        localStorage.setItem(SESSION_KEY, sid);
      }
    } catch {
      sid = newSessionId();
    }
    setSessionId(sid);
  }, []);

  // La deschidere: lista de sesiuni + istoricul sesiunii curente.
  useEffect(() => {
    if (open && sessionId) {
      loadSessions();
      if (msgs.length === 0) loadHistory(sessionId);
      setTimeout(() => inputRef.current?.focus(), 0);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, sessionId]);

  // Rămâi lipit de fund doar dacă erai deja acolo.
  useEffect(() => {
    const el = scrollRef.current;
    if (el && el.scrollHeight - el.scrollTop - el.clientHeight < 120) el.scrollTop = el.scrollHeight;
  }, [msgs]);

  function remember(sid: string) {
    try {
      localStorage.setItem(SESSION_KEY, sid);
    } catch {
      /* fără storage: sesiunea ține cât pagina */
    }
  }

  function newChat() {
    const sid = newSessionId();
    remember(sid);
    setSessionId(sid);
    setMsgs([]);
    inputRef.current?.focus();
  }

  function switchSession(sid: string) {
    if (sid === sessionId) return;
    remember(sid);
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

    let acc = "";
    let badge: string | undefined;
    // Un singur re-render pe cadru, nu unul pe token.
    const flush = () => {
      frame.current = null;
      const cur = acc, b = badge;
      setMsgs((prev) => {
        const next = [...prev];
        next[next.length - 1] = { role: "assistant", content: cur, badge: b };
        return next;
      });
    };

    try {
      const resp = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Session-Id": sessionId },
        body: JSON.stringify({ model: "auto", stream: true, messages: history.map((m) => ({ role: m.role, content: m.content })) }),
      });
      if (!resp.ok || !resp.body) throw new Error(resp.status === 401 ? t("Kage a refuzat cererea (401).", "Kage refused the request (401).") : `${t("Kage a răspuns cu", "Kage answered with")} ${resp.status}.`);

      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";
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
          if (frame.current == null) frame.current = requestAnimationFrame(flush);
        }
      }
      if (frame.current != null) cancelAnimationFrame(frame.current);
      flush();
    } catch (e) {
      setMsgs((prev) => {
        const next = [...prev];
        next[next.length - 1] = { role: "assistant", content: e instanceof Error ? e.message : String(e) };
        return next;
      });
    } finally {
      setBusy(false);
      loadSessions();
    }
  }

  const known = sessions.some((s) => s.id === sessionId);

  return (
    <Sheet
      open={open}
      onClose={onClose}
      title="Chat"
      meta={
        <>
          {sessions.length > 0 && (
            <select className="input" style={{ width: "auto", maxWidth: 190, padding: "6px 10px", fontSize: 13, marginLeft: 8 }}
              aria-label={t("Conversații recente", "Recent conversations")} value={known ? sessionId : ""} onChange={(e) => e.target.value && switchSession(e.target.value)}>
              {!known && <option value="">{t("Conversație nouă", "New conversation")}</option>}
              {sessions.map((s) => <option key={s.id} value={s.id}>{sessionLabel(s)}</option>)}
            </select>
          )}
          <button className="link" onClick={newChat} style={{ marginLeft: 4 }}>{t("Nouă", "New")}</button>
        </>
      }
      footer={
        <>
          <label className="sr-only" htmlFor="chat-input">{t("Mesaj", "Message")}</label>
          <textarea
            id="chat-input"
            ref={inputRef}
            className="input"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                send();
              }
            }}
            placeholder={t("Scrie un mesaj (Enter trimite, Shift+Enter rând nou)", "Write a message (Enter sends, Shift+Enter for a new line)")}
            rows={2}
            style={{ maxHeight: 160 }}
          />
          <button className="btn primary" onClick={send} disabled={busy || !input.trim()}>
            {busy ? "…" : t("Trimite", "Send")}
          </button>
        </>
      }
    >
      <div className="sheet-body" ref={scrollRef} aria-live="polite">
        {msgs.length === 0 && (
          <div className="empty-chat">
            <span className="kanji" lang="ja" aria-hidden="true">影</span>
            {t("Întreabă orice. Kage alege singur modelul: local când se poate, cloud când trebuie.", "Ask anything. Kage picks the model itself: local when it can, cloud when it has to.")}
          </div>
        )}
        {msgs.map((m, i) => (
          <div key={i} className={`msg ${m.role}`}>
            {m.badge && <div className="route">{m.badge}</div>}
            <div className="bubble">{m.content || (busy && i === msgs.length - 1 ? "…" : "")}</div>
          </div>
        ))}
      </div>
    </Sheet>
  );
}
