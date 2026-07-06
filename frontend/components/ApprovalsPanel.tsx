"use client";

import { useState } from "react";
import { Approval } from "@/lib/types";
import { T } from "@/lib/tokens";
import { respondApproval } from "@/lib/actions";

export function ApprovalsPanel({ approvals }: { approvals: Approval[] }) {
  // optimistic: ascunde cardul imediat ce APPROVE/DENY reușește; SSE reconciliază.
  const [resolving, setResolving] = useState<Record<string, boolean>>({});
  const [hidden, setHidden] = useState<Record<string, boolean>>({});

  async function act(id: string, action: "confirm" | "block") {
    setResolving((r) => ({ ...r, [id]: true }));
    const ok = await respondApproval(id, action);
    if (ok) setHidden((h) => ({ ...h, [id]: true }));
    else setResolving((r) => ({ ...r, [id]: false }));
  }

  const visible = approvals.filter((ap) => !hidden[ap.id]);
  const has = visible.length > 0;
  return (
    <div
      style={{
        background: has ? "rgba(255,106,48,.05)" : T.panel,
        border: has ? `1px solid ${T.orangeLine}` : `1px solid ${T.border2}`,
        borderRadius: 12,
        padding: 14,
        boxShadow: has ? `${T.cardShadow}, 0 0 24px rgba(255,106,48,.1)` : "none",
      }}
    >
      <div style={{ display: "flex", alignItems: "baseline", gap: 8, marginBottom: 10 }}>
        <span style={{ fontSize: 10, letterSpacing: ".14em", textTransform: "uppercase", color: T.orange2, fontWeight: 500 }}>
          Approvals
        </span>
        <span style={{ fontFamily: T.mono, fontSize: 10, color: T.orange2 }}>{visible.length}</span>
      </div>

      {!has && (
        <div style={{ textAlign: "center", color: T.dim, padding: "22px 0" }}>
          <div style={{ fontSize: 30, opacity: 0.5, textShadow: "3px 3px 0 rgba(255,106,48,.15)" }}>影</div>
          <div style={{ fontSize: 12, marginTop: 10, lineHeight: 1.5 }}>Inbox gol.</div>
        </div>
      )}

      <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
        {visible.map((ap) => (
          <div
            key={ap.id}
            style={{
              background: T.panel,
              border: `1.5px solid ${T.orangeLine}`,
              borderRadius: 12,
              padding: 12,
              animation: "krise .3s ease",
            }}
          >
            <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 9 }}>
              <span
                style={{
                  fontFamily: T.mono,
                  fontSize: 9.5,
                  letterSpacing: ".07em",
                  color: T.orange2,
                  border: `1px solid ${T.orangeLine}`,
                  background: T.orangeTint,
                  borderRadius: 7,
                  padding: "2px 8px",
                }}
              >
                {ap.risk}
              </span>
              <span style={{ fontFamily: T.mono, fontSize: 10, color: T.muted, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {ap.agent}
              </span>
            </div>
            <div
              style={{
                fontFamily: T.mono,
                fontSize: 12,
                color: "#ffd9c9",
                background: "#0b0907",
                border: "1px solid rgba(255,106,48,.22)",
                borderRadius: 9,
                padding: "10px 11px",
                whiteSpace: "pre-wrap",
                wordBreak: "break-all",
              }}
            >
              {ap.cmd}
            </div>
            {ap.why && <div style={{ fontSize: 12, color: T.muted, lineHeight: 1.5, marginTop: 8 }}>{ap.why}</div>}
            <div style={{ display: "flex", gap: 9, marginTop: 11 }}>
              <button
                onClick={() => act(ap.id, "confirm")}
                disabled={resolving[ap.id]}
                style={{
                  flex: 2,
                  height: 40,
                  border: "none",
                  borderRadius: 10,
                  background: T.orange,
                  color: T.bg,
                  fontWeight: 700,
                  fontSize: 14,
                  cursor: resolving[ap.id] ? "wait" : "pointer",
                  opacity: resolving[ap.id] ? 0.55 : 1,
                  boxShadow: "0 0 18px rgba(255,106,48,.25)",
                }}
              >
                {resolving[ap.id] ? "…" : "APPROVE"}
              </button>
              <button
                onClick={() => act(ap.id, "block")}
                disabled={resolving[ap.id]}
                style={{
                  flex: 1,
                  height: 40,
                  border: `1px solid ${T.border3}`,
                  borderRadius: 10,
                  background: "none",
                  color: T.soft2,
                  fontSize: 13,
                  cursor: resolving[ap.id] ? "wait" : "pointer",
                  opacity: resolving[ap.id] ? 0.55 : 1,
                }}
              >
                DENY
              </button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
