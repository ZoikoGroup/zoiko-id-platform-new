import { server } from "@/server/bff";

export const dynamic = "force-dynamic";

/** Cell readiness for the status bar. Only the overall status leaves the server. */
export async function GET() {
  const { env } = server();
  try {
    const res = await fetch(`${env.cellApiUrl}/health/ready`, { cache: "no-store", signal: AbortSignal.timeout(3000) });
    const body = (await res.json().catch(() => ({}))) as { data?: { status?: string } };
    return Response.json(
      { status: res.ok ? (body.data?.status ?? "ready") : "degraded", environment: env.environment },
      { headers: { "cache-control": "no-store" } },
    );
  } catch {
    return Response.json({ status: "unreachable", environment: env.environment }, { headers: { "cache-control": "no-store" } });
  }
}
