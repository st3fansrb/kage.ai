// Lista sesiunilor de chat → orchestrator GET /api/sessions (token atașat server-side).
import { relay } from "@/lib/proxy";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET() {
  return relay("/api/sessions", { method: "GET" });
}
