import { safeReturnTo } from "@zoiko/auth-bff";

import { server } from "@/server/bff";

export const dynamic = "force-dynamic";

/** Starts enterprise sign-in: the cell sends the browser to the IdP, then back through
 * /oauth2/authorize (same origin) to /auth/callback. */
export async function GET(req: Request, ctx: { params: Promise<{ connectionId: string }> }) {
  const { bff, env } = server();
  const { connectionId } = await ctx.params;
  if (!env.federationConnections.some((c) => c.id === connectionId)) {
    return new Response(null, { status: 303, headers: { location: "/sign-in?error=unknown_connection" } });
  }
  const returnTo = safeReturnTo(new URL(req.url).searchParams.get("returnTo"), "/account");
  const clientId = bff.clientFor(req.headers.get("cookie"));
  const authorize = await bff.authorizePath(returnTo, clientId);
  const q = new URLSearchParams({ client_id: clientId, return_to: authorize });
  return new Response(null, {
    status: 303,
    headers: { location: `/federation/connections/${encodeURIComponent(connectionId)}/login?${q.toString()}`, "cache-control": "no-store" },
  });
}
