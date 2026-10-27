"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { EMPTY_STATE, MissionState } from "./types";

export type ConnState = "connecting" | "live" | "error";

export interface Conn {
  state: ConnState;
  since: Date | null; // ultima dată când datele au fost la zi (live) sau începutul căderii
  cause: string | null; // de ce nu avem legătură, în cuvinte
  hasData: boolean; // a venit cel puțin un snapshot
}

// Traduce statusul din /api/ping într-o cauză pe care o poți repara.
function causeFor(status: number): string {
  if (status === 401 || status === 403) return `${status}: orchestratorul refuză cererea; verifică KAGE_API_TOKEN în frontend/.env.local`;
  if (status === 502 || status === 0) return "orchestratorul nu răspunde; pornește-l cu scripts/start_all.sh";
  return `orchestratorul a răspuns cu ${status}`;
}

// Consumă stream-ul AG-UI (same-origin /api/agui) și expune starea Mission Control.
// Backend-ul emite RUN_STARTED → STATE_SNAPSHOT (inițial) → STATE_SNAPSHOT la schimbare.
// La eroare: păstrează ultima stare, află cauza prin /api/ping și reîncearcă la 5s.
export function useMissionState(): { state: MissionState; conn: Conn; retry: () => void } {
  const [state, setState] = useState<MissionState>(EMPTY_STATE);
  const [conn, setConn] = useState<Conn>({ state: "connecting", since: null, cause: null, hasData: false });
  const esRef = useRef<EventSource | null>(null);
  const retryRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const closedRef = useRef(false);

  const connect = useCallback(() => {
    if (retryRef.current) clearTimeout(retryRef.current);
    esRef.current?.close();
    const es = new EventSource("/api/agui");
    esRef.current = es;

    es.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data);
        if (msg.type === "STATE_SNAPSHOT" && msg.snapshot) {
          // Merge cu defaults: un backend mai vechi (sau parțial) poate omite chei.
          setState({ ...EMPTY_STATE, ...(msg.snapshot as Partial<MissionState>) });
          setConn({ state: "live", since: new Date(), cause: null, hasData: true });
        }
      } catch {
        /* ignoră liniile care nu sunt JSON (keepalive) */
      }
    };
    es.onerror = () => {
      es.close();
      setConn((c) => ({ ...c, state: "error", since: c.state === "error" ? c.since : c.since ?? new Date() }));
      fetch("/api/ping", { cache: "no-store" })
        .then((r) => r.json())
        .then((p: { status: number }) => setConn((c) => ({ ...c, cause: p.status === 200 ? null : causeFor(p.status) })))
        .catch(() => setConn((c) => ({ ...c, cause: "frontend-ul nu poate ajunge la propriul server" })));
      if (!closedRef.current) retryRef.current = setTimeout(connect, 5000);
    };
  }, []);

  useEffect(() => {
    closedRef.current = false;
    connect();
    return () => {
      closedRef.current = true;
      if (retryRef.current) clearTimeout(retryRef.current);
      esRef.current?.close();
    };
  }, [connect]);

  return { state, conn, retry: connect };
}
