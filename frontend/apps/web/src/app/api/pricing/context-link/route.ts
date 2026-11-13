import { server } from "@/server/bff";
import { errorJson, sameOrigin } from "@/server/workspace";

export const dynamic = "force-dynamic";

const FIELDS = ["subject_country", "payer_country", "issuer_country", "operating_region"] as const;

/** A signed link to the market a buyer chose: relays to the cell's POST /v1/public/pricing/context-link. The link holds only the market, expires, and is judged afresh when opened. */
export async function POST(req: Request) {
  if (!sameOrigin(req)) return errorJson(403, "CSRF_REJECTED", "The request could not be verified.");
  let body: Record<string, unknown>;
  try {
    body = (await req.json()) as Record<string, unknown>;
  } catch {
    return errorJson(400, "VALIDATION_FAILED", "The request is not valid JSON.");
  }
  const out: Record<string, unknown> = {};
  for (const f of FIELDS) if (body[f] !== undefined && body[f] !== null && body[f] !== "") out[f] = body[f];
  const { env } = server();
  let res: Response;
  try {
    res = await fetch(`${env.cellApiUrl.replace(/\/$/, "")}/v1/public/pricing/context-link`, {
      method: "POST",
      headers: { "content-type": "application/json", accept: "application/json" },
      body: JSON.stringify(out),
      cache: "no-store",
    });
  } catch {
    return errorJson(503, "SERVICE_UNAVAILABLE", "A link could not be made right now.");
  }
  return new Response(await res.text(), { status: res.status, headers: { "content-type": "application/json", "cache-control": "no-store" } });
}
