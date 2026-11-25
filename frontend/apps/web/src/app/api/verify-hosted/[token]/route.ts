import { validToken } from "@/features/verify-hosted/logic";
import { server } from "@/server/bff";
import { errorJson, sameOrigin } from "@/server/workspace";

export const dynamic = "force-dynamic";

const OUTCOME = /^[a-z]{4,20}$/;

/** The hosted verification page's relay to the cell (CR-151). The person holds only the link, so this passes only the link and the one thing they choose; a malformed link is refused here
 * without reaching the cell, and the cell's own single answer for every wrong link is passed back unchanged. */
async function relay(token: string, suffix: string, method: "GET" | "POST", body?: unknown): Promise<Response> {
  const { env } = server();
  try {
    const res = await fetch(`${env.cellApiUrl.replace(/\/$/, "")}/v1/verify-api/hosted/${token}${suffix}`, {
      method,
      headers: { accept: "application/json", ...(body ? { "content-type": "application/json" } : {}) },
      body: body ? JSON.stringify(body) : undefined,
      cache: "no-store",
    });
    return new Response(await res.text(), { status: res.status, headers: { "content-type": "application/json", "cache-control": "no-store" } });
  } catch {
    return errorJson(503, "SERVICE_UNAVAILABLE", "This could not be done right now. Please try again shortly.");
  }
}

export async function GET(_req: Request, ctx: { params: Promise<{ token: string }> }) {
  const { token } = await ctx.params;
  if (!validToken(token)) return errorJson(404, "LAUNCH_INVALID", "This link cannot be used.");
  return relay(token, "", "GET");
}

/** `{ "action": "open" }` or `{ "action": "simulate", "outcome": "approved" }`. */
export async function POST(req: Request, ctx: { params: Promise<{ token: string }> }) {
  if (!sameOrigin(req)) return errorJson(403, "CSRF_REJECTED", "The request could not be verified.");
  const { token } = await ctx.params;
  if (!validToken(token)) return errorJson(404, "LAUNCH_INVALID", "This link cannot be used.");
  let body: { action?: string; outcome?: string; consented?: unknown; notice_version?: unknown; nonce?: unknown; captured_at?: unknown; image_base64?: unknown; selfie_base64?: unknown };
  try {
    body = (await req.json()) as typeof body;
  } catch {
    return errorJson(400, "VALIDATION_FAILED", "The request is not valid JSON.");
  }
  if (body.action === "open") return relay(token, ":open", "POST", {});
  // the live check (production sessions): the notice answer, a one-time value, and the two photographs. Each is passed on with only the fields it needs.
  if (body.action === "consent" && typeof body.consented === "boolean" && typeof body.notice_version === "string" && body.notice_version.length <= 80) {
    return relay(token, "/consent", "POST", { consented: body.consented, notice_version: body.notice_version });
  }
  if (body.action === "nonce") return relay(token, "/nonce", "POST", {});
  if (
    body.action === "capture" &&
    typeof body.nonce === "string" &&
    body.nonce.length <= 128 &&
    typeof body.captured_at === "string" &&
    body.captured_at.length <= 40 &&
    typeof body.image_base64 === "string" &&
    typeof body.selfie_base64 === "string"
  ) {
    return relay(token, "/capture", "POST", { nonce: body.nonce, captured_at: body.captured_at, image_base64: body.image_base64, selfie_base64: body.selfie_base64 });
  }
  if (body.action === "simulate" && typeof body.outcome === "string" && OUTCOME.test(body.outcome)) {
    return relay(token, ":simulate", "POST", { outcome: body.outcome });
  }
  return errorJson(422, "VALIDATION_FAILED", "That is not something this page can do.");
}
