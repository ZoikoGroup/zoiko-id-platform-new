import { server } from "@/server/bff";

export const dynamic = "force-dynamic";

/** OAuth redirect target for redirect-based sign-in (enterprise federation). */
export function GET(req: Request) {
  return server().bff.callback(req);
}
