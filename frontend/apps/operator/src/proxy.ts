import { buildCsp, createNonce } from "@zoiko/auth-bff/csp";
import { NextResponse, type NextRequest } from "next/server";

/** Per-request nonce-based Content-Security-Policy. No authorization logic lives here: the cell
 * authorizes every call; pages only decide what to render. */
export function proxy(request: NextRequest) {
  const nonce = createNonce();
  const csp = buildCsp(nonce, { dev: process.env.NODE_ENV !== "production" });
  const headers = new Headers(request.headers);
  headers.set("x-nonce", nonce);
  headers.set("x-zid-path", request.nextUrl.pathname + request.nextUrl.search);
  headers.set("content-security-policy", csp);
  const response = NextResponse.next({ request: { headers } });
  response.headers.set("content-security-policy", csp);
  return response;
}

export const config = {
  matcher: [
    {
      source: "/((?!api/|authn/|oauth2/|federation/|auth/|\\.well-known/|_next/static|_next/image|favicon.ico).*)",
      missing: [{ type: "header", key: "next-router-prefetch" }],
    },
  ],
};
