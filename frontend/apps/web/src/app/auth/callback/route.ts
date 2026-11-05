import { server } from "@/server/bff";
import { localHandback } from "@/server/local-handback";

export const dynamic = "force-dynamic";

/** OAuth redirect target for redirect-based sign-in (enterprise federation). */
export function GET(req: Request) {
  const { env, bff } = server();
  // Locally the cell sends the browser back to the 127.0.0.1 address it is registered with; finish the sign-in on the app's own origin instead.
  const home = localHandback(req.url, env.publicOrigin, env.environment, req.headers.get("host"));
  if (home) return new Response(null, { status: 307, headers: { location: home, "cache-control": "no-store" } });
  return bff.callback(req);
}
