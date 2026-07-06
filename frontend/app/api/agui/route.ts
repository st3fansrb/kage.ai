// Proxy server-side pentru stream-ul AG-UI al orchestratorului.
// Deschide ${ORCHESTRATOR_URL}/agui cu token-ul API (server-side) și streamează SSE-ul
// înapoi la browser, same-origin. Token-ul nu ajunge niciodată în client.

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function GET() {
  const base = process.env.ORCHESTRATOR_URL || "http://localhost:4001";
  const token = process.env.KAGE_API_TOKEN || "";

  const headers: Record<string, string> = { Accept: "text/event-stream" };
  if (token) headers["Authorization"] = `Bearer ${token}`;

  let upstream: Response;
  try {
    upstream = await fetch(`${base}/agui`, { headers, cache: "no-store" });
  } catch (e) {
    return new Response(
      `event: error\ndata: ${JSON.stringify({ error: `orchestrator unreachable: ${String(e)}` })}\n\n`,
      { status: 502, headers: { "Content-Type": "text/event-stream" } },
    );
  }

  if (!upstream.ok || !upstream.body) {
    return new Response(
      `event: error\ndata: ${JSON.stringify({ error: `orchestrator ${upstream.status}` })}\n\n`,
      { status: upstream.status, headers: { "Content-Type": "text/event-stream" } },
    );
  }

  return new Response(upstream.body, {
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache, no-transform",
      Connection: "keep-alive",
      "X-Accel-Buffering": "no",
    },
  });
}
