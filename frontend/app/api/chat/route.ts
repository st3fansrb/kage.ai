// Chat → orchestrator POST /v1/chat/completions (SSE), cu token atașat server-side.
import { forward } from "@/lib/proxy";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(req: Request) {
  const body = await req.text();
  const sid = req.headers.get("x-session-id") || "default";
  try {
    const up = await forward("/v1/chat/completions", {
      method: "POST",
      body,
      headers: { Accept: "text/event-stream", "X-Session-Id": sid },
    });
    if (!up.body) {
      return new Response(JSON.stringify({ error: `orchestrator ${up.status}` }), {
        status: up.status || 502,
        headers: { "Content-Type": "application/json" },
      });
    }
    return new Response(up.body, {
      status: up.status,
      headers: { "Content-Type": "text/event-stream", "Cache-Control": "no-cache, no-transform" },
    });
  } catch (e) {
    return new Response(JSON.stringify({ error: String(e) }), {
      status: 502,
      headers: { "Content-Type": "application/json" },
    });
  }
}
