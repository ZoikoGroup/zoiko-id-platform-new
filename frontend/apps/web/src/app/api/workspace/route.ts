import { server } from "@/server/bff";
import { clearWorkspaceCookies, errorJson, sameOrigin, workspaceCookies } from "@/server/workspace";

export const dynamic = "force-dynamic";

const NAME = /^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$/;

/** Choose the company workspace to sign in to, by its workspace name. */
export async function POST(req: Request) {
  if (!sameOrigin(req)) return errorJson(403, "CSRF_REJECTED", "The request could not be verified.");
  const body = (await req.json().catch(() => ({}))) as { workspace?: unknown; email?: unknown };
  const { env } = server();
  let name = typeof body.workspace === "string" ? body.workspace.trim().toLowerCase() : "";
  if (typeof body.email === "string" && body.email.trim()) {
    // Sign-in from an email address: the cell says which workspace that account lives in. The person still needs their own passkey there.
    let found: Response;
    try {
      found = await fetch(`${env.cellApiUrl.replace(/\/$/, "")}/v1/registration/sign-in-workspace`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ email: body.email.trim() }),
        cache: "no-store",
      });
    } catch {
      return errorJson(503, "SERVICE_UNAVAILABLE", "The service is temporarily unavailable.");
    }
    if (found.status === 429) return errorJson(429, "RATE_LIMITED", "Too many tries. Wait a few minutes and try again.");
    const hit = found.ok ? ((await found.json().catch(() => ({}))) as { workspace?: string | null }) : {};
    if (!hit.workspace) return errorJson(404, "WORKSPACE_NOT_FOUND", "We couldn't find an account for that address. Check it, or enter your workspace name instead.");
    name = hit.workspace;
  }
  if (!NAME.test(name)) return errorJson(422, "VALIDATION_FAILED", "Enter your email address or your workspace name.");
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

/** Is a workspace name free? (registration) Answers only taken/free, sets no cookie. */
export async function GET(req: Request) {
  const name = (new URL(req.url).searchParams.get("name") ?? "").trim().toLowerCase();
  if (!NAME.test(name)) return errorJson(422, "VALIDATION_FAILED", "Lower-case letters, digits and inner hyphens.");
  const { env } = server();
  let res: Response;
  try {
    res = await fetch(`${env.cellApiUrl.replace(/\/$/, "")}/v1/workspaces/${encodeURIComponent(name)}`, { cache: "no-store" });
  } catch {
    return errorJson(503, "SERVICE_UNAVAILABLE", "The service is temporarily unavailable.");
  }
  if (res.status !== 404 && !res.ok) return errorJson(res.status, "WORKSPACE_LOOKUP_FAILED", "The name could not be checked.");
  return new Response(JSON.stringify({ workspace: name, available: res.status === 404 }), {
    status: 200,
    headers: { "content-type": "application/json", "cache-control": "no-store" },
  });
}

/** Back to this app's default workspace. */
export function DELETE(req: Request) {
  if (!sameOrigin(req)) return errorJson(403, "CSRF_REJECTED", "The request could not be verified.");
  const headers = new Headers({ "content-type": "application/json", "cache-control": "no-store" });
  for (const c of clearWorkspaceCookies()) headers.append("set-cookie", c);
  return new Response(JSON.stringify({ workspace: null }), { status: 200, headers });
}
