import { server } from "@/server/bff";
import { errorJson, sameOrigin } from "@/server/workspace";

export const dynamic = "force-dynamic";

const FIELDS = [
  "kind",
  "contact_name",
  "work_email",
  "company",
  "country",
  "region",
  "capabilities",
  "volume_band",
  "entities",
  "referral_source",
  "security_needs",
  "partner_type",
  "notice_version",
  "website",
] as const;

/** Public sales inquiry: relays to the cell's POST /v1/public/pricing/leads. The origin is checked, only the known fields are forwarded (the cell refuses any other), and the
 * caller's address is not passed on beyond what the proxy connection shows. An inquiry creates no account, quote, contract or payment. */
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
    res = await fetch(`${env.cellApiUrl.replace(/\/$/, "")}/v1/public/pricing/leads`, {
      method: "POST",
      headers: { "content-type": "application/json", accept: "application/json" },
      body: JSON.stringify(out),
      cache: "no-store",
    });
  } catch {
    return errorJson(503, "SERVICE_UNAVAILABLE", "Your inquiry could not be sent right now. Please try again shortly.");
  }
  return new Response(await res.text(), { status: res.status, headers: { "content-type": "application/json", "cache-control": "no-store" } });
}
