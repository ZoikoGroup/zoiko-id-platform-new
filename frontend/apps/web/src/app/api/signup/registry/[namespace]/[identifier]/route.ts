import { server } from "@/server/bff";
import { errorJson } from "@/server/workspace";

export const dynamic = "force-dynamic";

const NS = /^(GB-COH)$/;
const ID = /^[A-Za-z0-9]{6,20}$/;

/** Public register lookup before registering: the cell answers with the legal name and status only
 * (never officers) and rate-limits it. Nothing is stored. */
export async function GET(_req: Request, ctx: { params: Promise<{ namespace: string; identifier: string }> }) {
  const { namespace, identifier } = await ctx.params;
  if (!NS.test(namespace) || !ID.test(identifier)) return errorJson(422, "VALIDATION_FAILED", "Enter a valid company number.");
  const { env } = server();
  let res: Response;
  try {
    res = await fetch(
      `${env.cellApiUrl.replace(/\/$/, "")}/v1/signup/registry/${encodeURIComponent(namespace)}/${encodeURIComponent(identifier.toUpperCase())}`,
      { headers: { accept: "application/json" }, cache: "no-store" },
    );
  } catch {
    return errorJson(503, "SERVICE_UNAVAILABLE", "The service is temporarily unavailable.");
  }
  return new Response(await res.text(), { status: res.status, headers: { "content-type": "application/json", "cache-control": "no-store" } });
}
