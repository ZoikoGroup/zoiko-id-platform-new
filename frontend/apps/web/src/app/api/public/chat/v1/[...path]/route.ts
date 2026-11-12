import { createAssistantProxy, upstreamFromEnv } from "@/server/assistant-proxy";

export const dynamic = "force-dynamic";

/** Same-origin relay to the public assistant service (backend/assistant, ZI-CHAT-ADR-006). Public and unauthenticated by design: it
 * carries no Zoiko iD session, token or cookie, and the assistant holds none (INV-01). The allowlist lives in `server/assistant-proxy.ts`. */
async function relay(req: Request, ctx: { params: Promise<{ path: string[] }> }): Promise<Response> {
  const { path } = await ctx.params;
  const proxy = createAssistantProxy({
    upstream: upstreamFromEnv(process.env.ZID_ASSISTANT_API_URL),
    secureCookies: process.env.NODE_ENV === "production" ? true : undefined,
  });
  return proxy(req, path);
}

export const GET = relay;
export const POST = relay;
export const DELETE = relay;
