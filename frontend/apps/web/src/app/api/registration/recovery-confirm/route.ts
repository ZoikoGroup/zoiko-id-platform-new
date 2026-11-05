import { confirmedCookies, post } from "@/server/registration";

export const dynamic = "force-dynamic";

/** The emailed recovery link: sets this browser's workspace and holds the one-passkey enrollment ticket in an HttpOnly cookie. */
export async function POST(req: Request) {
  return post(req, "/v1/registration/recovery/confirm", ["token"], { onOk: confirmedCookies });
}
