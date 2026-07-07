// Task runner (agent autonom) → orchestrator POST /task/run (SSE), token server-side.
import { forward } from "@/lib/proxy";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(req: Request) {
  const body = await req.text();
  try {
    const up = await forward("/task/run", {
      method: "POST",
      body,
      headers: { Accept: "text/event-stream" },
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
