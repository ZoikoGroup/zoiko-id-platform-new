import { relay } from "@/server/applicant-relay";
import { server } from "@/server/bff";

export const dynamic = "force-dynamic";

/** The hosted verification's relay to the cell. Public and session-less by design; the allowlist lives in `server/applicant-relay.ts`. */
async function handle(req: Request, ctx: { params: Promise<{ path: string[] }> }): Promise<Response> {
  const { path } = await ctx.params;
  return relay(req, path.join("/"), server().env.cellApiUrl);
}

export const GET = handle;
export const POST = handle;
