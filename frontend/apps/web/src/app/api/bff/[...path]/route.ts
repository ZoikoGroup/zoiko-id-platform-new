import { server } from "@/server/bff";

type Ctx = { params: Promise<{ path: string[] }> };

async function handle(req: Request, ctx: Ctx) {
  return server().bff.proxy(req, (await ctx.params).path);
}

export const dynamic = "force-dynamic";
export { handle as GET, handle as POST, handle as PUT, handle as PATCH, handle as DELETE };
