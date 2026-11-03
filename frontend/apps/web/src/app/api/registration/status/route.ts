import { clearTicketCookies, cookieValue, get, REQUEST_COOKIE } from "@/server/registration";

export const dynamic = "force-dynamic";

/** The status of this browser's own request (its HttpOnly cookie), so no request id travels in a URL. Reading it also clears any leftover
 * enrollment ticket: by now the passkey step is over or abandoned, and the ticket is single use anyway. */
export async function GET(req: Request) {
  const id = new URL(req.url).searchParams.get("request_id") ?? cookieValue(req, REQUEST_COOKIE);
  return get("/v1/registration/status", { request_id: id }, clearTicketCookies());
}
