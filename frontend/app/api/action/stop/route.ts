// Kill switch global → orchestrator POST /api/stop (oprește agenții + pauzează scheduler-ul).
import { relay } from "@/lib/proxy";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST() {
  return relay("/api/stop", { method: "POST" });
}
