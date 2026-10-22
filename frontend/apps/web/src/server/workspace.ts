import "server-only";

import { readCookie } from "@zoiko/auth-bff";

import { server } from "@/server/bff";

/** Which company workspace this browser signs in to: its console OAuth client id, plus the company
 * name for display. Choosing a workspace grants nothing: the cell maps the client to its tenant
 * and the person must still authenticate there with their own passkey. */
export const WORKSPACE_COOKIE = "zid_workspace";
export const WORKSPACE_NAME_COOKIE = "zid_workspace_name";
const ONE_YEAR = 365 * 24 * 60 * 60;

function cookie(name: string, value: string, maxAge: number, httpOnly: boolean): string {
  const { env } = server();
  const parts = [`${name}=${value}`, "Path=/", "SameSite=Lax", `Max-Age=${maxAge}`];
  if (httpOnly) parts.push("HttpOnly");
  if (env.publicOrigin.startsWith("https://")) parts.push("Secure");
  return parts.join("; ");
}

export function workspaceCookies(clientId: string, displayName: string): string[] {
  return [
    cookie(WORKSPACE_COOKIE, clientId, ONE_YEAR, true),
    cookie(WORKSPACE_NAME_COOKIE, encodeURIComponent(displayName.slice(0, 200)), ONE_YEAR, false),
  ];
}

export function clearWorkspaceCookies(): string[] {
  return [cookie(WORKSPACE_COOKIE, "", 0, true), cookie(WORKSPACE_NAME_COOKIE, "", 0, false)];
}

export function workspaceName(cookieHeader: string | null): string | null {
  const v = readCookie(cookieHeader, WORKSPACE_NAME_COOKIE);
  if (!v) return null;
  try {
    return decodeURIComponent(v);
  } catch {
    return null;
  }
}

/** Same-origin state-changing requests only (these routes run before any session exists). */
export function sameOrigin(req: Request): boolean {
  const origin = req.headers.get("origin");
  return origin === server().env.publicOrigin.replace(/\/$/, "");
}

export function errorJson(status: number, code: string, message: string): Response {
  return new Response(JSON.stringify({ success: false, error: { code, message, details: [] } }), {
    status,
    headers: { "content-type": "application/json", "cache-control": "no-store" },
  });
}
