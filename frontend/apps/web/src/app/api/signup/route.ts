import { server } from "@/server/bff";
import { errorJson, sameOrigin, workspaceCookies } from "@/server/workspace";

export const dynamic = "force-dynamic";

/** Company sign-up: relays to the cell's public POST /v1/signup, then points this browser at the new
 * company's workspace so the passkey it creates next belongs to that tenant. The enrollment ticket
 * goes back to this page once and is not stored here. */
export async function POST(req: Request) {
  if (!sameOrigin(req)) return errorJson(403, "CSRF_REJECTED", "The request could not be verified.");
  let body: { company_name?: unknown; workspace?: unknown };
  try {
    body = (await req.json()) as typeof body;
  } catch {
    return errorJson(400, "VALIDATION_FAILED", "The request is not valid JSON.");
  }
  const { env } = server();
  let res: Response;
  try {
    res = await fetch(`${env.cellApiUrl.replace(/\/$/, "")}/v1/signup`, {
      method: "POST",
      headers: { "content-type": "application/json", accept: "application/json" },
      body: JSON.stringify({ company_name: body.company_name, workspace: body.workspace }),
      cache: "no-store",
    });
  } catch {
    return errorJson(503, "SERVICE_UNAVAILABLE", "The service is temporarily unavailable.");
  }
  const payload = (await res.json().catch(() => ({}))) as Record<string, unknown>;
  const headers = new Headers({ "content-type": "application/json", "cache-control": "no-store" });
  if (res.status === 201 && typeof payload.console_client_id === "string") {
    for (const c of workspaceCookies(payload.console_client_id, String(payload.display_name ?? ""))) headers.append("set-cookie", c);
  }
  return new Response(JSON.stringify(payload), { status: res.status, headers });
}
