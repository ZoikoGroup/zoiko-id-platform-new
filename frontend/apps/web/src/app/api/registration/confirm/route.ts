import { confirmedCookies, post } from "@/server/registration";

export const dynamic = "force-dynamic";

export async function POST(req: Request) {
  return post(req, "/v1/registration/confirm", ["token", "request_id"], { onOk: confirmedCookies });
}
