"use client";

import { useEffect, useRef, useState } from "react";
import { EMPTY_STATE, MissionState } from "./types";

export type ConnState = "connecting" | "live" | "error";

// Consumă stream-ul AG-UI (same-origin /api/agui) și expune starea Mission Control.
// Backend-ul emite RUN_STARTED → STATE_SNAPSHOT (inițial) → STATE_SNAPSHOT la schimbare.
// Aici tratăm STATE_SNAPSHOT (înlocuire completă); reconectare automată la eroare.
export function useMissionState(): { state: MissionState; conn: ConnState } {
  const [state, setState] = useState<MissionState>(EMPTY_STATE);
  const [conn, setConn] = useState<ConnState>("connecting");
  const esRef = useRef<EventSource | null>(null);

  useEffect(() => {
    let closed = false;
    let retry: ReturnType<typeof setTimeout> | null = null;

    function connect() {
      const es = new EventSource("/api/agui");
      esRef.current = es;

      es.onopen = () => setConn("live");
      es.onmessage = (ev) => {
        try {
          const msg = JSON.parse(ev.data);
          if (msg.type === "STATE_SNAPSHOT" && msg.snapshot) {
            setState(msg.snapshot as MissionState);
            setConn("live");
          }
        } catch {
          /* ignoră liniile care nu sunt JSON (keepalive) */
        }
      };
      es.onerror = () => {
        setConn("error");
        es.close();
        if (!closed) retry = setTimeout(connect, 3000);
      };
    }

    connect();
    return () => {
      closed = true;
      if (retry) clearTimeout(retry);
      esRef.current?.close();
    };
  }, []);

  return { state, conn };
}
