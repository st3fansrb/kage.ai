// Config non-sensibil pentru UI → orchestrator GET /api/config (allowed_task_roots + cwd).
import { relay } from "@/lib/proxy";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET() {
  return relay("/api/config", { method: "GET" });
}
