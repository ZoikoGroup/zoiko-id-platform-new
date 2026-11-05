/** Local development only. The cell accepts only https or loopback-IP redirects, so locally a company sign-in (Auth0) returns to
 * `http://127.0.0.1:3000/auth/callback` while the app itself runs on `http://localhost:3000` (passkeys need a name, not an IP). Cookies and the
 * same-origin check are per host, so finishing there would leave the person on a second host that half works.
 *
 * The sign-in state lives on the server (not in a cookie), so the very same callback can be completed on the app's own origin: this gives the
 * address to send the browser on to (same path and query), or null when nothing should happen. It acts only in the local environment, only for a
 * loopback-IP host, and never for the app's own host, so it can neither loop nor run anywhere else.
 *
 * The host is the one the browser asked for (the `Host` header): the dev server may describe the request URL with its own configured name. */
export function localHandback(requestUrl: string, publicOrigin: string, environment: string, hostHeader?: string | null): string | null {
  if (environment !== "local") return null;
  const url = new URL(requestUrl);
  const home = new URL(publicOrigin);
  const host = (hostHeader ?? "").trim().toLowerCase() || url.host;
  const hostname = host.startsWith("[") ? host.slice(0, host.indexOf("]") + 1) : host.split(":")[0];
  const loopbackIp = hostname === "127.0.0.1" || hostname === "[::1]";
  if (!loopbackIp || host === home.host) return null;
  return `${home.origin}${url.pathname}${url.search}`;
}
