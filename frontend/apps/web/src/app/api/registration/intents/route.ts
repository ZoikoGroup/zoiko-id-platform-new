import { post, requestCookie } from "@/server/registration";

export const dynamic = "force-dynamic";

export async function POST(req: Request) {
  return post(req, "/v1/registration/intents", ["country", "journey", "email", "locale", "notice_version", "nonce"], { onOk: requestCookie });
}
