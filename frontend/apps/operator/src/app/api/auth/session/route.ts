import { server } from "@/server/bff";

export const dynamic = "force-dynamic";

/** Current display identity + CSRF token (never tokens). */
export function GET(req: Request) {
  return server().bff.session(req);
}

/** After passkey sign-in or step-up: exchange the cell session for fresh tokens, server-side. */
export function POST(req: Request) {
  return server().bff.reauthorize(req);
}
