// Diagnostic pentru UI: spune ce status dă orchestratorul cu token-ul acestui server
// (200 ok, 401/403 token greșit, 502 oprit). Folosit doar când stream-ul /agui cade.
import { forward } from "@/lib/proxy";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET() {
  try {
    const r = await forward("/api/sessions", { method: "GET" });
    return Response.json({ status: r.status });
  } catch {
    return Response.json({ status: 502 });
  }
}
