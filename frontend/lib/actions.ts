"use client";

// Acțiuni de la client → proxy-urile same-origin /api/action/* (care atașează token-ul).

export async function respondApproval(id: string, action: "confirm" | "block"): Promise<boolean> {
  try {
    const r = await fetch(`/api/action/respond/${encodeURIComponent(id)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action }),
    });
    return r.ok;
  } catch {
    return false;
  }
}

export async function stopAll(): Promise<boolean> {
  try {
    const r = await fetch("/api/action/stop", { method: "POST" });
    return r.ok;
  } catch {
    return false;
  }
}
