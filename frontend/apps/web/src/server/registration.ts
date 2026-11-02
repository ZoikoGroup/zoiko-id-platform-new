import "server-only";

import { server } from "@/server/bff";
import { readCookie } from "@zoiko/auth-bff";

import { errorJson, sameOrigin, workspaceCookies } from "@/server/workspace";

/** Relays the person-first sign-up calls to the cell's public /v1/registration routes (ZID-UX-WF-013A). Nothing here decides anything:
 * the cell validates every field again, derives the region, tenant and status, and answers every caller the same way. The body is never
 * logged, and only the fields each call takes are forwarded. */
async function toCell(path: string, init: RequestInit): Promise<Response | { res: Response; payload: Record<string, unknown> }> {
  const { env } = server();
  let res: Response;
  try {
    res = await fetch(`${env.cellApiUrl.replace(/\/$/, "")}${path}`, { ...init, cache: "no-store" });
  } catch {
    return errorJson(503, "SERVICE_UNAVAILABLE", "The service is temporarily unavailable.");
  }
  const payload = (await res.json().catch(() => ({}))) as Record<string, unknown>;
  return { res, payload };
}

function reply(res: Response, payload: unknown, extra?: string[]): Response {
  const headers = new Headers({ "content-type": "application/json", "cache-control": "no-store" });
  for (const c of extra ?? []) headers.append("set-cookie", c);
  return new Response(JSON.stringify(payload), { status: res.status, headers });
}

export async function get(path: string, params: Record<string, string | null>, extra: string[] = []): Promise<Response> {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v) q.set(k, v);
  const out = await toCell(`${path}${q.size ? `?${q}` : ""}`, { method: "GET", headers: { accept: "application/json" } });
  return out instanceof Response ? out : reply(out.res, out.payload, extra);
}

type Body = Record<string, unknown>;

export async function post(req: Request, path: string, fields: string[], opts: { onOk?: (payload: Record<string, unknown>) => string[] } = {}): Promise<Response> {
  if (!sameOrigin(req)) return errorJson(403, "CSRF_REJECTED", "The request could not be verified.");
  let body: Body;
  try {
    body = (await req.json()) as Body;
  } catch {
    return errorJson(400, "VALIDATION_FAILED", "The request is not valid JSON.");
  }
  const forward: Body = {};
  for (const f of fields) if (body[f] !== undefined) forward[f] = body[f];
  if (fields.includes("request_id") && !fields.includes("token") && forward.request_id === undefined) {
    const held = cookieValue(req, REQUEST_COOKIE);
    if (held) forward.request_id = held;
  }
  const headers: Record<string, string> = { "content-type": "application/json", accept: "application/json" };
  const key = req.headers.get("idempotency-key");
  if (key) headers["idempotency-key"] = key;
  const out = await toCell(path, { method: "POST", headers, body: JSON.stringify(forward) });
  if (out instanceof Response) return out;
  const cookies = out.res.ok && opts.onOk ? opts.onOk(out.payload) : [];
  return reply(out.res, out.payload, cookies);
}

/** After the link is redeemed, point this browser at the account's console client so the passkey it creates next belongs to that
 * account. Choosing a workspace grants nothing: the person must still authenticate there with their own passkey. */
export function accountCookies(payload: Record<string, unknown>): string[] {
  return typeof payload.console_client_id === "string" ? workspaceCookies(payload.console_client_id, "") : [];
}

/** The request id, the single-use enrollment ticket and the masked address are held in short-lived HttpOnly cookies set here, never in
 * web storage, a URL or a log. The page reads them on the server; the browser's scripts cannot. */
export const REQUEST_COOKIE = "zid_signup_req";
export const TICKET_COOKIE = "zid_signup_ticket";
export const ADDRESS_COOKIE = "zid_signup_addr";

function httpOnly(name: string, value: string, maxAge: number): string {
  const { env } = server();
  const parts = [`${name}=${encodeURIComponent(value)}`, "Path=/", "SameSite=Lax", `Max-Age=${maxAge}`, "HttpOnly"];
  if (env.publicOrigin.startsWith("https://")) parts.push("Secure");
  return parts.join("; ");
}

export function requestCookie(payload: Record<string, unknown>): string[] {
  return typeof payload.request_id === "string" ? [httpOnly(REQUEST_COOKIE, payload.request_id, 24 * 3600)] : [];
}

export function confirmedCookies(payload: Record<string, unknown>): string[] {
  const out = [...accountCookies(payload), ...requestCookie(payload)];
  if (typeof payload.enrollment_ticket === "string") {
    out.push(httpOnly(TICKET_COOKIE, payload.enrollment_ticket, 600));
    out.push(httpOnly(ADDRESS_COOKIE, String(payload.address ?? ""), 600));
  }
  return out;
}

export function clearTicketCookies(): string[] {
  return [httpOnly(TICKET_COOKIE, "", 0), httpOnly(ADDRESS_COOKIE, "", 0)];
}

export function cookieValue(req: Request, name: string): string | null {
  const v = readCookie(req.headers.get("cookie"), name);
  if (!v) return null;
  try {
    return decodeURIComponent(v);
  } catch {
    return null;
  }
}
