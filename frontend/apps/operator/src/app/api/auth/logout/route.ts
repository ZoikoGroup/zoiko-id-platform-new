import { server } from "@/server/bff";

export const dynamic = "force-dynamic";

export function POST(req: Request) {
  return server().bff.logout(req);
}
