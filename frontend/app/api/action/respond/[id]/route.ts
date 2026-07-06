// APPROVE / DENY o aprobare de risc → orchestrator POST /risk/respond/{id}
// body: { action: "confirm" | "block" }
import { relay } from "@/lib/proxy";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(req: Request, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const body = await req.text();
  return relay(`/risk/respond/${encodeURIComponent(id)}`, { method: "POST", body });
}
