import { server } from "@/server/bff";
import { errorJson, sameOrigin } from "@/server/workspace";

export const dynamic = "force-dynamic";

const FIELDS = ["subject_country", "payer_country", "issuer_country", "operating_region", "period", "volumes"] as const;

/** Public family estimate: relays to the cell's POST /v1/public/pricing/estimate. The origin is checked and only the known fields are forwarded; the cell prices from the active rate card
 * only and validates everything again. The answer is an estimate, never a quote, an invoice or a charge. */
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
    res = await fetch(`${env.cellApiUrl.replace(/\/$/, "")}/v1/public/pricing/estimate`, {
      method: "POST",
      headers: { "content-type": "application/json", accept: "application/json" },
      body: JSON.stringify(out),
      cache: "no-store",
    });
  } catch {
    return errorJson(503, "SERVICE_UNAVAILABLE", "The estimate is temporarily unavailable.");
  }
  return new Response(await res.text(), { status: res.status, headers: { "content-type": "application/json", "cache-control": "no-store" } });
}
