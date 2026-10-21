import { server } from "@/server/bff";

export const dynamic = "force-dynamic";

export function GET(req: Request) {
  return server().bff.passthrough(req, "/.well-known/webauthn");
}
