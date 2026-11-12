import { guideHref } from "@zoiko/console-guide";

export const dynamic = "force-dynamic";

/** Where a console-guide link goes. The guide hands the widget a link id; this fixed map turns it into one of the console's own paths.
 * Nothing a visitor or a server supplies can choose the destination (no open redirect), and the redirect is relative, so the host header
 * is never trusted. The page it lands on still requires a signed-in session and decides what to show. */
export async function GET(_req: Request, ctx: { params: Promise<{ id: string }> }): Promise<Response> {
  const { id } = await ctx.params;
  const href = guideHref(id);
  if (!href) return new Response("Not found", { status: 404, headers: { "cache-control": "no-store" } });
  return new Response(null, { status: 302, headers: { location: href, "cache-control": "no-store", "referrer-policy": "no-referrer" } });
}
