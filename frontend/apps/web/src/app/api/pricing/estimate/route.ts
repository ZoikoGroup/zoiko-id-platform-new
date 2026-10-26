import { server } from "@/server/bff";
import { errorJson, sameOrigin } from "@/server/workspace";

export const dynamic = "force-dynamic";

const FIELDS = ["mah", "auth_events", "policy_decisions", "m2m_grants", "sso_connections", "scim_connections"] as const;

/** Public pricing calculator: relays to the cell's POST /v1/billing/estimate, which runs the same rating
 * function as an invoice over the approved price book. No session and no tenant data are involved, and the
 * answer is an estimate, never a quotation. Only the known fields are forwarded; the cell validates them again. */
export async function POST(req: Request) {
  if (!sameOrigin(req)) return errorJson(403, "CSRF_REJECTED", "The request could not be verified.");
  let body: Record<string, unknown>;
  try {
    body = (await req.json()) as Record<string, unknown>;
  } catch {
    return errorJson(400, "VALIDATION_FAILED", "The request is not valid JSON.");
  }
  const out: Record<string, unknown> = { plan: body.plan };
  for (const f of FIELDS) out[f] = body[f] ?? 0;
  const { env } = server();
  let res: Response;
  try {
    res = await fetch(`${env.cellApiUrl.replace(/\/$/, "")}/v1/billing/estimate`, {
      method: "POST",
      headers: { "content-type": "application/json", accept: "application/json" },
      body: JSON.stringify(out),
      cache: "no-store",
    });
  } catch {
    return errorJson(503, "SERVICE_UNAVAILABLE", "The service is temporarily unavailable.");
  }
  return new Response(await res.text(), {
    status: res.status,
    headers: { "content-type": "application/json", "cache-control": "no-store" },
  });
}
