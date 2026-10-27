"use client";

import { useEffect, useRef, useState } from "react";
import { Sheet } from "./Sheet";
import { t } from "@/lib/i18n";

type Mode = "claude" | "sysrun";

const MODES: { value: Mode; label: string }[] = [
  { value: "claude", label: "Claude Code" },
  { value: "sysrun", label: t("Sysrun (codul Kage)", "Sysrun (Kage's own code)") },
];

const BADGE_RE = /^\*\*\[([^\]]+)\]\*\* /;

// Construiește textul de task în forma așteptată de /task/run (vezi _prepare_and_launch_task).
function buildTask(mode: Mode, text: string): string {
  return mode === "sysrun" ? "!sysrun " + text : text; // claude = agent implicit
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
  const [error, setError] = useState<string | null>(null);
  const outRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (!open) return;
    setTimeout(() => inputRef.current?.focus(), 0);
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
    setError(null);

    const usesCwd = mode !== "sysrun" && cwd;
    const body = usesCwd ? { task: buildTask(mode, task), cwd } : { task: buildTask(mode, task) };

    try {
      const resp = await fetch("/api/task", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!resp.ok || !resp.body) throw new Error(resp.status === 401 ? t("Kage a refuzat task-ul (401).", "Kage refused the task (401).") : `${t("Kage a răspuns cu", "Kage answered with")} ${resp.status}.`);

      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";
      let acc = "";
      let badgeDone = false;

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
          // Badge-ul poate veni rupt în mai multe delta-uri: îl căutăm pe textul acumulat.
          if (!badgeDone) {
            const m = acc.match(BADGE_RE);
            if (m) {
              setBadge(m[1]);
              acc = acc.slice(m[0].length);
              badgeDone = true;
            } else if (acc.length > 80 || !acc.startsWith("*")) {
              badgeDone = true;
            }
          }
          setOutput(acc);
        }
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Sheet
      open={open}
      onClose={onClose}
      title={t("Task nou", "New task")}
      footer={
        <>
          <span className="quiet" style={{ fontSize: 12, marginRight: "auto", alignSelf: "center" }}>
            {t("Rulează în fundal; îl găsești la Agenți și dacă închizi.", "Runs in the background; it stays under Agents if you close this.")}
          </span>
          <button className="btn primary" onClick={run} disabled={busy || !text.trim()}>
            {busy ? t("Rulează…", "Running…") : <>{t("Pornește", "Start")} <kbd>⌘↵</kbd></>}
          </button>
        </>
      }
    >
      <div className="sheet-body">
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1.4fr", gap: 12 }}>
          <label className="field">
            {t("Cine lucrează", "Who does the work")}
            <select className="input" value={mode} onChange={(e) => setMode(e.target.value as Mode)}>
              {MODES.map((m) => <option key={m.value} value={m.value}>{m.label}</option>)}
            </select>
          </label>
          <label className="field">
            {t("În folderul", "In folder")}
            <select className="input" value={cwd} onChange={(e) => setCwd(e.target.value)} disabled={mode === "sysrun" || roots.length === 0}>
              {mode === "sysrun" && <option value="">{t("codul Kage", "Kage's own code")}</option>}
              {mode !== "sysrun" && roots.length === 0 && <option value="">{confinement ? t("niciun folder permis", "no allowed folder") : t("folderul implicit", "default folder")}</option>}
              {mode !== "sysrun" && roots.map((r) => <option key={r} value={r}>{r.replace(/^\/Users\/[^/]+/, "~")}</option>)}
            </select>
          </label>
        </div>
        <label className="field">
          {t("Ce trebuie făcut", "What needs doing")}
          <textarea
            ref={inputRef}
            className="input"
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
                e.preventDefault();
                run();
              }
            }}
            placeholder={t("ex: rulează testele și repară ce pică în test_scheduler.py", "e.g. run the tests and fix whatever fails in test_scheduler.py")}
            rows={4}
          />
        </label>
        {error && <p className="err" role="alert">{error}</p>}
        {(output || busy) && (
          <div ref={outRef} style={{ background: "var(--ground)", borderRadius: 12, padding: 14, overflowY: "auto", maxHeight: "50dvh" }} aria-live="polite">
            {badge && <div className="quiet" style={{ fontSize: 12, marginBottom: 6 }}>{badge}</div>}
            <pre className="out">{output || t("Pornește…", "Starting…")}</pre>
          </div>
        )}
      </div>
    </Sheet>
  );
}
