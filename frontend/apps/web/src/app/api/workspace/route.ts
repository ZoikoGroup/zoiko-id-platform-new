import { server } from "@/server/bff";
import { clearWorkspaceCookies, errorJson, sameOrigin, workspaceCookies } from "@/server/workspace";

export const dynamic = "force-dynamic";

const NAME = /^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$/;

/** Choose the company workspace to sign in to, by its workspace name. */
export async function POST(req: Request) {
  if (!sameOrigin(req)) return errorJson(403, "CSRF_REJECTED", "The request could not be verified.");
  const body = (await req.json().catch(() => ({}))) as { workspace?: unknown };
  const name = typeof body.workspace === "string" ? body.workspace.trim().toLowerCase() : "";
  if (!NAME.test(name)) return errorJson(422, "VALIDATION_FAILED", "Enter your company's workspace name.");
  const { env } = server();
  let res: Response;
  try {
    res = await fetch(`${env.cellApiUrl.replace(/\/$/, "")}/v1/workspaces/${encodeURIComponent(name)}`, { cache: "no-store" });
  } catch {
    return errorJson(503, "SERVICE_UNAVAILABLE", "The service is temporarily unavailable.");
  }
  if (res.status === 404) return errorJson(404, "WORKSPACE_NOT_FOUND", "No company uses that workspace name.");
  if (!res.ok) return errorJson(res.status, "WORKSPACE_LOOKUP_FAILED", "The workspace could not be looked up.");
  const w = (await res.json()) as { workspace: string; display_name: string; console_client_id: string };
  const headers = new Headers({ "content-type": "application/json", "cache-control": "no-store" });
  for (const c of workspaceCookies(w.console_client_id, w.display_name)) headers.append("set-cookie", c);
  return new Response(JSON.stringify({ workspace: w.workspace, display_name: w.display_name }), { status: 200, headers });
}

/** Back to this app's default workspace. */
export function DELETE(req: Request) {
  if (!sameOrigin(req)) return errorJson(403, "CSRF_REJECTED", "The request could not be verified.");
  const headers = new Headers({ "content-type": "application/json", "cache-control": "no-store" });
  for (const c of clearWorkspaceCookies()) headers.append("set-cookie", c);
  return new Response(JSON.stringify({ workspace: null }), { status: 200, headers });
}
