import { server } from "@/server/bff";

type Ctx = { params: Promise<{ path: string[] }> };

/** Same-origin relay of the cell's browser-facing /federation flows (see server/bff.ts allowlist). */
async function handle(req: Request, ctx: Ctx) {
  const { path } = await ctx.params;
  const encoded = path.map((p) => encodeURIComponent(p).replace(/%3A/gi, ":")).join("/");
  return server().bff.passthrough(req, `/federation/${encoded}`);
}

export const dynamic = "force-dynamic";
export { handle as GET, handle as POST };
