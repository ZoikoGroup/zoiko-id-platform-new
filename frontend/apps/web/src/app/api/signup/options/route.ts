import { server } from "@/server/bff";
import { errorJson } from "@/server/workspace";

export const dynamic = "force-dynamic";

/** Public, read-only relay to the cell (no session, no token): the privacy-notice version, data locations and registers sign-up offers. */
export async function GET() {
  const { env } = server();
  let res: Response;
  try {
    res = await fetch(`${env.cellApiUrl.replace(/\/$/, "")}/v1/signup/options`, { headers: { accept: "application/json" }, cache: "no-store" });
  } catch {
    return errorJson(503, "SERVICE_UNAVAILABLE", "The service is temporarily unavailable.");
  }
  const payload = await res.text();
  return new Response(payload, { status: res.status, headers: { "content-type": "application/json", "cache-control": "no-store" } });
}
