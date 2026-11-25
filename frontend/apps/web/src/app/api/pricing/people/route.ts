import { server } from "@/server/bff";

export const dynamic = "force-dynamic";

/** Public Verify People prices and an estimate, relayed from the cell's GET /v1/verify-api/pricing/people. Only plan and volume are forwarded; the cell validates them again. No session, no tenant data. */
export async function GET(req: Request) {
  const u = new URL(req.url);
  const q = new URLSearchParams();
  const plan = u.searchParams.get("plan");
  const n = u.searchParams.get("verifications");
  if (plan && /^[a-z]{3,20}$/.test(plan)) q.set("plan", plan);
  if (n && /^\d{1,10}$/.test(n)) q.set("verifications", n);
  const { env } = server();
  try {
    const res = await fetch(`${env.cellApiUrl.replace(/\/$/, "")}/v1/verify-api/pricing/people${q.size ? `?${q}` : ""}`, { cache: "no-store", headers: { accept: "application/json" } });
    return new Response(await res.text(), { status: res.status, headers: { "content-type": "application/json", "cache-control": "no-store" } });
  } catch {
    return Response.json({}, { status: 503 });
  }
}
