// Istoricul unei sesiuni → orchestrator GET /api/history?session_id=… (token server-side).
import { relay } from "@/lib/proxy";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(req: Request) {
  const sid = new URL(req.url).searchParams.get("session_id") || "default";
  return relay(`/api/history?session_id=${encodeURIComponent(sid)}`, { method: "GET" });
}
