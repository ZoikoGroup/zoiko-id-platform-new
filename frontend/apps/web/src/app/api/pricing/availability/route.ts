import { server } from "@/server/bff";
import { errorJson } from "@/server/workspace";

export const dynamic = "force-dynamic";

const COUNTRY = /^[A-Z]{2}$/;
const REGION = /^[A-Za-z0-9._-]{2,40}$/;

/** Public availability by market: relays to the cell's GET /v1/public/pricing/availability. No session and no tenant data are involved, nothing is cached, and no price is
 * ever in the answer. Only the four known inputs are forwarded and each is checked here and again by the cell. */
export async function GET(req: Request) {
  const url = new URL(req.url);
  const q = new URLSearchParams();
  for (const key of ["subject_country", "payer_country", "issuer_country"] as const) {
    const v = url.searchParams.get(key);
    if (v) {
      if (!COUNTRY.test(v)) return errorJson(422, "VALIDATION_FAILED", "A country is two capital letters.");
      q.set(key, v);
    }
  }
  const region = url.searchParams.get("operating_region");
  if (region) {
    if (!REGION.test(region)) return errorJson(422, "VALIDATION_FAILED", "The data region is not valid.");
    q.set("operating_region", region);
  }
  const { env } = server();
  let res: Response;
  try {
    res = await fetch(`${env.cellApiUrl.replace(/\/$/, "")}/v1/public/pricing/availability?${q.toString()}`, {
      headers: { accept: "application/json" },
      cache: "no-store",
    });
  } catch {
    return errorJson(503, "SERVICE_UNAVAILABLE", "Availability could not be checked right now.");
  }
  return new Response(await res.text(), { status: res.status, headers: { "content-type": "application/json", "cache-control": "no-store" } });
}
