"use client";

import { useEffect, useRef, useState } from "react";
import { T } from "@/lib/tokens";

type Mode = "claude" | "sysrun";

const MODES: { value: Mode; label: string }[] = [
  { value: "claude", label: "Claude" },
  { value: "sysrun", label: "Sysrun (cod Kage)" },
];

const BADGE_RE = /^\*\*\[([^\]]+)\]\*\* /;

// Construiește textul de task în forma așteptată de /task/run (vezi _prepare_and_launch_task).
function buildTask(mode: Mode, text: string): string {
  switch (mode) {
    case "sysrun":
      return "!sysrun " + text;
    default:
      return text; // claude = agent implicit
  }
}

export function TaskPanel({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [mode, setMode] = useState<Mode>("claude");
  const [roots, setRoots] = useState<string[]>([]);
  const [cwd, setCwd] = useState("");
  const [confinement, setConfinement] = useState(false);
  const [text, setText] = useState("");
  const [output, setOutput] = useState("");
  const [badge, setBadge] = useState<string | undefined>();
  const [busy, setBusy] = useState(false);
  const outRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (!open) return;
    inputRef.current?.focus();
    fetch("/api/config")
      .then((r) => r.json())
      .then((cfg) => {
        const rs: string[] = Array.isArray(cfg.allowed_task_roots) ? cfg.allowed_task_roots : [];
        setRoots(rs);
        setConfinement(!!cfg.confinement_enabled);
        setCwd(cfg.default_task_cwd || rs[0] || "");
      })
      .catch(() => {});
  }, [open]);

  useEffect(() => {
    outRef.current?.scrollTo({ top: outRef.current.scrollHeight });
  }, [output]);

  async function run() {
    const task = text.trim();
    if (!task || busy) return;
    setBusy(true);
    setOutput("");
    setBadge(undefined);

    const usesCwd = mode !== "sysrun" && cwd;
    const body = usesCwd ? { task: buildTask(mode, task), cwd } : { task: buildTask(mode, task) };

    try {
      const resp = await fetch("/api/task", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!resp.body) throw new Error("fără stream");

      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";
      let acc = "";
      let foundBadge = false;

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
          if (!foundBadge) {
            const m = delta.match(BADGE_RE);
            if (m) {
              setBadge(m[1]);
              acc += delta.slice(m[0].length);
            } else {
              acc += delta;
            }
            foundBadge = true;
          } else {
            acc += delta;
          }
          setOutput(acc);
        }
      }
    } catch (e) {
      setOutput(`⚠️ ${String(e)}`);
    } finally {
      setBusy(false);
    }
  }

  if (!open) return null;

  const ctrl: React.CSSProperties = {
    background: T.panel,
    color: T.text,
    border: `1px solid ${T.border3}`,
    borderRadius: 8,
    fontSize: 12,
    fontFamily: T.mono,
    padding: "6px 8px",
    outline: "none",
  };

  return (
    <>
      <div onClick={onClose} style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,.35)", zIndex: 40 }} />
      <aside
        style={{
          position: "fixed",
          top: 0,
          right: 0,
          height: "100vh",
          width: "min(460px, 94vw)",
          background: T.bg,
          borderLeft: `1px solid ${T.border}`,
          zIndex: 50,
          display: "flex",
          flexDirection: "column",
          boxShadow: "-16px 0 40px rgba(0,0,0,.4)",
        }}
      >
        <div style={{ height: 52, flex: "none", display: "flex", alignItems: "center", gap: 10, padding: "0 12px 0 16px", borderBottom: `1px solid ${T.border}` }}>
          <span style={{ fontWeight: 600, fontSize: 14 }}>task runner</span>
          <span style={{ fontFamily: T.mono, fontSize: 10, color: T.dim }}>agent autonom</span>
          <div style={{ flex: 1 }} />
          <button onClick={onClose} style={{ background: "none", border: "none", color: T.muted, fontSize: 18, cursor: "pointer" }}>
            ✕
          </button>
        </div>

        <div style={{ flex: "none", padding: 14, display: "flex", flexDirection: "column", gap: 10, borderBottom: `1px solid ${T.border}` }}>
          <div style={{ display: "flex", gap: 8 }}>
            <select value={mode} onChange={(e) => setMode(e.target.value as Mode)} style={{ ...ctrl, flex: 1 }}>
              {MODES.map((m) => (
                <option key={m.value} value={m.value}>
                  {m.label}
                </option>
              ))}
            </select>
            <select
              value={cwd}
              onChange={(e) => setCwd(e.target.value)}
              disabled={mode === "sysrun" || roots.length === 0}
              title={mode === "sysrun" ? "Sysrun folosește directorul sursă Kage" : "Director de lucru"}
              style={{ ...ctrl, flex: 1.4, opacity: mode === "sysrun" || roots.length === 0 ? 0.5 : 1 }}
            >
              {roots.length === 0 && <option value="">{confinement ? "fără rooturi" : "cwd implicit"}</option>}
              {roots.map((r) => (
                <option key={r} value={r}>
                  {r.replace(/^\/Users\/[^/]+/, "~")}
                </option>
              ))}
            </select>
          </div>
          <textarea
            ref={inputRef}
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
                e.preventDefault();
                run();
              }
            }}
            placeholder="descrie task-ul agentului… (⌘Enter pornește)"
            rows={3}
            style={{ ...ctrl, fontFamily: T.sans, fontSize: 13, resize: "vertical", minHeight: 64 }}
          />
          <button
            onClick={run}
            disabled={busy || !text.trim()}
            style={{
              alignSelf: "flex-start",
              padding: "8px 18px",
              borderRadius: 10,
              border: "none",
              background: T.orange,
              color: T.bg,
              fontWeight: 700,
              fontSize: 13,
              cursor: busy || !text.trim() ? "not-allowed" : "pointer",
              opacity: busy || !text.trim() ? 0.5 : 1,
            }}
          >
            {busy ? "rulează…" : "▶ Pornește agentul"}
          </button>
        </div>

        <div ref={outRef} style={{ flex: 1, overflowY: "auto", padding: 16 }}>
          {badge && <div style={{ fontFamily: T.mono, fontSize: 10, color: T.teal, marginBottom: 8 }}>{badge}</div>}
          {output ? (
            <pre style={{ margin: 0, fontFamily: T.mono, fontSize: 12, lineHeight: 1.55, color: T.soft, whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
              {output}
              {busy && <span style={{ color: T.orange }}>▍</span>}
            </pre>
          ) : (
            <div style={{ color: T.dim, fontSize: 13, textAlign: "center", marginTop: 40, lineHeight: 1.6 }}>
              Agentul rulează în fundal.<br />Output-ul se salvează în istoric chiar dacă închizi.
            </div>
          )}
        </div>
      </aside>
    </>
  );
}
