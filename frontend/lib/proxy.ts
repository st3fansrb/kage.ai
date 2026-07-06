// Helper server-side: forwardează o cerere spre orchestrator cu token-ul API atașat.
// Folosit DOAR din route handlers (runtime nodejs) — token-ul rămâne pe server.

export async function forward(path: string, init?: RequestInit): Promise<Response> {
  const base = process.env.ORCHESTRATOR_URL || "http://localhost:4001";
  const token = process.env.KAGE_API_TOKEN || "";
  const headers = new Headers(init?.headers);
  headers.set("Content-Type", "application/json");
  if (token) headers.set("Authorization", `Bearer ${token}`);
  return fetch(`${base}${path}`, { ...init, headers, cache: "no-store" });
}

export async function relay(path: string, init?: RequestInit): Promise<Response> {
  try {
    const r = await forward(path, init);
    const text = await r.text();
    return new Response(text || "{}", {
      status: r.status,
      headers: { "Content-Type": "application/json" },
    });
  } catch (e) {
    return new Response(JSON.stringify({ ok: false, error: String(e) }), {
      status: 502,
      headers: { "Content-Type": "application/json" },
    });
  }
}
