import { server } from "@/server/bff";

export const dynamic = "force-dynamic";

/** Relays a vulnerability report to the cell and returns its answer. The browser never calls the cell directly; the report text is not logged here. */
export async function POST(req: Request) {
  const { env } = server();
  let body: string;
  try {
    body = JSON.stringify(await req.json());
  } catch {
    return Response.json({}, { status: 422 });
  }
  if (body.length > 20_000) return Response.json({}, { status: 413 });
  try {
    const fwd = req.headers.get("x-forwarded-for");
    const res = await fetch(`${env.cellApiUrl.replace(/\/$/, "")}/public/trust/v1/vulnerability-reports`, {
      method: "POST", cache: "no-store", body, headers: { "content-type": "application/json", accept: "application/json", ...(fwd ? { "x-forwarded-for": fwd } : {}) },
    });
    return new Response(await res.text(), { status: res.status, headers: { "content-type": "application/json", "cache-control": "no-store" } });
  } catch {
    return Response.json({}, { status: 503 });
  }
}
