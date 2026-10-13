/** Session cookie: `__Host-` prefix (Secure, Path=/, no Domain), httpOnly, SameSite=Lax so the OIDC
 * redirect back from the cell carries it; CSRF is handled separately by the `x-zid-csrf` header. */

export type CookieOptions = { name: string; secure: boolean };

export function sessionCookieName(app: string, secure: boolean): string {
  return secure ? `__Host-zid_${app}` : `zid_${app}`;
}

export function serializeCookie(name: string, value: string, maxAgeSeconds: number, secure: boolean): string {
  const parts = [`${name}=${value}`, "Path=/", "HttpOnly", "SameSite=Lax", `Max-Age=${maxAgeSeconds}`];
  if (secure) parts.push("Secure");
  return parts.join("; ");
}

export function clearCookie(name: string, secure: boolean): string {
  return serializeCookie(name, "", 0, secure);
}

export function readCookie(header: string | null, name: string): string | null {
  if (!header) return null;
  for (const part of header.split(";")) {
    const i = part.indexOf("=");
    if (i < 0) continue;
    if (part.slice(0, i).trim() === name) return part.slice(i + 1).trim() || null;
  }
  return null;
}
